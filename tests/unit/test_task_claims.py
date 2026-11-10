import json
from datetime import UTC, datetime, timedelta

import pytest
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def test_claims_are_distinct_and_failed_dependency_is_blocked(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "claims.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    first = TaskStore(runs)
    first.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(id="a", objective="A", max_iterations=1, max_seconds=10),
            TaskDefinition(id="b", objective="B", max_iterations=1, max_seconds=10),
            TaskDefinition(
                id="c", objective="C", dependencies=["a"], max_iterations=1, max_seconds=10
            ),
            TaskDefinition(
                id="d", objective="D", dependencies=["c"], max_iterations=1, max_seconds=10
            ),
        ],
    )
    second = TaskStore(RunStore(runs.path))
    a = first.claim_ready(run_id, parent)
    b = second.claim_ready(run_id, parent)
    assert {a["task"]["local_id"], b["task"]["local_id"]} == {"a", "b"}
    with pytest.raises(ValueError, match="candidate"):
        first.record_outcome(run_id, parent, "a", a["token"], state="completed", result={})
    with pytest.raises(PermissionError):
        first.record_outcome(run_id, parent, "a", b["token"], state="failed", result={})
    first.record_outcome(
        run_id, parent, "a", a["token"], state="failed", result={"error": "fixture failure"}
    )
    assert second.claim_ready(run_id, parent) is None
    rows = {row["local_id"]: row for row in second.records(run_id)}
    assert rows["c"]["status"] == rows["d"]["status"] == "blocked"
    assert rows["c"]["blocked_reason"] == rows["d"]["blocked_reason"] == "dependency_failed"
    blocked_events = [
        event
        for event in runs.list_events(run_id)
        if event["event_type"] == "task.blocked" and event["payload"].get("task_id") in {"c", "d"}
    ]
    assert {event["payload"]["task_id"] for event in blocked_events} == {"c", "d"}
    assert len(blocked_events) == 2
    assert second.claim_ready(run_id, parent) is None
    repeated_blocked_events = [
        event
        for event in runs.list_events(run_id)
        if event["event_type"] == "task.blocked" and event["payload"].get("task_id") in {"c", "d"}
    ]
    assert repeated_blocked_events == blocked_events
    second.renew_worker(run_id, parent, "b", b["token"])
    assert second.recover_expired(run_id, parent) == []
    with runs.connection() as connection:
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    with pytest.raises(PermissionError):
        second.renew_worker(run_id, parent, "b", b["token"])
    assert second.recover_expired(run_id, parent) == ["b"]
    recovered = next(row for row in second.records(run_id) if row["local_id"] == "b")
    assert recovered["status"] == "blocked"
    assert recovered["blocked_reason"] == "worker_lost_reconciliation_required"
    with pytest.raises(PermissionError):
        second.record_outcome(run_id, parent, "b", b["token"], state="verifying", result={})
    assert second.claim_ready(run_id, parent) is None
    assert second.recover_expired(run_id, parent) == []


def test_token_reservation_is_bound_to_its_started_specialist(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "task-reservation-identity.sqlite3")
    run_id = runs.create_run(
        objective="Bind reservations to specialists",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=4,
        max_total_tokens=100,
        pre_inference_token_reservations=True,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(id="a", objective="A", max_iterations=1, max_seconds=10),
            TaskDefinition(id="b", objective="B", max_iterations=1, max_seconds=10),
        ],
    )
    a = tasks.claim_ready(run_id, parent)
    b = tasks.claim_ready(run_id, parent)
    assert a is not None and b is not None
    inference_id = tasks.reserve_inference(run_id, parent, "a", a["token"])

    with pytest.raises(ValueError, match="requires its task identity"):
        runs.reserve_tokens(run_id, parent, inference_id, 20, 20)
    with pytest.raises(ValueError, match="must match its started task inference"):
        tasks.reserve_tokens(
            run_id,
            parent,
            inference_id,
            20,
            20,
            task_id="b",
            task_token=b["token"],
        )

    tasks.reserve_tokens(
        run_id,
        parent,
        inference_id,
        20,
        20,
        task_id="a",
        task_token=a["token"],
    )
    report = runs.usage_report(run_id)
    assert report["outstanding_reservations"] == 1
    assert report["outstanding_reserved_tokens"] == 40
    hold = next(
        event for event in runs.list_events(run_id) if event["event_type"] == "tokens.reserved"
    )
    assert hold["payload"]["task_id"] == "a"


def test_lost_worker_journal_allows_only_safe_saved_result_resume(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="read", objective="Read evidence", max_seconds=120)],
    )
    claim = tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {
                "task_id": "read",
                "inference_id": "inference-1",
            },
        )
        runs.record_event(
            connection,
            run_id,
            "tokens.reserved",
            {
                "task_id": "read",
                "inference_id": "inference-1",
                "reserved_tokens": 9,
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "inference-1",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        },
                    ],
                },
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.tool.result",
            {
                "task_id": "read",
                "call_id": "call-1",
                "journal_id": "read-1",
                "result": {"text": "actual", "sha256": "abc"},
            },
        )
        connection.execute(
            "UPDATE run_events SET created_at=? WHERE run_id=? AND event_type='task.claimed'",
            ((datetime.now(UTC) - timedelta(seconds=10)).isoformat(), run_id),
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["safe_to_resume"] is True
    assert summary["saved_tool_results"] == 1
    assert summary["pending_model_tool_calls"] == []
    assert summary["reserved_tokens"] == 0
    tasks.resolve_lost_worker(run_id, parent, "read", "resume")
    recovered = tasks.claim_ready(run_id, parent)
    assert recovered is not None
    assert recovered["task"]["status"] == "running"
    assert recovered["task"]["local_id"] == "read"
    assert 100 < recovered["task"]["budgets"]["max_seconds"] <= 110
    assert recovered["token"] != claim["token"]


def test_lost_worker_with_unanswered_token_reservation_is_not_safe_to_resume(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "reservation-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="read", objective="Read evidence")])
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "read", "inference_id": "inference-pending"},
        )
        runs.record_event(
            connection,
            run_id,
            "tokens.reserved",
            {
                "task_id": "read",
                "inference_id": "inference-pending",
                "reserved_tokens": 11,
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "prior-empty-message",
                "message": {"role": "assistant", "content": ""},
                "usage": None,
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["reserved_tokens"] == 11
    assert summary["safe_to_resume"] is False
    with pytest.raises(ValueError, match="Review the saved candidate"):
        tasks.resolve_lost_worker(run_id, parent, "read", "resume")


@pytest.mark.parametrize("result_saved", [False, True])
def test_lost_specialist_mcp_read_is_sealed_or_attached_without_replay(
    workspace_tmp_path, result_saved
):
    import hashlib

    from xaa.storage.mcp import MCPDiscoveredTool, MCPRegistry, MCPServerSpec
    from xaa.storage.runs import TaskReadScope

    store = RunStore(workspace_tmp_path / f"mcp-task-recovery-{result_saved}.sqlite3")
    registry = MCPRegistry(store)
    server = registry.create_server(MCPServerSpec(name="Fixture", command="fixture"))
    read_tool = registry.synchronize_tools(
        server.id,
        [
            MCPDiscoveredTool(
                name="lookup",
                input_schema={"type": "object", "properties": {}},
                schema_sha256="a" * 64,
            )
        ],
    )[0]
    read_tool = registry.set_permission(server.id, read_tool.name, "read", read_tool.schema_sha256)
    run_id = store.create_run(
        objective="Read one approved MCP result",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="read",
                objective="Read from the configured MCP server",
                permissions=("workspace.read", read_tool.qualified_name),
                max_iterations=3,
            )
        ],
    )
    claim = tasks.claim_ready(run_id, parent)
    assert claim is not None
    model_call_id = "mcp-model-call"
    tasks.record_read_worker_event(
        run_id,
        parent,
        "read",
        claim["token"],
        "task.model.message",
        {
            "task_id": "read",
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": model_call_id,
                        "type": "function",
                        "function": {"name": read_tool.qualified_name, "arguments": "{}"},
                    }
                ],
            },
        },
    )
    journal_call_id = (
        "taskfs-" + hashlib.sha256(f"{run_id}:read:{model_call_id}".encode()).hexdigest()
    )
    scoped = RunStore(
        store.path,
        task_scope=TaskReadScope(run_id, parent, "read", claim["token"]),
        internal_tools=True,
    )
    assert scoped.record_tool_intent(
        run_id,
        parent,
        call_id=journal_call_id,
        tool="mcp.call",
        version="1",
        arguments={
            "tool": read_tool.qualified_name,
            "arguments": {},
            "permission": "read",
            "schema_sha256": read_tool.schema_sha256,
        },
        idempotency_key=journal_call_id,
        internal=True,
    )
    expected_result = {"status": "ok", "trust": "untrusted_mcp_output", "content": []}
    if result_saved:
        scoped.record_tool_result(run_id, parent, journal_call_id, expected_result, internal=True)

    with store.connection() as connection:
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    action = next(
        item
        for item in summary["recoverable_task_actions"]
        if item["model_call_id"] == model_call_id
    )
    assert action["tool_name"] == "mcp.call"
    assert action["mcp_tool"] == read_tool.qualified_name
    assert action["state"] == ("completed" if result_saved else "intended")
    from xaa.api.contracts import TaskRecoverySummary

    TaskRecoverySummary.model_validate(summary)

    tasks.record_recovered_task_action_result(
        run_id, parent, "read", model_call_id, journal_call_id
    )
    attached = next(
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "task.tool.result"
        and event["payload"].get("call_id") == model_call_id
    )
    if result_saved:
        assert attached["payload"]["result"] == expected_result
    else:
        assert attached["payload"]["result"]["error"]["code"] == "interrupted_read"
        assert (
            "no previous contents are assumed" in attached["payload"]["result"]["error"]["message"]
        )
        assert not store.unresolved_tool_calls(run_id)


def test_lost_specialist_browser_screenshot_result_recovers_with_artifact(workspace_tmp_path):
    import hashlib

    from xaa.storage.runs import TaskReadScope

    store = RunStore(workspace_tmp_path / "browser-screenshot-recovery.sqlite3")
    run_id = store.create_run(
        objective="Capture an approved page",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        approved_browser=True,
        approved_web_hosts=["example.com"],
    )["id"]
    parent = store.acquire_lease(run_id, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="research",
                objective="Capture the approved page",
                permissions=("workspace.read", "web.browser.read"),
            )
        ],
    )
    claim = tasks.claim_ready(run_id, parent)
    assert claim is not None
    model_call_id = "browser-screenshot-call"
    tasks.record_read_worker_event(
        run_id,
        parent,
        "research",
        claim["token"],
        "task.model.message",
        {
            "task_id": "research",
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": model_call_id,
                        "type": "function",
                        "function": {
                            "name": "browser_run",
                            "arguments": '{"url":"https://example.com/","screenshot":true}',
                        },
                    }
                ],
            },
        },
    )
    journal_call_id = (
        "taskfs-" + hashlib.sha256(f"{run_id}:research:{model_call_id}".encode()).hexdigest()
    )
    scoped = RunStore(
        store.path,
        task_scope=TaskReadScope(run_id, parent, "research", claim["token"]),
        internal_tools=True,
    )
    assert scoped.record_tool_intent(
        run_id,
        parent,
        call_id=journal_call_id,
        tool="browser.run",
        version="1",
        arguments={"url": "https://example.com/", "screenshot": True},
        idempotency_key=journal_call_id,
        internal=True,
    )
    artifact_root = store.path.parent / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    screenshot_bytes = b"png"
    filename = "specialist-screenshot.png"
    (artifact_root / filename).write_bytes(screenshot_bytes)
    artifact = scoped.register_artifact(
        run_id,
        parent,
        filename,
        provenance="browser.screenshot:untrusted_web_content",
        media_type="image/png",
    )
    expected_result = {
        "status": "ok",
        "trust": "untrusted_web_content",
        "screenshot_artifact": artifact,
    }
    scoped.record_tool_result(run_id, parent, journal_call_id, expected_result, internal=True)

    with store.connection() as connection:
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["research"]
    summary = tasks.recovery_summary(run_id, "research")
    action = next(
        item
        for item in summary["recoverable_task_actions"]
        if item["model_call_id"] == model_call_id
    )
    assert action["tool_name"] == "browser.run"
    assert action["state"] == "completed"
    tasks.record_recovered_task_action_result(
        run_id, parent, "research", model_call_id, journal_call_id
    )
    attached = next(
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "task.tool.result"
        and event["payload"].get("call_id") == model_call_id
    )
    assert attached["payload"]["result"]["screenshot_artifact"] == artifact


def test_lost_worker_cannot_resume_after_specialist_time_budget_is_exhausted(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "task-time-budget.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=8,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="read", objective="Read evidence", max_seconds=1)],
    )
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "prior-empty-message",
                "message": {"role": "assistant", "content": ""},
                "usage": None,
            },
        )
        connection.execute(
            "UPDATE run_events SET created_at=? WHERE run_id=? AND event_type='task.claimed'",
            ((datetime.now(UTC) - timedelta(seconds=10)).isoformat(), run_id),
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    assert tasks.recovery_summary(run_id, "read")["safe_to_resume"] is False


def test_lost_worker_cannot_resume_when_shared_run_budget_is_exhausted(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "exhausted-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=1,
        max_total_tokens=1,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="read", objective="Read evidence", max_iterations=1)],
    )
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "read", "inference_id": "inference-complete"},
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "inference-complete",
                "message": {"role": "assistant", "content": ""},
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["safe_to_resume"] is False
    with pytest.raises(ValueError, match="Review the saved candidate"):
        tasks.resolve_lost_worker(run_id, parent, "read", "resume")


def test_lost_worker_cannot_restart_unmeasured_inference_with_shared_token_budget(
    workspace_tmp_path,
):
    runs = RunStore(workspace_tmp_path / "unmeasured-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=8,
        max_total_tokens=100,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="read", objective="Read evidence")])
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "read", "inference_id": "unmeasured-attempt"},
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["safe_to_resume"] is False
    assert summary["resume_from_start"] is False


def test_clean_lost_worker_can_be_requeued_from_assignment(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "clean-restart-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    runs.transition(run_id, RunState.PLANNING, owner=parent)
    tasks = TaskStore(runs)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="read", objective="Read evidence")])
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run_id,)
        )

    reopened = RunStore(runs.path)
    assert reopened.recover_abandoned_runs() == []
    recovered_tasks = TaskStore(reopened)
    summary = recovered_tasks.recovery_summary(run_id, "read")
    assert summary["resume_from_start"] is True
    owner = reopened.acquire_blocked_run_recovery(run_id, "local-user-task-recovery")
    assert owner is not None
    recovered_tasks.resolve_lost_worker(run_id, owner, "read", "resume")

    assert reopened.get_run(run_id)["state"] == "queued"
    assert recovered_tasks.records(run_id)[0]["status"] == "queued"
    decision = next(
        event
        for event in reversed(reopened.list_events(run_id))
        if event["event_type"] == "task.recovery_decided"
    )
    assert decision["payload"]["decision"] == "resume"
    assert decision["payload"]["evidence"]["resume_from_start"] is True


@pytest.mark.parametrize("exhausted_limit", ["iterations", "tokens"])
def test_lost_worker_cannot_resume_when_specialist_budget_is_exhausted(
    workspace_tmp_path, exhausted_limit
):
    runs = RunStore(workspace_tmp_path / f"task-budget-{exhausted_limit}.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=8,
        max_total_tokens=8,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    budgets = {"max_iterations": 1 if exhausted_limit == "iterations" else 4}
    if exhausted_limit == "tokens":
        budgets["max_total_tokens"] = 1
    tasks.save_plan(
        run_id,
        parent,
        [TaskDefinition(id="read", objective="Read evidence", **budgets)],
    )
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "read", "inference_id": "task-budget-inference"},
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "task-budget-inference",
                "message": {"role": "assistant", "content": ""},
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    assert tasks.recovery_summary(run_id, "read")["safe_to_resume"] is False


def test_lost_worker_pending_tool_call_cannot_be_resumed(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "uncertain-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="read", objective="Read evidence")])
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "earlier",
                "message": {"role": "assistant", "content": "An earlier answer."},
                "usage": None,
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "inference-1",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        },
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"README.md"}',
                            },
                        },
                    ],
                },
                "usage": None,
            },
        )
        runs.record_event(
            connection,
            run_id,
            "task.tool.result",
            {
                "task_id": "read",
                "call_id": "call-1",
                "journal_id": "read-1",
                "result": {"text": "done"},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    summary = tasks.recovery_summary(run_id, "read")
    assert summary["safe_to_resume"] is False
    assert summary["candidate_reviewable"] is False
    assert summary["pending_model_tool_calls"] == [
        {"call_id": "call-1", "name": "workspace_read_file"}
    ]
    with pytest.raises(ValueError, match="Review the saved candidate"):
        tasks.resolve_lost_worker(run_id, parent, "read", "resume")


def test_lost_worker_final_candidate_enters_existing_review_state(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "candidate-recovery.sqlite3")
    run_id = runs.create_run(
        objective="Plan",
        acceptance_criteria=["Report evidence"],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=12,
    )["id"]
    parent = runs.acquire_lease(run_id, "parent")
    tasks = TaskStore(runs)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="read", objective="Read evidence", acceptance_criteria=["Report evidence"]
            )
        ],
    )
    tasks.claim_ready(run_id, parent)
    with runs.connection() as connection:
        runs.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "read",
                "inference_id": "inference-1",
                "message": {"role": "assistant", "content": "The file says hello."},
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["read"]
    assert tasks.recovery_summary(run_id, "read")["candidate_reviewable"] is True
    tasks.resolve_lost_worker(run_id, parent, "read", "review_candidate")
    recovered = next(row for row in tasks.records(run_id) if row["local_id"] == "read")
    assert recovered["status"] == "verifying"
    assert json.loads(recovered["result"]) == {"candidate": "The file says hello."}

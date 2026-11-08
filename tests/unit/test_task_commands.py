import asyncio
import hashlib
import os
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_command_controller import FixtureTransport, setup
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.command_controller import CommandController
from xaa.runtime.states import RunState
from xaa.runtime.task_commands import command_evidence
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_worker import FilesystemWorkerError, execute_filesystem
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.tasks import TaskStore


async def private_tasks(root, transport, *, command_permission=True):
    _, store, run_id, owner, project = setup(root, transport)
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id=name,
                objective="Validate private files",
                acceptance_criteria=["Inspect actual validation"],
                permissions=["workspace.read", "workspace.write"]
                + (["workspace.command"] if command_permission else []),
            )
            for name in ("a", "b")
        ],
    )
    scopes = []
    for name in ("a", "b"):
        claim = tasks.claim_ready(run_id, owner)
        scope = TaskReadScope(run_id, owner, name, claim["token"])
        scopes.append(scope)
        for operation, arguments in [
            ("branch_init", {}),
            (
                "branch_write",
                {
                    "path": "fixture.txt",
                    "content": name,
                    "expected_sha256": hashlib.sha256(b"fixture").hexdigest(),
                },
            ),
        ]:
            await execute_filesystem(
                store,
                run_id,
                owner,
                name + operation,
                operation,
                arguments,
                idempotency_key=name + operation,
                internal=True,
                task_id=name,
                task_token=scope.task_token,
            )
    return store, run_id, owner, project, scopes


async def test_commands_capture_each_private_tree_and_stale_validation_is_visible(
    workspace_tmp_path,
):
    class InspectingTransport(FixtureTransport):
        def __init__(self):
            super().__init__()
            self.snapshots = []

        async def run(self, intent, snapshot, *, deadline):
            self.snapshots.append(snapshot)
            return await super().run(intent, snapshot, deadline=deadline)

    transport = InspectingTransport()
    store, run_id, owner, project, scopes = await private_tasks(workspace_tmp_path, transport)
    for scope in scopes:
        result = await CommandController(store, transport, task_scope=scope).execute(
            run_id,
            owner,
            "test-" + scope.task_id,
            {"argv": ["fixture"]},
            idempotency_key="test-" + scope.task_id,
            deadline=time.monotonic() + 15,
        )
        assert result["succeeded"]
        assert transport.snapshots[-1].files[0].content == scope.task_id.encode()
        evidence = command_evidence(store, run_id, scope.task_id)
        assert len(evidence) == 1 and evidence[0]["matches_current_private_tree"]
        assert evidence[0]["snapshot_sha256"] == result["snapshot_sha256"]
    assert (project / "fixture.txt").read_bytes() == b"fixture"
    assert store.conversation_messages(run_id) == []
    scope = scopes[0]
    await execute_filesystem(
        store,
        run_id,
        owner,
        "edit-after-test",
        "branch_write",
        {
            "path": "fixture.txt",
            "content": "changed after test",
            "expected_sha256": hashlib.sha256(b"a").hexdigest(),
        },
        idempotency_key="edit-after-test",
        internal=True,
        task_id="a",
        task_token=scope.task_token,
    )
    assert not command_evidence(store, run_id, "a")[0]["matches_current_private_tree"]
    assert command_evidence(store, run_id, "b")[0]["matches_current_private_tree"]


async def test_private_command_requires_explicit_task_permission(workspace_tmp_path):
    transport = FixtureTransport()
    store, run_id, owner, _, scopes = await private_tasks(
        workspace_tmp_path, transport, command_permission=False
    )
    with pytest.raises(PermissionError, match="command permission"):
        await CommandController(store, transport, task_scope=scopes[0]).execute(
            run_id,
            owner,
            "denied",
            {"argv": ["fixture"]},
            idempotency_key="denied",
            deadline=time.monotonic() + 15,
        )
    assert not store.unresolved_tool_calls(run_id)


async def test_lost_worker_can_attach_already_saved_command_result(workspace_tmp_path):
    transport = FixtureTransport()
    store, run_id, owner, _, scopes = await private_tasks(workspace_tmp_path, transport)
    scope = scopes[0]
    model_call_id = "model-command-1"
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": scope.task_id,
                "inference_id": "inference-command-1",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": model_call_id,
                            "type": "function",
                            "function": {
                                "name": "workspace_run_command",
                                "arguments": '{"argv":["fixture"]}',
                            },
                        }
                    ],
                },
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
    await CommandController(store, transport, task_scope=scope).execute(
        run_id,
        owner,
        "journal-command-1",
        {"argv": ["fixture"]},
        idempotency_key="journal-command-1",
        deadline=time.monotonic() + 15,
        task_call_id=model_call_id,
    )
    with store.connection() as connection:
        connection.execute(
            "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (f"{run_id}:{scope.task_id}",),
        )
    tasks = TaskStore(store)
    assert tasks.recover_expired(run_id, owner) == [scope.task_id]
    summary = tasks.recovery_summary(run_id, scope.task_id)
    assert summary["unresolved_tool_calls"] == 0
    assert summary["recoverable_commands"] == [
        {
            "model_call_id": model_call_id,
            "journal_call_id": "journal-command-1",
            "argv": ["fixture"],
            "state": "completed",
        }
    ]
    store.transition(run_id, RunState.BLOCKED, reason="lost_specialist", owner=owner)
    token = "task-command-recovery-api-token-123456"
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        response = client.post(
            f"/runs/{run_id}/tasks/{scope.task_id}/recovery/commands/{model_call_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["saved_tool_results"] == 1
    recovered = tasks.recovery_summary(run_id, scope.task_id)
    assert recovered["pending_model_tool_calls"] == []
    assert recovered["saved_tool_results"] == 1


async def test_lost_worker_can_attach_committed_private_file_result(workspace_tmp_path):
    transport = FixtureTransport()
    store, run_id, owner, _, scopes = await private_tasks(workspace_tmp_path, transport)
    scope = scopes[0]
    model_call_id = "model-read-1"
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": scope.task_id,
                "inference_id": "inference-read-1",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": model_call_id,
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"fixture.txt"}',
                            },
                        }
                    ],
                },
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
    journal_id = (
        "taskfs-" + hashlib.sha256(f"{run_id}:{scope.task_id}:{model_call_id}".encode()).hexdigest()
    )
    await execute_filesystem(
        store,
        run_id,
        owner,
        journal_id,
        "branch_read",
        {"path": "fixture.txt"},
        idempotency_key=journal_id,
        internal=True,
        task_id=scope.task_id,
        task_token=scope.task_token,
    )
    with store.connection() as connection:
        connection.execute(
            "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (f"{run_id}:{scope.task_id}",),
        )
    tasks = TaskStore(store)
    assert tasks.recover_expired(run_id, owner) == [scope.task_id]
    summary = tasks.recovery_summary(run_id, scope.task_id)
    assert summary["recoverable_task_actions"] == [
        {
            "model_call_id": model_call_id,
            "journal_call_id": journal_id,
            "tool_name": "workspace.read_file",
            "state": "completed",
        }
    ]
    store.transition(run_id, RunState.BLOCKED, reason="lost_specialist", owner=owner)
    token = "task-file-recovery-api-token-long-123456"
    with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
        response = client.post(
            f"/runs/{run_id}/tasks/{scope.task_id}/recovery/actions/{model_call_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        duplicate = client.post(
            f"/runs/{run_id}/tasks/{scope.task_id}/recovery/actions/{model_call_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200, response.text
    assert response.json()["saved_tool_results"] == 1
    assert duplicate.status_code == 409
    recovered = tasks.recovery_summary(run_id, scope.task_id)
    assert recovered["pending_model_tool_calls"] == []
    assert recovered["recoverable_task_actions"] == []


@pytest.mark.parametrize("save_result", [True, False])
async def test_lost_read_only_worker_can_reconcile_read(workspace_tmp_path, save_result: bool):
    transport = FixtureTransport()
    _, store, run_id, owner, project = setup(workspace_tmp_path, transport)
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id="reader", objective="Read actual evidence", permissions=["workspace.read"]
            )
        ],
    )
    claim = tasks.claim_ready(run_id, owner)
    assert claim is not None
    scope = TaskReadScope(run_id, owner, "reader", claim["token"])
    model_call_id = "model-read-only-1"
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": scope.task_id,
                "inference_id": "inference-read-only-1",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": model_call_id,
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"fixture.txt"}',
                            },
                        }
                    ],
                },
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
    journal_id = (
        "taskfs-" + hashlib.sha256(f"{run_id}:{scope.task_id}:{model_call_id}".encode()).hexdigest()
    )
    if save_result:
        result = await execute_filesystem(
            store,
            run_id,
            owner,
            journal_id,
            "read",
            {"path": "fixture.txt"},
            idempotency_key=journal_id,
            internal=True,
            task_id=scope.task_id,
            task_token=scope.task_token,
        )
        assert result["text"] == "fixture"
    else:
        scoped_store = RunStore(store.path, task_scope=scope)
        assert scoped_store.record_tool_intent(
            run_id,
            owner,
            call_id=journal_id,
            tool="workspace.read_file",
            version="1",
            arguments={"path": "fixture.txt"},
            idempotency_key=journal_id,
            internal=True,
        )
    with store.connection() as connection:
        connection.execute(
            "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (f"{run_id}:{scope.task_id}",),
        )
    assert tasks.recover_expired(run_id, owner) == [scope.task_id]
    summary = tasks.recovery_summary(run_id, scope.task_id)
    state = "completed" if save_result else "intended"
    assert summary["recoverable_task_actions"] == [
        {
            "model_call_id": model_call_id,
            "journal_call_id": journal_id,
            "tool_name": "workspace.read_file",
            "state": state,
        }
    ]
    if save_result:
        tasks.record_recovered_task_action_result(
            run_id, owner, scope.task_id, model_call_id, journal_id
        )
    else:
        store.transition(run_id, RunState.BLOCKED, reason="lost_specialist", owner=owner)
        token = "task-read-interruption-api-token-long-123456"
        with TestClient(create_app(LMStudioProvider(), SecretStr(token), store=store)) as client:
            response = client.post(
                f"/runs/{run_id}/tasks/{scope.task_id}/recovery/actions/{model_call_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 200, response.text
    recovered = tasks.recovery_summary(run_id, scope.task_id)
    assert recovered["pending_model_tool_calls"] == []
    assert recovered["saved_tool_results"] == 1
    result_event = next(
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "task.tool.result"
        and event["payload"].get("call_id") == model_call_id
    )
    if save_result:
        assert result_event["payload"]["result"]["text"] == "fixture"
    else:
        assert result_event["payload"]["result"]["error"]["code"] == "interrupted_read"
    assert (project / "fixture.txt").read_text(encoding="utf-8") == "fixture"


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("review_dependency_file", '{"dependency_id":"research","path":"fixture.txt"}'),
        (
            "review_dependency_output",
            '{"dependency_id":"research","artifact_id":"output-1","offset":0,"limit":1024}',
        ),
    ],
)
async def test_lost_reviewer_can_seal_dependency_read_without_assuming_contents(
    workspace_tmp_path, tool_name: str, arguments: str
):
    transport = FixtureTransport()
    _, store, run_id, owner, _ = setup(workspace_tmp_path, transport)
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id="reviewer",
                objective="Review saved dependency evidence",
                role="reviewer",
                permissions=["workspace.read"],
            )
        ],
    )
    claim = tasks.claim_ready(run_id, owner)
    assert claim is not None
    scope = TaskReadScope(run_id, owner, "reviewer", claim["token"])
    model_call_id = "review-dependency-call"
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": scope.task_id,
                "inference_id": "review-dependency-inference",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": model_call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": arguments,
                            },
                        }
                    ],
                },
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
        connection.execute(
            "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (f"{run_id}:{scope.task_id}",),
        )
    assert tasks.recover_expired(run_id, owner) == [scope.task_id]
    summary = tasks.recovery_summary(run_id, scope.task_id)
    journal_id = (
        "taskfs-" + hashlib.sha256(f"{run_id}:{scope.task_id}:{model_call_id}".encode()).hexdigest()
    )
    assert summary["recoverable_task_actions"] == [
        {
            "model_call_id": model_call_id,
            "journal_call_id": journal_id,
            "tool_name": tool_name,
            "state": "intended",
        }
    ]
    tasks.record_recovered_task_action_result(
        run_id, owner, scope.task_id, model_call_id, journal_id
    )
    saved = next(
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "task.tool.result"
        and event["payload"].get("call_id") == model_call_id
    )
    assert saved["payload"]["result"]["error"]["code"] == "interrupted_read"
    assert "No dependency contents are assumed" in saved["payload"]["result"]["error"]["message"]


@pytest.mark.parametrize("save_result", [False, True])
async def test_lost_researcher_can_seal_or_attach_web_search_result(
    workspace_tmp_path, save_result: bool
):
    store = RunStore(workspace_tmp_path / f"web-recovery-{save_result}.sqlite3")
    project = workspace_tmp_path / f"web-project-{save_result}"
    project.mkdir()
    run_id = store.create_run(
        objective="Research approved web sources",
        acceptance_criteria=[],
        workspace=str(project),
        mode="observe",
        model="fixture",
        approved_web_search=True,
    )["id"]
    owner = store.acquire_lease(run_id, "web-recovery-owner")
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id="researcher",
                objective="Search an approved source",
                role="researcher",
                permissions=["workspace.read", "web.search"],
            )
        ],
    )
    claim = tasks.claim_ready(run_id, owner)
    assert claim is not None
    scope = TaskReadScope(run_id, owner, "researcher", claim["token"])
    call_id = "research-search-call"
    journal_id = "taskfs-" + hashlib.sha256(f"{run_id}:researcher:{call_id}".encode()).hexdigest()
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "researcher",
                "inference_id": "research-inference",
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "web_search",
                                "arguments": '{"query":"saved research","count":1}',
                            },
                        }
                    ],
                },
                "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
            },
        )
    scoped_store = RunStore(store.path, task_scope=scope, internal_tools=True)
    assert scoped_store.record_tool_intent(
        run_id,
        owner,
        call_id=journal_id,
        tool="web.search",
        version="1",
        arguments={"query": "saved research", "count": 1},
        idempotency_key=journal_id,
    )
    if save_result:
        scoped_store.record_tool_result(
            run_id,
            owner,
            journal_id,
            {
                "status": "ok",
                "trust": "untrusted_web_content",
                "results": [{"url": "https://example.com", "title": "saved"}],
            },
        )
    with store.connection() as connection:
        connection.execute(
            "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (f"{run_id}:researcher",),
        )
    assert tasks.recover_expired(run_id, owner) == ["researcher"]
    summary = tasks.recovery_summary(run_id, "researcher")
    assert summary["recoverable_task_actions"] == [
        {
            "model_call_id": call_id,
            "journal_call_id": journal_id,
            "tool_name": "web.search",
            "state": "completed" if save_result else "intended",
        }
    ]
    store.transition(run_id, RunState.BLOCKED, reason="lost_specialist", owner=owner)
    recovery_owner = store.acquire_blocked_run_recovery(
        run_id, "test-web-recovery-owner", seconds=180
    )
    assert recovery_owner is not None
    store.transition(run_id, RunState.PLANNING, reason="test_recovery", owner=recovery_owner)
    store.transition(run_id, RunState.RUNNING, reason="test_recovery", owner=recovery_owner)
    tasks.record_recovered_task_action_result(
        run_id, recovery_owner, "researcher", call_id, journal_id
    )
    store.transition(run_id, RunState.BLOCKED, reason="test_result_attached", owner=recovery_owner)
    assert tasks.recovery_summary(run_id, "researcher")["pending_model_tool_calls"] == []
    saved = next(
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "task.tool.result" and event["payload"].get("call_id") == call_id
    )
    if save_result:
        assert saved["payload"]["result"]["results"][0]["title"] == "saved"
    else:
        assert saved["payload"]["result"]["error"]["code"] == "interrupted_read"


async def test_task_scope_is_required_for_snapshot_and_launch(workspace_tmp_path):
    transport = FixtureTransport()
    store, run_id, owner, _, scopes = await private_tasks(workspace_tmp_path, transport)
    journal = CommandJournal(store, task_scope=scopes[0])
    journal.reserve(run_id, owner, "pending", {"argv": ["fixture"]}, idempotency_key="pending")
    for other in (CommandJournal(store), CommandJournal(store, task_scope=scopes[1])):
        with pytest.raises(PermissionError):
            other.snapshot_intent(run_id, owner, "pending")
        with pytest.raises(PermissionError):
            other.authorize_launch(run_id, owner, "pending")
    with store.connection() as connection:
        connection.execute(
            "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (run_id + ":a",),
        )
    with pytest.raises(PermissionError):
        journal.snapshot_intent(run_id, owner, "pending")
    # Revoked workers cannot launch; root recovery can still seal an existing identity.
    result = await CommandController(store, transport).recover(run_id, owner, "pending")
    assert result["verification"] == "not_verified"
    assert not store.unresolved_tool_calls(run_id)
    assert store.conversation_messages(run_id) == []


async def test_workspace_hash_worker_rejects_junction_before_command_execution(workspace_tmp_path):
    _controller, store, run_id, owner, project = setup(workspace_tmp_path, FixtureTransport())
    outside = workspace_tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "private.txt"
    sentinel.write_bytes(b"outside command data")
    junction = project / "external"
    if os.name == "nt":
        await asyncio.to_thread(
            subprocess.run,
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        junction.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(FilesystemWorkerError) as failure:
            await execute_filesystem(
                store,
                run_id,
                owner,
                "junction-project-hash",
                "project_hash",
                {},
                idempotency_key="junction-project-hash",
                internal=True,
            )
        assert failure.value.code == "PermissionError"
        assert not any(
            event["event_type"] in {"execution.reserved", "execution.launched"}
            for event in store.list_events(run_id)
        )
        assert sentinel.read_bytes() == b"outside command data"
    finally:
        if os.name == "nt":
            junction.rmdir()
        else:
            junction.unlink()


async def test_expiry_during_command_cleans_up_without_publishing_success(workspace_tmp_path):
    class RevokingTransport(FixtureTransport):
        async def run(self, intent, snapshot, *, deadline):
            with store.connection() as connection:
                connection.execute(
                    "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
                    (run_id + ":a",),
                )
            return await super().run(intent, snapshot, deadline=deadline)

    transport = RevokingTransport()
    store, run_id, owner, project, scopes = await private_tasks(workspace_tmp_path, transport)
    with pytest.raises(PermissionError):
        await CommandController(store, transport, task_scope=scopes[0]).execute(
            run_id,
            owner,
            "revoked",
            {"argv": ["fixture"]},
            idempotency_key="revoked",
            deadline=time.monotonic() + 15,
        )
    assert transport.controls == ["cleanup", "status"]
    assert command_evidence(store, run_id, "a") == []
    assert not store.unresolved_tool_calls(run_id)
    assert store.conversation_messages(run_id) == []
    assert (project / "fixture.txt").read_bytes() == b"fixture"


def test_parent_command_grant_and_private_permissions_cannot_be_invented(workspace_tmp_path):
    _, store, run_id, owner, _ = setup(workspace_tmp_path, FixtureTransport())
    with store.connection() as connection:
        connection.execute(
            "DELETE FROM run_events WHERE run_id=? AND event_type='execution.granted'", (run_id,)
        )
    with pytest.raises(PermissionError):
        TaskStore(store).save_plan(
            run_id,
            owner,
            [
                TaskDefinition(
                    id="a",
                    objective="No grant",
                    permissions=["workspace.read", "workspace.write", "workspace.command"],
                )
            ],
        )


@pytest.mark.parametrize("stale", [False, True])
async def test_review_requires_actual_successful_current_private_validation(
    workspace_tmp_path, stale
):
    from xaa.runtime.states import RunState
    from xaa.runtime.task_review import TaskReviewDecision, review_candidate

    transport = FixtureTransport()
    store, run_id, owner, project, scopes = await private_tasks(workspace_tmp_path, transport)
    scope = scopes[0]
    await CommandController(store, transport, task_scope=scope).execute(
        run_id,
        owner,
        "validation",
        {"argv": ["fixture"]},
        idempotency_key="validation",
        deadline=time.monotonic() + 15,
    )
    if stale:
        await execute_filesystem(
            store,
            run_id,
            owner,
            "later-edit",
            "branch_write",
            {
                "path": "fixture.txt",
                "content": "later",
                "expected_sha256": hashlib.sha256(b"a").hexdigest(),
            },
            idempotency_key="later-edit",
            internal=True,
            task_id="a",
            task_token=scope.task_token,
        )
    result = await execute_filesystem(
        store,
        run_id,
        owner,
        "read-private",
        "branch_read",
        {"path": "fixture.txt"},
        idempotency_key="read-private",
        internal=True,
        task_id="a",
        task_token=scope.task_token,
    )
    TaskStore(store).record_read_worker_event(
        run_id,
        owner,
        "a",
        scope.task_token,
        "task.tool.result",
        {"task_id": "a", "call_id": "read", "journal_id": "read-private", "result": result},
    )
    TaskStore(store).record_outcome(
        run_id,
        owner,
        "a",
        scope.task_token,
        state="verifying",
        result={"candidate": "Inspect actual private validation"},
    )
    store.transition(run_id, RunState.VERIFYING, owner=owner)
    store.release_lease(run_id, owner)
    decision = TaskReviewDecision(
        decision="accept",
        rationale="Fixture inspects exact command output and diff",
        confirmed_criteria=[0],
        evidence_call_ids=["read-private"],
        changes_confirmed=True,
    )
    with pytest.raises(ValueError, match="successful validation evidence"):
        await review_candidate(store, run_id, "a", decision)
    chosen = decision.model_copy(update={"validation_call_ids": ["validation"]})
    if stale:
        with pytest.raises(ValueError, match="successful validation evidence"):
            await review_candidate(store, run_id, "a", chosen)
        assert (project / "fixture.txt").read_bytes() == b"fixture"
    else:
        await review_candidate(store, run_id, "a", chosen)
        assert (project / "fixture.txt").read_bytes() == b"a"


async def test_changed_output_cannot_be_presented_as_validation_evidence(workspace_tmp_path):
    transport = FixtureTransport()
    store, run_id, owner, _, scopes = await private_tasks(workspace_tmp_path, transport)
    result = await CommandController(store, transport, task_scope=scopes[0]).execute(
        run_id,
        owner,
        "validation",
        {"argv": ["fixture"]},
        idempotency_key="validation",
        deadline=time.monotonic() + 15,
    )
    artifact = store.get_artifact(result["stdout_artifact_id"])
    (store.path.parent / "artifacts" / artifact["storage_key"]).write_bytes(b"fabricated pass")
    with pytest.raises(ValueError, match="output changed"):
        command_evidence(store, run_id, "a")

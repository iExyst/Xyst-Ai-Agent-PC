import asyncio
import hashlib
import json
import time

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.command_controller import CommandController
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.states import RunState
from xaa.runtime.task_commands import read_dependency_command_output
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.task_review import candidate_evidence
from xaa.runtime.tool_worker import FilesystemWorkerError, execute_filesystem
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.task_workspaces import TaskWorkspaces
from xaa.storage.tasks import TaskStore


def setup(root):
    project = root / "project"
    project.mkdir()
    (project / "shared.txt").write_bytes(b"original\n")
    store = RunStore(root / "data" / "db.sqlite3")
    run_id = store.create_run(
        objective="Independent edits",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model="fixture",
        max_iterations=10,
    )["id"]
    owner = store.acquire_lease(run_id, "parent", seconds=300)
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        owner,
        [
            TaskDefinition(
                id=name,
                objective=name,
                permissions=["workspace.read", "workspace.write"],
                max_iterations=3,
            )
            for name in ("a", "b")
        ],
    )
    return store, run_id, owner, tasks, project


async def test_reviewer_can_read_only_completed_dependency_changes_and_test_outputs(
    workspace_tmp_path,
):
    from test_command_controller import FixtureTransport
    from test_command_controller import setup as setup_command

    controller, store, run_id, parent, project = setup_command(
        workspace_tmp_path, FixtureTransport()
    )
    (project / "removed.txt").write_text("remove this file", encoding="utf-8")
    with store.connection() as connection:
        connection.execute("UPDATE run_limits SET max_iterations=30 WHERE run_id=?", (run_id,))
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="implementation",
                objective="Edit and validate the private project",
                permissions=["workspace.read", "workspace.write", "workspace.command"],
                max_iterations=3,
            ),
            TaskDefinition(
                id="review",
                objective="Independently inspect the implementation and test output",
                role="reviewer",
                dependencies=["implementation"],
                permissions=["workspace.read"],
                max_iterations=16,
            ),
        ],
    )
    implementation = tasks.claim_ready(run_id, parent)
    assert implementation is not None
    worker = implementation["token"]
    for call_id, operation, arguments in [
        ("branch-init", "branch_init", {}),
        (
            "branch-edit",
            "branch_write",
            {
                "path": "fixture.txt",
                "content": "reviewed implementation",
                "expected_sha256": hashlib.sha256(b"fixture").hexdigest(),
            },
        ),
        (
            "branch-delete",
            "branch_delete",
            {
                "path": "removed.txt",
                "expected_sha256": hashlib.sha256(b"remove this file").hexdigest(),
            },
        ),
    ]:
        await execute_filesystem(
            store,
            run_id,
            parent,
            call_id,
            operation,
            arguments,
            idempotency_key=call_id,
            internal=True,
            task_id="implementation",
            task_token=worker,
        )
    command = await CommandController(
        store,
        controller.transport,
        task_scope=TaskReadScope(run_id, parent, "implementation", worker),
    ).execute(
        run_id,
        parent,
        "test-command",
        {"argv": ["fixture"]},
        idempotency_key="test-command",
        deadline=time.monotonic() + 15,
    )
    tasks.record_read_worker_event(
        run_id,
        parent,
        "implementation",
        worker,
        "task.tool.result",
        {
            "task_id": "implementation",
            "call_id": "test-call",
            "journal_id": "test-command",
            "result": command,
        },
    )
    tasks.record_outcome(
        run_id,
        parent,
        "implementation",
        worker,
        state="verifying",
        result={"candidate": "The check passed."},
    )
    with store.connection() as connection:
        connection.execute(
            "UPDATE tasks SET status='completed' WHERE id=?", (f"{run_id}:implementation",)
        )
    review = tasks.claim_ready(run_id, parent)
    assert review is not None
    review_token = review["token"]
    idle_provider = LMStudioProvider()
    runner = SpecialistRunner(store, idle_provider)
    gaps = runner._review_evidence_gaps(run_id, review["task"])
    assert "artifact:implementation:fixture.txt" in gaps
    assert "artifact:implementation:removed.txt" in gaps
    assert f"test-output:implementation:{command['stdout_artifact_id']}" in gaps
    assert f"test-output:implementation:{command['stderr_artifact_id']}" in gaps
    tasks.record_read_worker_event(
        run_id,
        parent,
        "review",
        review_token,
        "task.tool.result",
        {
            "task_id": "review",
            "journal_id": "review-output-shortcut",
            "reviewed_dependency_output": {
                "dependency_id": "implementation",
                "artifact_id": command["stdout_artifact_id"],
                "offset": command["stdout_bytes"] + 1,
                "total_bytes": command["stdout_bytes"],
                "bytes_read": 0,
                "eof": True,
            },
        },
    )
    assert (
        f"test-output:implementation:{command['stdout_artifact_id']}"
        in runner._review_evidence_gaps(run_id, review["task"])
    )
    file_result = TaskWorkspaces(store).read_dependency_change(
        run_id, parent, "review", review_token, "implementation", "fixture.txt"
    )
    assert file_result["text"] == "reviewed implementation"
    assert file_result["sha256"] == hashlib.sha256(b"reviewed implementation").hexdigest()
    deleted_result = TaskWorkspaces(store).read_dependency_change(
        run_id, parent, "review", review_token, "implementation", "removed.txt"
    )
    assert deleted_result["deleted"] is True
    stdout_id = command["stdout_artifact_id"]
    chunks = []
    offset = 0
    while True:
        chunk = read_dependency_command_output(
            store,
            TaskReadScope(run_id, parent, "review", review_token),
            "implementation",
            stdout_id,
            offset=offset,
            limit=5,
        )
        chunks.append(chunk["text"])
        offset += len(chunk["text"].encode("utf-8"))
        if chunk["eof"]:
            break
    assert "".join(chunks) == "untrusted command bytes"
    snapshot_id = next(
        item["id"]
        for item in store.list_artifacts(run_id)
        if item["id"] != stdout_id and item["id"] != command["stderr_artifact_id"]
    )
    with pytest.raises(PermissionError, match="validation evidence"):
        read_dependency_command_output(
            store,
            TaskReadScope(run_id, parent, "review", review_token),
            "implementation",
            snapshot_id,
            offset=0,
            limit=16,
        )

    calls = [
        {
            "name": "review_dependency_file",
            "arguments": {
                "dependency_id": "implementation",
                "path": "fixture.txt",
                "offset": offset,
                "limit": 5,
            },
        }
        for offset in range(0, len(file_result["text"]), 5)
    ]
    file_page_count = len(calls)
    calls.append(
        {
            "name": "review_dependency_file",
            "arguments": {"dependency_id": "implementation", "path": "removed.txt"},
        }
    )
    for artifact_id, size in (
        (command["stdout_artifact_id"], command["stdout_bytes"]),
        (command["stderr_artifact_id"], command["stderr_bytes"]),
    ):
        for offset in range(0, max(size, 1), 5):
            calls.append(
                {
                    "name": "review_dependency_output",
                    "arguments": {
                        "dependency_id": "implementation",
                        "artifact_id": artifact_id,
                        "offset": offset,
                        "limit": 5,
                    },
                }
            )

    class Reviewer(LMStudioProvider):
        def __init__(self):
            super().__init__()
            self.steps = iter(calls)
            self.first_response = True

        async def chat(self, *, model, messages, tools=None, **kwargs):
            assert model is None
            names = {item["function"]["name"] for item in tools or []}
            assert {"review_dependency_file", "review_dependency_output"} <= names
            if self.first_response:
                self.first_response = False
                message = {"role": "assistant", "content": "I reviewed the proposed change."}
            else:
                call = next(self.steps, None)
                if call is None:
                    message = {
                        "role": "assistant",
                        "content": "The implementation and validation evidence support the candidate.",
                    }
                else:
                    index = len(messages)
                    message = {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": f"review-{index}",
                                "type": "function",
                                "function": {
                                    "name": call["name"],
                                    "arguments": json.dumps(call["arguments"]),
                                },
                            }
                        ],
                    }
            return {"choices": [{"message": message}]}

    provider = Reviewer()
    runner.provider = provider
    try:
        await runner.run_claim(
            run_id,
            parent,
            review,
            deadline=time.monotonic() + 120,
        )
    finally:
        await provider.close()
        await idle_provider.close()
    # The first premature candidate must be rejected, then the actual tool loop reads each page.
    review_events = [
        event["payload"]
        for event in store.list_events(run_id)
        if event["event_type"] == "task.tool.result" and event["payload"].get("task_id") == "review"
    ]
    assert any(item.get("reviewed_dependency_artifact") for item in review_events), (
        [(item["local_id"], item["status"], item["result"]) for item in tasks.records(run_id)],
        review_events,
    )
    assert (
        sum(bool(item.get("reviewed_dependency_artifact")) for item in review_events)
        == file_page_count + 1
    )
    assert (
        sum(
            bool(item.get("reviewed_dependency_output"))
            for item in review_events
            if "call_id" in item
        )
        == len(calls) - file_page_count - 1
    )
    assert runner._review_evidence_gaps(run_id, review["task"]) == []
    assert next(row for row in tasks.records(run_id) if row["local_id"] == "review")["status"] == (
        "verifying"
    )


async def test_real_workers_keep_each_private_edit_and_original_project_separate(
    workspace_tmp_path,
):
    store, run_id, owner, tasks, project = setup(workspace_tmp_path)
    claims = [tasks.claim_ready(run_id, owner) for _ in range(2)]

    async def call(claim, identity, op, args):
        return await execute_filesystem(
            store,
            run_id,
            owner,
            identity,
            op,
            args,
            idempotency_key=identity,
            internal=True,
            task_id=claim["task"]["local_id"],
            task_token=claim["token"],
        )

    await asyncio.gather(
        *(call(claim, "init-" + claim["task"]["local_id"], "branch_init", {}) for claim in claims)
    )
    digest = hashlib.sha256(b"original\n").hexdigest()
    await asyncio.gather(
        *(
            call(
                claim,
                "edit-" + claim["task"]["local_id"],
                "branch_write",
                {
                    "path": "shared.txt",
                    "content": claim["task"]["local_id"] + " version\n",
                    "expected_sha256": digest,
                },
            )
            for claim in claims
        )
    )
    for claim in claims:
        name = claim["task"]["local_id"]
        result = await call(claim, "read-" + name, "branch_read", {"path": "shared.txt"})
        assert result["text"] == name + " version\n"
        changes = TaskWorkspaces(store).changes(run_id, name)
        assert len(changes) == 1 and changes[0]["base_sha256"] == digest
        assert name + " version" in changes[0]["diff"]
    assert (project / "shared.txt").read_bytes() == b"original\n"
    assert store.conversation_messages(run_id) == []
    assert not store.unresolved_tool_calls(run_id)
    with pytest.raises(FilesystemWorkerError):
        await execute_filesystem(
            store,
            run_id,
            owner,
            "different-call-same-key",
            "branch_read",
            {"path": "shared.txt"},
            idempotency_key="read-a",
            internal=True,
            task_id="a",
            task_token=claims[0]["token"],
        )
    with pytest.raises(FilesystemWorkerError):
        await call(
            claims[0],
            "edit-a",
            "branch_write",
            {"path": "shared.txt", "content": "duplicate", "expected_sha256": digest},
        )
    with pytest.raises(FilesystemWorkerError):
        await call(claims[0], "escape", "branch_create", {"path": "../escape.txt", "content": "no"})
    await call(
        claims[0],
        "newline-only",
        "branch_write",
        {
            "path": "shared.txt",
            "content": "a version",
            "expected_sha256": hashlib.sha256(b"a version\n").hexdigest(),
        },
    )
    newline_diff = TaskWorkspaces(store).changes(run_id, "a")[0]["diff"]
    assert "+a version\n\\ No newline at end of file" in newline_diff
    with store.connection() as c:
        c.execute(
            "UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",
            (run_id + ":a",),
        )
    with pytest.raises(FilesystemWorkerError):
        await call(
            claims[0],
            "stale",
            "branch_write",
            {
                "path": "shared.txt",
                "content": "stale",
                "expected_sha256": hashlib.sha256(b"a version\n").hexdigest(),
            },
        )
    assert not (project.parent / "escape.txt").exists()


async def test_private_journal_replays_cas_operations_in_order_across_delete_and_restore(
    workspace_tmp_path,
):
    store, run_id, owner, tasks, project = setup(workspace_tmp_path)
    claim = tasks.claim_ready(run_id, owner)

    async def call(call_id, operation, arguments):
        return await execute_filesystem(
            store,
            run_id,
            owner,
            call_id,
            operation,
            arguments,
            idempotency_key=call_id,
            internal=True,
            task_id="a",
            task_token=claim["token"],
        )

    await call("journal-init", "branch_init", {})
    original_hash = hashlib.sha256(b"original\n").hexdigest()
    await call(
        "journal-write",
        "branch_write",
        {"path": "shared.txt", "content": "temporary\n", "expected_sha256": original_hash},
    )
    temporary_hash = hashlib.sha256(b"temporary\n").hexdigest()
    await call(
        "journal-delete",
        "branch_delete",
        {"path": "shared.txt", "expected_sha256": temporary_hash},
    )
    assert TaskWorkspaces(store).changes(run_id, "a")[0]["proposed_sha256"] is None
    await call(
        "journal-restore",
        "branch_create",
        {"path": "shared.txt", "content": "original\n"},
    )
    assert TaskWorkspaces(store).changes(run_id, "a") == []
    assert (project / "shared.txt").read_bytes() == b"original\n"


async def test_private_delete_replay_rejects_mismatched_expected_hash_in_journal(
    workspace_tmp_path,
):
    store, run_id, owner, tasks, _project = setup(workspace_tmp_path)
    claim = tasks.claim_ready(run_id, owner)
    await execute_filesystem(
        store,
        run_id,
        owner,
        "tamper-init",
        "branch_init",
        {},
        idempotency_key="tamper-init",
        internal=True,
        task_id="a",
        task_token=claim["token"],
    )
    digest = hashlib.sha256(b"original\n").hexdigest()
    await execute_filesystem(
        store,
        run_id,
        owner,
        "tamper-delete",
        "branch_delete",
        {"path": "shared.txt", "expected_sha256": digest},
        idempotency_key="tamper-delete",
        internal=True,
        task_id="a",
        task_token=claim["token"],
    )
    with store.connection() as connection:
        row = connection.execute(
            "SELECT arguments FROM tool_calls WHERE run_id=? AND provider_call_id=?",
            (run_id, "tamper-delete"),
        ).fetchone()
        arguments = json.loads(row["arguments"])
        arguments["expected_sha256"] = "0" * 64
        connection.execute(
            "UPDATE tool_calls SET arguments=? WHERE run_id=? AND provider_call_id=?",
            (json.dumps(arguments), run_id, "tamper-delete"),
        )
        with pytest.raises(ValueError, match="deletion journal is inconsistent"):
            TaskWorkspaces(store).view(connection, run_id, "a")


async def test_specialist_deletion_stays_private_until_review_authorizes_it(workspace_tmp_path):
    from xaa.runtime.task_integration import preview
    from xaa.runtime.task_review import TaskReviewDecision, review_candidate

    store, run_id, owner, _tasks, project = setup(workspace_tmp_path)
    claim = _tasks.claim_ready(run_id, owner)
    read_result = None
    for call_id, operation, arguments in [
        ("delete-init", "branch_init", {}),
        ("delete-read", "branch_read", {"path": "shared.txt"}),
        (
            "delete-proposal",
            "branch_delete",
            {"path": "shared.txt", "expected_sha256": hashlib.sha256(b"original\n").hexdigest()},
        ),
    ]:
        result = await execute_filesystem(
            store,
            run_id,
            owner,
            call_id,
            operation,
            arguments,
            idempotency_key=call_id,
            internal=True,
            task_id="a",
            task_token=claim["token"],
        )
        if operation == "branch_read":
            read_result = result
    from xaa.storage.tasks import TaskStore

    TaskStore(store).record_read_worker_event(
        run_id,
        owner,
        "a",
        claim["token"],
        "task.tool.result",
        {
            "task_id": "a",
            "call_id": "read-model-call",
            "journal_id": "delete-read",
            "result": read_result,
        },
    )
    assert (project / "shared.txt").read_bytes() == b"original\n"
    changes = TaskWorkspaces(store).changes(run_id, "a")
    assert len(changes) == 1 and changes[0]["proposed_sha256"] is None
    assert "-original" in changes[0]["diff"]
    with store.connection() as connection:
        connection.execute(
            "UPDATE tasks SET status='verifying',acceptance_criteria='[\"Review deletion\"]' WHERE id=?",
            (run_id + ":a",),
        )
        connection.execute(
            "UPDATE tasks SET result=? WHERE id=?",
            (json.dumps({"candidate": "Proposed deletion"}), run_id + ":a"),
        )
    for state in (RunState.PLANNING, RunState.RUNNING, RunState.VERIFYING):
        store.transition(run_id, state, owner=owner)
    store.release_lease(run_id, owner)
    token = store.acquire_lease(run_id, "preview-delete", seconds=180)
    snapshot = await preview(store, run_id, "a", token)
    store.release_lease(run_id, token)
    assert snapshot.changes[0].proposed_sha256 is None and not snapshot.changes[0].conflict
    decision = TaskReviewDecision(
        decision="accept",
        rationale="Reviewed deletion diff",
        confirmed_criteria=[0],
        evidence_call_ids=["delete-read"],
        changes_confirmed=True,
    )
    await review_candidate(store, run_id, "a", decision)
    assert not (project / "shared.txt").exists()
    assert not store.unresolved_tool_calls(run_id)


async def test_private_change_limit_counts_deletions_and_can_return_under_limit(workspace_tmp_path):
    store, run_id, owner, tasks, project = setup(workspace_tmp_path)
    for index in range(17):
        (project / f"file-{index}.txt").write_bytes(f"original {index}\n".encode())
    claim = tasks.claim_ready(run_id, owner)
    init = await execute_filesystem(
        store,
        run_id,
        owner,
        "quota-init",
        "branch_init",
        {},
        idempotency_key="quota-init",
        internal=True,
        task_id="a",
        task_token=claim["token"],
    )
    assert init["effect"] == "private_copy_only"
    for index in range(16):
        path = f"file-{index}.txt"
        await execute_filesystem(
            store,
            run_id,
            owner,
            f"quota-del-{index}",
            "branch_delete",
            {
                "path": path,
                "expected_sha256": hashlib.sha256(f"original {index}\n".encode()).hexdigest(),
            },
            idempotency_key=f"quota-del-{index}",
            internal=True,
            task_id="a",
            task_token=claim["token"],
        )
    assert len(TaskWorkspaces(store).changes(run_id, "a")) == 16
    with pytest.raises(FilesystemWorkerError):
        await execute_filesystem(
            store,
            run_id,
            owner,
            "quota-add-over",
            "branch_create",
            {"path": "new.txt", "content": "new\n"},
            idempotency_key="quota-add-over",
            internal=True,
            task_id="a",
            task_token=claim["token"],
        )
    await execute_filesystem(
        store,
        run_id,
        owner,
        "quota-restore",
        "branch_create",
        {"path": "file-0.txt", "content": "original 0\n"},
        idempotency_key="quota-restore",
        internal=True,
        task_id="a",
        task_token=claim["token"],
    )
    assert len(TaskWorkspaces(store).changes(run_id, "a")) == 15


async def test_reviewed_deletion_conflict_requires_choice_and_rejects_stale_hash(
    workspace_tmp_path,
):
    from xaa.runtime.task_integration import IntegrationResolution, preview
    from xaa.runtime.task_review import TaskReviewDecision, review_candidate

    store, run_id, owner, _tasks, project = setup(workspace_tmp_path)
    claim = _tasks.claim_ready(run_id, owner)
    for call_id, operation, arguments in [
        ("conflict-init", "branch_init", {}),
        ("conflict-read", "branch_read", {"path": "shared.txt"}),
        (
            "conflict-delete",
            "branch_delete",
            {"path": "shared.txt", "expected_sha256": hashlib.sha256(b"original\n").hexdigest()},
        ),
    ]:
        await execute_filesystem(
            store,
            run_id,
            owner,
            call_id,
            operation,
            arguments,
            idempotency_key=call_id,
            internal=True,
            task_id="a",
            task_token=claim["token"],
        )
    from xaa.storage.tasks import TaskStore

    with store.connection() as connection:
        read_journal = connection.execute(
            "SELECT result FROM tool_calls WHERE run_id=? AND provider_call_id=?",
            (run_id, "conflict-read"),
        ).fetchone()
    TaskStore(store).record_read_worker_event(
        run_id,
        owner,
        "a",
        claim["token"],
        "task.tool.result",
        {
            "task_id": "a",
            "call_id": "read-model-call",
            "journal_id": "conflict-read",
            "result": json.loads(read_journal["result"]),
        },
    )
    with store.connection() as connection:
        connection.execute(
            "UPDATE tasks SET status='verifying',acceptance_criteria='[\"Review deletion\"]' WHERE id=?",
            (run_id + ":a",),
        )
        connection.execute(
            "UPDATE tasks SET result=? WHERE id=?",
            (json.dumps({"candidate": "Proposed deletion"}), run_id + ":a"),
        )
    for state in (RunState.PLANNING, RunState.RUNNING, RunState.VERIFYING):
        store.transition(run_id, state, owner=owner)
    store.release_lease(run_id, owner)
    (project / "shared.txt").write_bytes(b"external\n")
    token = store.acquire_lease(run_id, "preview-delete-conflict", seconds=180)
    snapshot = await preview(store, run_id, "a", token)
    store.release_lease(run_id, token)
    make_decision = lambda resolutions: TaskReviewDecision(
        decision="accept",
        rationale="Reviewed deletion conflict",
        confirmed_criteria=[0],
        evidence_call_ids=["conflict-read"],
        changes_confirmed=True,
        resolutions=resolutions,
    )
    with pytest.raises(ValueError, match="explicitly choose"):
        await review_candidate(store, run_id, "a", make_decision([]))
    (project / "shared.txt").write_bytes(b"another external change\n")
    with pytest.raises(ValueError, match="changed after conflict preview"):
        await review_candidate(
            store,
            run_id,
            "a",
            make_decision(
                [
                    IntegrationResolution(
                        path="shared.txt",
                        expected_current_sha256=snapshot.changes[0].current_sha256,
                        choice="proposed",
                    )
                ]
            ),
        )
    assert (project / "shared.txt").read_bytes() == b"another external change\n"


def test_interrupted_delete_reconciliation_observes_without_retry(workspace_tmp_path):
    from xaa.runtime.executor import WorkspaceExecutor

    store, run_id, owner, _tasks, project = setup(workspace_tmp_path)
    executor = WorkspaceExecutor(store)
    original_hash = hashlib.sha256(b"original\n").hexdigest()
    with store.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO tool_calls (id,task_id,run_id,tool_name,tool_version,arguments,idempotency_key,state,result,error,created_at,updated_at,provider_call_id) VALUES ('delete-recovery',NULL,?,'workspace.delete_file','1',?, 'delete-recovery','intended',NULL,NULL,'now','now','delete-recovery')",
            (run_id, json.dumps({"path": "shared.txt", "expected_sha256": original_hash})),
        )
        store.record_event(
            connection,
            run_id,
            "tool.intended",
            {"call_id": "delete-recovery", "tool": "workspace.delete_file", "internal": True},
        )
    assert executor.reconcile_writes(run_id, owner)["delete-recovery"] == "unchanged"
    assert (project / "shared.txt").read_bytes() == b"original\n"
    (project / "shared.txt").unlink()
    assert executor.reconcile_writes(run_id, owner)["delete-recovery"] == "applied"
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.parametrize("failure", ["known_no_effect", "unknown_effect", "completed"])
async def test_integration_recovery_never_replays_uncertain_or_completed_writes(
    workspace_tmp_path, monkeypatch, failure
):
    from xaa.runtime import task_integration

    store, run_id, owner, tasks, project = setup(workspace_tmp_path)
    claim = tasks.claim_ready(run_id, owner)
    for identity, operation, arguments in [
        ("init", "branch_init", {}),
        (
            "write",
            "branch_write",
            {
                "path": "shared.txt",
                "content": "approved\n",
                "expected_sha256": hashlib.sha256(b"original\n").hexdigest(),
            },
        ),
    ]:
        await execute_filesystem(
            store,
            run_id,
            owner,
            identity,
            operation,
            arguments,
            idempotency_key=identity,
            internal=True,
            task_id="a",
            task_token=claim["token"],
        )
    with store.connection() as connection:
        connection.execute("UPDATE tasks SET status='verifying' WHERE id=?", (run_id + ":a",))
    for state in (RunState.PLANNING, RunState.RUNNING, RunState.VERIFYING):
        store.transition(run_id, state, owner=owner)
    actual_execute = task_integration.execute_filesystem
    attempts = []

    async def interrupted(*args, **kwargs):
        if args[4] != "integrate":
            return await actual_execute(*args, **kwargs)
        attempts.append(args[3])
        if failure == "completed":
            await actual_execute(*args, **kwargs)
        else:
            store.record_tool_intent(
                run_id,
                owner,
                call_id=args[3],
                tool="workspace.write_file",
                version="1",
                arguments={},
                idempotency_key=args[3],
                internal=True,
            )
            if failure == "known_no_effect":
                store.record_tool_failure(
                    run_id, owner, args[3], code="FixturePrecondition", message="No write started"
                )
        raise RuntimeError("Simulated interruption before review commitment")

    monkeypatch.setattr(task_integration, "execute_filesystem", interrupted)
    with pytest.raises(RuntimeError, match="Simulated interruption"):
        await task_integration.integrate_changes(store, run_id, "a", owner, [])
    monkeypatch.setattr(task_integration, "execute_filesystem", actual_execute)
    if failure == "unknown_effect":
        with pytest.raises(ValueError, match="reconciliation"):
            await task_integration.integrate_changes(store, run_id, "a", owner, [])
        assert (project / "shared.txt").read_bytes() == b"original\n"
        assert len(store.unresolved_tool_calls(run_id)) == 1
    else:
        await task_integration.integrate_changes(store, run_id, "a", owner, [])
        assert (project / "shared.txt").read_bytes() == b"approved\n"
        assert not store.unresolved_tool_calls(run_id)
        with store.connection() as connection:
            writes = connection.execute(
                "SELECT provider_call_id,state FROM tool_calls WHERE run_id=? AND provider_call_id LIKE 'integrate-%'",
                (run_id,),
            ).fetchall()
        assert len(writes) == (2 if failure == "known_no_effect" else 1)
        assert sum(row["state"] == "completed" for row in writes) == 1
        if failure == "known_no_effect":
            assert writes[0]["provider_call_id"] != writes[1]["provider_call_id"]


@pytest.mark.parametrize("edit_style", ["text", "lines"])
async def test_specialists_produce_real_private_changes_without_parent_history_pollution(
    workspace_tmp_path,
    edit_style,
):
    store, run_id, owner, _tasks, project = setup(workspace_tmp_path)

    class Editor(LMStudioProvider):
        async def chat(self, *, model, messages, tools=None, **kwargs):
            objective = json.loads(messages[1]["content"])["objective"]
            if messages[-1]["role"] == "user":
                function = {
                    "name": "workspace_read_file",
                    "arguments": json.dumps({"path": "shared.txt"}),
                }
            elif messages[-1]["tool_call_id"] == "read":
                result = json.loads(messages[-1]["content"])
                function = {
                    "name": "workspace_write_lines"
                    if edit_style == "lines"
                    else "workspace_write_file",
                    "arguments": json.dumps(
                        {
                            "path": "shared.txt",
                            **(
                                {"lines": [objective + " version"], "final_newline": True}
                                if edit_style == "lines"
                                else {"content": objective + " version\n"}
                            ),
                            "expected_sha256": result["sha256"],
                        }
                    ),
                }
            else:
                assert json.loads(messages[-1]["content"])["effect"] == "private_copy_only"
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Changed shared.txt in my private copy",
                            }
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "read"
                                    if function["name"] == "workspace_read_file"
                                    else "write",
                                    "type": "function",
                                    "function": function,
                                }
                            ],
                        }
                    }
                ]
            }

    provider = Editor()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, owner, deadline=time.monotonic() + 45
        )
        assert all(row["status"] == "verifying" for row in statuses)
        for name in ("a", "b"):
            candidate = candidate_evidence(store, run_id, name)
            assert candidate["workspace_changes"][0]["path"] == "shared.txt"
            assert candidate["evidence"][0]["sha256"] == hashlib.sha256(b"original\n").hexdigest()
        assert (project / "shared.txt").read_bytes() == b"original\n"
        assert store.conversation_messages(run_id) == []
    finally:
        await provider.close()


async def test_last_request_budget_guidance_keeps_tools_and_never_grants_commands(
    workspace_tmp_path,
):
    store, run_id, owner, _, project = setup(workspace_tmp_path)
    with store.connection() as connection:
        connection.execute("DELETE FROM tasks WHERE id=?", (run_id + ":b",))

    class BudgetAware(LMStudioProvider):
        requests = 0

        async def chat(self, *, model, messages, tools=None, **kwargs):
            self.requests += 1
            assert json.loads(messages[1]["content"])["budgets"]["max_iterations"] == 3
            assert f"{4 - self.requests} model request(s) remain" in messages[0]["content"]
            names = {item["function"]["name"] for item in tools}
            assert "workspace_write_lines" in names and "workspace_run_command" not in names
            if self.requests == 3:
                assert "This is the last request" in messages[0]["content"]
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Candidate based on the saved file reads",
                            }
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": f"read-{self.requests}",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_read_file",
                                        "arguments": '{"path":"shared.txt"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    provider = BudgetAware()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, owner, deadline=time.monotonic() + 30
        )
        assert statuses == [{"task_id": "a", "status": "verifying"}]
        assert provider.requests == 3
        assert (project / "shared.txt").read_bytes() == b"original\n"
        assert store.conversation_messages(run_id) == []
    finally:
        await provider.close()


@pytest.mark.parametrize("choice", ["current", "proposed"])
async def test_review_integrates_only_approved_changes_and_requires_explicit_conflict_choice(
    workspace_tmp_path,
    choice,
):
    from xaa.runtime.task_integration import IntegrationResolution, preview
    from xaa.runtime.task_review import TaskReviewDecision, review_candidate

    store, run_id, owner, tasks, project = setup(workspace_tmp_path)

    class Editor(LMStudioProvider):
        async def chat(self, *, model, messages, tools=None, **kwargs):
            name = json.loads(messages[1]["content"])["objective"]
            if messages[-1]["role"] == "user":
                call = {
                    "id": "read",
                    "type": "function",
                    "function": {
                        "name": "workspace_read_file",
                        "arguments": json.dumps({"path": "shared.txt"}),
                    },
                }
            elif messages[-1]["tool_call_id"] == "read":
                call = {
                    "id": "write",
                    "type": "function",
                    "function": {
                        "name": "workspace_write_file",
                        "arguments": json.dumps(
                            {
                                "path": "shared.txt",
                                "expected_sha256": json.loads(messages[-1]["content"])["sha256"],
                                "content": name + " version\n",
                            }
                        ),
                    },
                }
            else:
                return {"choices": [{"message": {"role": "assistant", "content": "Proposed edit"}}]}
            return {"choices": [{"message": {"role": "assistant", "tool_calls": [call]}}]}

    provider = Editor()
    try:
        await SpecialistRunner(store, provider).run_ready(
            run_id, owner, deadline=time.monotonic() + 45
        )
    finally:
        await provider.close()
    store.transition(run_id, RunState.PLANNING, owner=owner)
    store.transition(run_id, RunState.RUNNING, owner=owner)
    store.transition(run_id, RunState.VERIFYING, owner=owner)
    store.release_lease(run_id, owner)
    # Existing tasks have no acceptance criteria in this fixture; make explicit test criteria.
    with store.connection() as c:
        c.execute(
            "UPDATE tasks SET acceptance_criteria='[\"Review the proposed file change\"]' WHERE run_id=?",
            (run_id,),
        )

    def decision(name, **extra):
        evidence = candidate_evidence(store, run_id, name)["evidence"]
        return TaskReviewDecision(
            decision="accept",
            rationale="Automated fixture review after inspecting private diff",
            confirmed_criteria=[0],
            evidence_call_ids=[evidence[0]["call_id"]],
            **extra,
        )

    with pytest.raises(ValueError, match="explicitly authorize"):
        await review_candidate(store, run_id, "a", decision("a"))
    assert (project / "shared.txt").read_bytes() == b"original\n"
    await review_candidate(store, run_id, "a", decision("a", changes_confirmed=True))
    assert (project / "shared.txt").read_bytes() == b"a version\n"
    with pytest.raises(ValueError, match="conflict"):
        await review_candidate(store, run_id, "b", decision("b", changes_confirmed=True))
    assert (project / "shared.txt").read_bytes() == b"a version\n"
    token = store.acquire_lease(run_id, "preview", seconds=180)
    snapshot = await preview(store, run_id, "b", token)
    store.release_lease(run_id, token)
    assert snapshot.changes[0].conflict
    (project / "shared.txt").write_bytes(b"external change\n")
    with pytest.raises(ValueError, match="changed after conflict preview"):
        await review_candidate(
            store,
            run_id,
            "b",
            decision(
                "b",
                changes_confirmed=True,
                resolutions=[
                    IntegrationResolution(
                        path="shared.txt",
                        expected_current_sha256=snapshot.changes[0].current_sha256,
                        choice=choice,
                    )
                ],
            ),
        )
    assert (project / "shared.txt").read_bytes() == b"external change\n"
    (project / "shared.txt").write_bytes(b"a version\n")
    await review_candidate(
        store,
        run_id,
        "b",
        decision(
            "b",
            changes_confirmed=True,
            resolutions=[
                IntegrationResolution(
                    path="shared.txt",
                    expected_current_sha256=snapshot.changes[0].current_sha256,
                    choice=choice,
                )
            ],
        ),
    )
    assert (project / "shared.txt").read_bytes() == (
        b"b version\n" if choice == "proposed" else b"a version\n"
    )
    assert all(row["status"] == "completed" for row in tasks.records(run_id))
    assert store.conversation_messages(run_id) == []
    assert not store.unresolved_tool_calls(run_id)


@pytest.mark.parametrize("next_response", ["write", "empty", "forbidden"])
async def test_specialist_response_repair_preserves_only_its_saved_scope(
    workspace_tmp_path, next_response
):
    store, run_id, owner, _tasks, project = setup(workspace_tmp_path)
    # Keep one task so this fixture counts each actual inference precisely.
    with store.connection() as c:
        c.execute("DELETE FROM tasks WHERE id=?", (run_id + ":b",))
        c.execute(
            "UPDATE tasks SET budgets=? WHERE id=?",
            (
                json.dumps({"max_iterations": 5, "max_seconds": 120, "max_output_tokens": 256}),
                run_id + ":a",
            ),
        )

    class RepairingEditor(LMStudioProvider):
        requests = 0

        async def chat(self, *, model, messages, tools=None, **kwargs):
            self.requests += 1
            if self.requests == 1:
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "read",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": '{"path":"shared.txt"}',
                            },
                        }
                    ],
                }
            elif self.requests == 2 or next_response == "empty":
                message = {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "private reasoning must not be promoted",
                }
            elif self.requests == 3:
                assert (
                    messages[-1]["role"] == "user" and "saved progress" in messages[-1]["content"]
                )
                assert any(item["function"]["name"] == "workspace_write_file" for item in tools)
                digest = hashlib.sha256(b"original\n").hexdigest()
                function = (
                    {
                        "name": "workspace_write_file",
                        "arguments": json.dumps(
                            {
                                "path": "shared.txt",
                                "expected_sha256": digest,
                                "content": "private edit\n",
                            }
                        ),
                    }
                    if next_response == "write"
                    else {
                        "name": "workspace_run_command",
                        "arguments": '{"argv":["not-permitted"]}',
                    }
                )
                message = {
                    "role": "assistant",
                    "tool_calls": [{"id": "edit", "type": "function", "function": function}],
                }
            else:
                message = {"role": "assistant", "content": "Proposed shared.txt edit"}
            return {
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    provider = RepairingEditor()
    try:
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, owner, deadline=time.monotonic() + 30
        )
        assert statuses[0]["status"] == ("verifying" if next_response == "write" else "failed")
        assert provider.requests == (4 if next_response == "write" else 3)
        events = store.list_events(run_id)
        assert sum(item["event_type"] == "task.response_requested" for item in events) == 1
        assert "private reasoning" not in json.dumps(events)
        assert (project / "shared.txt").read_bytes() == b"original\n"
        assert store.conversation_messages(run_id) == []
        assert not store.unresolved_tool_calls(run_id)
    finally:
        await provider.close()

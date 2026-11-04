import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import ValidationError
from xaa.runtime.executor import ReconciliationRequired
from xaa.runtime.states import RunState
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.storage.commands import CommandCapacityError, CommandJournal, CommandOutcome
from xaa.storage.runs import LeaseLost, RunStore


def setup(root, *, granted=True):
    project = root / "project"
    project.mkdir(exist_ok=True)
    (project / "fixture.txt").write_bytes(b"fixture")
    store = RunStore(root / "data" / "runs.sqlite3")
    grant = {"distribution": "Ubuntu-24.04", "profile_sha256": "a" * 64, "max_seconds": 10}
    run = store.create_run(
        objective="Inspect a fixture",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant=grant if granted else None,
    )
    owner = store.acquire_lease(run["id"], "test", seconds=3600)
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    return store, run["id"], owner, project


def artifact(store, run, owner, identity, kind, data):
    directory = store.path.parent / "artifacts"
    directory.mkdir(exist_ok=True)
    name = f"{uuid4()}.{kind}.txt"
    (directory / name).write_bytes(data)
    return store.register_artifact(run, owner, name, provenance=f"execution.{kind}:{identity}")


def ready(store, run, owner, project, call="call"):
    journal = CommandJournal(store)
    intent = journal.reserve(
        run, owner, call, {"argv": ["python3", "-m", "unittest"]}, idempotency_key=call
    )
    snapshot = capture_workspace(project)
    manifest = artifact(
        store, run, owner, intent.execution_id, "snapshot", snapshot.manifest_bytes()
    )
    journal.snapshot_ready(run, owner, call, snapshot, manifest["id"])
    return journal, intent, snapshot


def outcome(store, run, owner, intent, snapshot):
    out = b"literal untrusted output\n"
    err = b"test passed\n"
    stdout = artifact(store, run, owner, intent.execution_id, "stdout", out)
    stderr = artifact(store, run, owner, intent.execution_id, "stderr", err)
    return CommandOutcome(
        execution_id=intent.execution_id,
        snapshot_sha256=snapshot.sha256,
        filter_sha256="f" * 64,
        termination_reason="finished",
        exit_code=0,
        supervisor_exit_code=0,
        succeeded=True,
        output_complete=True,
        cleanup_confirmed=True,
        stdout_artifact_id=stdout["id"],
        stderr_artifact_id=stderr["id"],
        stdout_sha256=hashlib.sha256(out).hexdigest(),
        stderr_sha256=hashlib.sha256(err).hexdigest(),
        stdout_bytes=len(out),
        stderr_bytes=len(err),
        elapsed_seconds=0.1,
    )


def test_permission_is_not_implied_by_mode_or_prompt(workspace_tmp_path):
    store, run, owner, _ = setup(workspace_tmp_path, granted=False)
    with pytest.raises(PermissionError, match="explicit"):
        CommandJournal(store).reserve(
            run, owner, "call", {"argv": ["python3"]}, idempotency_key="key"
        )
    assert not store.unresolved_tool_calls(run)
    with pytest.raises(ValidationError):
        CommandJournal(store).reserve(
            run,
            owner,
            "call",
            {"argv": ["python3"], "distribution": "other"},
            idempotency_key="key",
        )


def test_snapshot_launch_result_and_reopen_preserve_identity(workspace_tmp_path):
    store, run, owner, project = setup(workspace_tmp_path)
    journal, intent, snapshot = ready(store, run, owner, project)
    assert intent.distribution == "Ubuntu-24.04" and intent.profile_sha256 == "a" * 64
    assert store.unresolved_tool_calls(run)[0]["arguments"]["execution_id"] == intent.execution_id
    assert owner not in json.dumps(store.list_events(run))
    saved = RunStore(store.path)
    assert saved.unresolved_tool_calls(run)[0]["arguments"]["execution_id"] == intent.execution_id
    with pytest.raises(ReconciliationRequired):
        CommandJournal(saved).reserve(
            run, owner, "call", {"argv": ["different"]}, idempotency_key="call"
        )
    assert journal.authorize_launch(run, owner, "call").execution_id == intent.execution_id
    with pytest.raises(ReconciliationRequired):
        journal.authorize_launch(run, owner, "call")
    result = journal.complete(run, owner, "call", outcome(store, run, owner, intent, snapshot))
    assert result["stdout_excerpt"] == "literal untrusted output\n"
    assert result["trust"] == "untrusted_command_output"
    assert not store.unresolved_tool_calls(run)
    with pytest.raises(ReconciliationRequired):
        journal.reserve(run, owner, "another-call", {"argv": ["python3"]}, idempotency_key="call")


def test_intent_and_events_roll_back_together(workspace_tmp_path, monkeypatch):
    store, run, owner, _ = setup(workspace_tmp_path)
    original = store.record_event
    before = store.list_events(run)

    def fail(connection, run_id, kind, payload):
        if kind == "execution.reserved":
            raise RuntimeError("fixture transaction failure")
        original(connection, run_id, kind, payload)

    monkeypatch.setattr(store, "record_event", fail)
    with pytest.raises(RuntimeError, match="transaction failure"):
        CommandJournal(store).reserve(
            run, owner, "call", {"argv": ["python3"]}, idempotency_key="call"
        )
    assert not store.unresolved_tool_calls(run)
    assert store.list_events(run) == before


def test_lost_or_replaced_lease_cannot_authorize_old_command(workspace_tmp_path):
    store, run, owner, project = setup(workspace_tmp_path)
    journal, _, _ = ready(store, run, owner, project)
    store.release_lease(run, owner)
    replacement = store.acquire_lease(run, "replacement")
    with pytest.raises(LeaseLost):
        journal.authorize_launch(run, owner, "call")
    with pytest.raises(ReconciliationRequired, match="new lease"):
        journal.authorize_launch(run, replacement, "call")
    assert len(store.unresolved_tool_calls(run)) == 1


def test_atomic_global_admission_holds_unresolved_slots(workspace_tmp_path):
    entries = [setup(workspace_tmp_path) for _ in range(3)]

    def reserve(entry):
        store, run, owner, _ = entry
        try:
            return CommandJournal(store).reserve(
                run, owner, "call", {"argv": ["python3"]}, idempotency_key="call"
            )
        except CommandCapacityError:
            return None

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(reserve, entries))
    assert sum(value is not None for value in results) == 2
    retained = next(
        entry for entry, result in zip(entries, results, strict=True) if result is not None
    )
    store, run, owner, _ = retained
    store.transition(run, RunState.PAUSED, owner=owner)
    other = next(entry for entry, result in zip(entries, results, strict=True) if result is None)
    with pytest.raises(CommandCapacityError):
        CommandJournal(other[0]).reserve(
            other[1], other[2], "call", {"argv": ["python3"]}, idempotency_key="call"
        )


def test_missing_snapshot_and_changed_artifact_block_completion(workspace_tmp_path):
    store, run, owner, project = setup(workspace_tmp_path)
    journal = CommandJournal(store)
    journal.reserve(run, owner, "call", {"argv": ["python3"]}, idempotency_key="call")
    with pytest.raises(ValueError, match="snapshot"):
        journal.authorize_launch(run, owner, "call")
    snapshot = capture_workspace(project)
    manifest = artifact(
        store,
        run,
        owner,
        store.unresolved_tool_calls(run)[0]["arguments"]["execution_id"],
        "snapshot",
        snapshot.manifest_bytes(),
    )
    journal.snapshot_ready(run, owner, "call", snapshot, manifest["id"])
    intent = journal.authorize_launch(run, owner, "call")
    result = outcome(store, run, owner, intent, snapshot)
    with store.connection() as connection:
        key = connection.execute(
            "SELECT storage_key FROM artifacts WHERE id=?", (result.stdout_artifact_id,)
        ).fetchone()[0]
    (store.path.parent / "artifacts" / key).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        journal.complete(run, owner, "call", result)
    assert len(store.unresolved_tool_calls(run)) == 1


def test_grant_cannot_target_application_data(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "data" / "runs.sqlite3")
    with pytest.raises(PermissionError, match="separate"):
        store.create_run(
            objective="bad",
            acceptance_criteria=[],
            workspace=str(workspace_tmp_path),
            mode="workspace",
            model=None,
            command_grant={"distribution": "Ubuntu-24.04", "profile_sha256": "a" * 64},
        )
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_interrupted_result_requires_sealed_cleanup_not_a_new_launch(workspace_tmp_path):
    store, run, owner, project = setup(workspace_tmp_path)
    journal, intent, _ = ready(store, run, owner, project)
    journal.authorize_launch(run, owner, "call")
    store.release_lease(run, owner)
    replacement = store.acquire_lease(run, "recovery")
    proof = {
        "execution_id": intent.execution_id,
        "distribution": intent.distribution,
        "profile_sha256": intent.profile_sha256,
        "closed": True,
        "unit_stopped": True,
        "cleanup_confirmed": True,
        "manager_alive": True,
        "manager_outcome_available": False,
    }
    unsafe = artifact(
        store, run, replacement, intent.execution_id, "cleanup", json.dumps(proof).encode()
    )
    with pytest.raises(ValueError, match="finished manager"):
        journal.close_interrupted(run, replacement, "call", unsafe["id"])
    assert len(store.unresolved_tool_calls(run)) == 1
    proof["manager_alive"] = False
    confirmed = artifact(
        store, run, replacement, intent.execution_id, "cleanup", json.dumps(proof).encode()
    )
    with pytest.raises(LeaseLost):
        journal.close_interrupted(run, owner, "call", confirmed["id"])
    result = journal.close_interrupted(run, replacement, "call", confirmed["id"])
    assert result["verification"] == "not_verified" and result["ok"] is False
    assert "exit_code" not in result
    assert not store.unresolved_tool_calls(run)
    with pytest.raises(ReconciliationRequired):
        journal.reserve(run, replacement, "call", {"argv": ["python3"]}, idempotency_key="call")


def test_interruptions_cannot_claim_success_or_numeric_cleanup_flags(workspace_tmp_path):
    store, run, owner, project = setup(workspace_tmp_path)
    _, intent, snapshot = ready(store, run, owner, project)
    original = outcome(store, run, owner, intent, snapshot).model_dump()
    for changed in (
        {"termination_reason": "cancelled"},
        {"cleanup_confirmed": 1},
        {"cleanup_confirmed": False},
        {"stdout_bytes": 1048576, "stderr_bytes": 1},
        {"elapsed_seconds": float("nan")},
    ):
        with pytest.raises(ValidationError):
            CommandOutcome.model_validate({**original, **changed})


def test_user_stop_cleanup_is_revision_fenced_and_never_promotes_success(workspace_tmp_path):
    store, run, owner, project = setup(workspace_tmp_path)
    journal, intent, _ = ready(store, run, owner, project)
    journal.authorize_launch(run, owner, "call")
    paused = store.transition(run, RunState.PAUSED, reason="user_requested")
    proof_data = {
        "execution_id": intent.execution_id,
        "distribution": intent.distribution,
        "profile_sha256": intent.profile_sha256,
        "closed": True,
        "unit_stopped": True,
        "cleanup_confirmed": True,
        "manager_alive": False,
        "manager_outcome_available": False,
    }
    from xaa.storage.commands import CommandCleanupEvidence

    proof = CommandCleanupEvidence.model_validate(proof_data)
    with pytest.raises(ReconciliationRequired):
        journal.record_stopped_cleanup(run, paused["revision"] - 1, "call", proof)
    store.transition(run, RunState.QUEUED)
    with pytest.raises(ReconciliationRequired):
        journal.record_stopped_cleanup(run, paused["revision"], "call", proof)
    current = store.transition(run, RunState.CANCELLED)
    result = journal.record_stopped_cleanup(run, current["revision"], "call", proof)
    assert not result["ok"] and result["verification"] == "not_verified"
    assert "exit_code" not in result and not store.unresolved_tool_calls(run)
    assert store.get_run(run)["state"] == "cancelled"
    assert store.get_run(run)["revision"] == current["revision"]

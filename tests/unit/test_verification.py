import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.verification import FileContentCheck, verify_files, verify_files_owned
from xaa.storage.runs import RunStore


async def test_owned_verification_checks_fresh_bytes_and_keeps_results_internal(workspace_tmp_path):
    (workspace_tmp_path / "one.txt").write_bytes(b"first\n")
    (workspace_tmp_path / "two.txt").write_bytes(b"second")
    store = RunStore(workspace_tmp_path / "owned.sqlite3")
    run = store.create_run(
        objective="Check files",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model=None,
    )
    token = store.acquire_lease(run["id"], "verifier")
    checks = [
        FileContentCheck(path="one.txt", expected_content="first\n"),
        FileContentCheck(path="two.txt", expected_content="second\n"),
    ]
    failures = await verify_files_owned(store, run["id"], token, checks)
    assert len(failures) == 1 and failures[0].startswith("two.txt:")
    assert not store.conversation_messages(run["id"])
    assert not store.unresolved_tool_calls(run["id"])
    (workspace_tmp_path / "two.txt").write_bytes(b"second\n")
    assert await verify_files_owned(store, run["id"], token, checks) == []


@pytest.mark.parametrize("actual,expected", [(b"value=2", "value=2\n"), (b"\xff", "\ufffd")])
def test_exact_artifact_check_detects_missing_newline(workspace_tmp_path, actual, expected):
    target = workspace_tmp_path / "config.txt"
    target.write_bytes(actual)
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    checks = [{"path": "config.txt", "expected_content": expected}]
    run_id = store.create_run(
        objective="Edit",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
        file_checks=checks,
    )["id"]
    token = store.acquire_lease(run_id, "verifier")
    check = FileContentCheck.model_validate(checks[0])
    failures = verify_files(WorkspaceExecutor(store), run_id, token, [check])
    assert len(failures) == 1
    assert "expected exact content" in failures[0]
    assert store.conversation_messages(run_id) == []
    target.write_bytes(expected.encode("utf-8"))
    assert verify_files(WorkspaceExecutor(store), run_id, token, [check]) == []


async def test_restored_candidate_is_checked_before_accepting_it(workspace_tmp_path):
    class NoInferenceProvider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, **kwargs):
            raise AssertionError("Consumed iteration budget must prevent inference")

    (workspace_tmp_path / "config.txt").write_bytes(b"wrong")
    store = RunStore(workspace_tmp_path / "restore.sqlite3")
    run_id = store.create_run(
        objective="Fix",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
        max_iterations=1,
        file_checks=[{"path": "config.txt", "expected_content": "correct\n"}],
    )["id"]
    token = store.acquire_lease(run_id, "old-worker")
    store.append_worker_event(
        run_id,
        token,
        "model.message",
        {"iteration": 0, "message": {"role": "assistant", "content": "Done"}},
    )
    store.release_lease(run_id, token)
    provider = NoInferenceProvider()
    try:
        await Coordinator(RunStore(store.path), provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "blocked"
    assert any(
        event["event_type"] == "verification.feedback" for event in store.list_events(run_id)
    )

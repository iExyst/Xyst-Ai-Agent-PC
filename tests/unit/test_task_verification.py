import json

import pytest
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.task_verification import inspect_task_files
from xaa.runtime.verification import FileContentCheck
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


def test_candidate_claim_is_checked_against_actual_files(workspace_tmp_path):
    target = workspace_tmp_path / "result.txt"
    target.write_bytes(b"incorrect")
    runs = RunStore(workspace_tmp_path / "tasks.sqlite3")
    run_id = runs.create_run(
        objective="Fix result",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
    )["id"]
    token = runs.acquire_lease(run_id, "coordinator")
    tasks = TaskStore(runs)
    tasks.save_plan(run_id, token, [TaskDefinition(id="fix", objective="Fix result")])
    check = FileContentCheck(path="result.txt", expected_content="correct\n")
    executor = WorkspaceExecutor(runs)
    with pytest.raises(ValueError, match="submitted candidate"):
        inspect_task_files(tasks, executor, run_id, token, "fix", [check])
    claim = tasks.claim_ready(run_id, token)
    tasks.record_outcome(
        run_id,
        token,
        "fix",
        claim["token"],
        state="verifying",
        result={"summary": "All requirements passed"},
    )
    assert inspect_task_files(tasks, executor, run_id, token, "fix", [check])
    record = TaskStore(RunStore(runs.path)).records(run_id)[0]
    assert json.loads(record["checkpoint"])["passed"] is False
    assert record["status"] == "verifying"
    target.write_bytes(b"correct\n")
    assert inspect_task_files(tasks, executor, run_id, token, "fix", [check]) == []
    record = tasks.records(run_id)[0]
    assert json.loads(record["checkpoint"])["passed"] is True
    assert record["status"] == "verifying"  # Other criteria still require review.
    assert runs.conversation_messages(run_id) == []
    with pytest.raises(ValueError, match="explicit file"):
        inspect_task_files(tasks, executor, run_id, token, "fix", [])

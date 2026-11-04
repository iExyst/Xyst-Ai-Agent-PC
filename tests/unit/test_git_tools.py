import asyncio
import subprocess

from xaa.runtime.git_tools import inspect_git
from xaa.storage.runs import RunStore


def test_git_inspection_reports_real_changes(workspace_tmp_path):
    repository = workspace_tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    target = repository / "file.txt"
    target.write_text("before\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "file.txt"], check=True, capture_output=True
    )
    target.write_text("after\n", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "db.sqlite3")
    run_id = store.create_run(
        objective="Inspect Git",
        acceptance_criteria=[],
        workspace=str(repository),
        mode="observe",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    status = asyncio.run(inspect_git(store, run_id, token, "status", "status"))
    diff = asyncio.run(inspect_git(store, run_id, token, "diff", "diff"))
    assert status["exit_code"] == 0 and "file.txt" in status["excerpt"]
    assert diff["exit_code"] == 0 and "+after" in diff["excerpt"]
    assert (store.path.parent / "artifacts" / diff["artifact"]).exists()

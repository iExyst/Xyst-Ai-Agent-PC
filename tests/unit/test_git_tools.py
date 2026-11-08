import asyncio
import os
import subprocess

import pytest
from xaa.runtime.git_tools import git_executable, inspect_git
from xaa.storage.runs import RunStore


def test_git_discovery_ignores_project_relative_and_batch_candidates(
    workspace_tmp_path, monkeypatch
):
    project = workspace_tmp_path / "project"
    project.mkdir()
    trusted = workspace_tmp_path / "installed"
    trusted.mkdir()
    name = "git.exe" if os.name == "nt" else "git"
    for directory in (project, trusted):
        target = directory / name
        target.write_bytes(b"fixture executable identity")
        target.chmod(0o755)
    (trusted / "git.cmd").write_text("@exit /b 99", encoding="utf-8")
    monkeypatch.chdir(project)
    monkeypatch.setenv("PATH", os.pathsep.join(("", ".", str(project), str(trusted))))
    assert git_executable(project) == str((trusted / name).resolve())
    (trusted / name).unlink()
    with pytest.raises(RuntimeError, match="outside the workspace"):
        git_executable(project)


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


def test_git_inspection_does_not_expose_external_junction_contents(workspace_tmp_path):
    repository = workspace_tmp_path / "repository"
    outside = workspace_tmp_path / "outside"
    repository.mkdir()
    outside.mkdir()
    private = outside / "private-marker.txt"
    private.write_text("outside-private-content", encoding="utf-8")
    subprocess.run(["git", "init", str(repository)], check=True, capture_output=True)
    (repository / "tracked.txt").write_text("safe\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repository), "add", "tracked.txt"], check=True, capture_output=True
    )
    junction = repository / "external"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        junction.symlink_to(outside, target_is_directory=True)
    try:
        store = RunStore(workspace_tmp_path / "db.sqlite3")
        run_id = store.create_run(
            objective="Inspect repository with an external junction",
            acceptance_criteria=[],
            workspace=str(repository),
            mode="observe",
            model=None,
        )["id"]
        token = store.acquire_lease(run_id, "worker")
        status = asyncio.run(inspect_git(store, run_id, token, "junction-status", "status"))
        diff = asyncio.run(inspect_git(store, run_id, token, "junction-diff", "diff"))
        assert status["exit_code"] == diff["exit_code"] == 0
        assert "outside-private-content" not in status["excerpt"] + diff["excerpt"]
        assert "private-marker.txt" not in status["excerpt"] + diff["excerpt"]
        assert private.read_text(encoding="utf-8") == "outside-private-content"
    finally:
        if os.name == "nt":
            junction.rmdir()
        else:
            junction.unlink()

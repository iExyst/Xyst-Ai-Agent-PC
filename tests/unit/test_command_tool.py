import json
import time

import pytest
from test_command_controller import FixtureTransport
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime import coordinator as module
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.storage.runs import LeaseLost, RunStore


@pytest.mark.parametrize("grant", [False, True])
async def test_command_catalog_and_dispatch_require_saved_user_grant(
    workspace_tmp_path, monkeypatch, grant
):
    project = workspace_tmp_path / "project"
    project.mkdir()
    (project / "fixture.txt").write_text("original")
    store = RunStore(workspace_tmp_path / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Check project",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={"distribution": "Ubuntu", "profile_sha256": "a" * 64} if grant else None,
    )
    owner = store.acquire_lease(run["id"], "worker", seconds=60)
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    run = store.get_run(run["id"])
    provider = LMStudioProvider()
    launches = []
    transport = FixtureTransport()
    transport.profile_sha256 = "a" * 64

    async def discover(distribution, *, deadline):
        launches.append(distribution)
        return transport

    monkeypatch.setattr(module.WSLTransport, "discover", discover)
    coordinator = Coordinator(store, provider)
    try:
        tools = coordinator._tools(run["id"], run)
        assert ("workspace_run_command" in [item["function"]["name"] for item in tools]) == grant
        call = {
            "id": "command",
            "function": {
                "name": "workspace_run_command",
                "arguments": json.dumps({"argv": ["/usr/bin/python3", "--version"]}),
            },
        }
        if not grant:
            with pytest.raises(PermissionError):
                await coordinator._execute_tool(
                    run["id"], owner, run, 0, time.monotonic() + 15, call, tools
                )
            assert launches == [] and store.list_artifacts(run["id"]) == []
        else:
            result = await coordinator._execute_tool(
                run["id"], owner, run, 0, time.monotonic() + 15, call, tools
            )
            assert result["succeeded"] is True
            assert result["trust"] == "untrusted_command_output"
            assert launches == ["Ubuntu"]
            assert len(store.list_artifacts(run["id"])) == 3
            assert not store.unresolved_tool_calls(run["id"])
            assert (project / "fixture.txt").read_text() == "original"
            call["id"] = "override"
            call["function"]["arguments"] = json.dumps(
                {"argv": ["fixture"], "distribution": "Other"}
            )
            with pytest.raises(ValueError):
                await coordinator._execute_tool(
                    run["id"], owner, run, 1, time.monotonic() + 15, call, tools
                )
            assert launches == ["Ubuntu"]
            call["function"]["arguments"] = json.dumps({"argv": ["fixture"]})
            transport.profile_sha256 = "b" * 64
            with pytest.raises(PermissionError, match="profile changed"):
                await coordinator._execute_tool(
                    run["id"], owner, run, 1, time.monotonic() + 15, call, tools
                )
            assert launches == ["Ubuntu", "Ubuntu"]
            assert not store.unresolved_tool_calls(run["id"])
            store.transition(run["id"], RunState.PAUSED, reason="user_pause")
            call["function"]["arguments"] = json.dumps({"argv": ["fixture"]})
            with pytest.raises(LeaseLost):
                await coordinator._execute_tool(
                    run["id"], owner, run, 1, time.monotonic() + 15, call, tools
                )
            assert launches == ["Ubuntu", "Ubuntu"]
    finally:
        await provider.close()


async def test_authorized_automation_never_exposes_or_dispatches_commands(workspace_tmp_path):
    project = workspace_tmp_path / "project"
    project.mkdir()
    store = RunStore(workspace_tmp_path / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Update project files",
        acceptance_criteria=[],
        workspace=str(project),
        mode="authorized_automation",
        model=None,
    )
    owner = store.acquire_lease(run["id"], "worker", seconds=60)
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    run = store.get_run(run["id"])
    coordinator = Coordinator(store, LMStudioProvider())
    tools = coordinator._tools(run["id"], run)
    names = [item["function"]["name"] for item in tools]
    assert "workspace_create_file" in names
    assert "workspace_run_command" not in names
    forged = {
        "id": "command",
        "function": {
            "name": "workspace_run_command",
            "arguments": json.dumps({"argv": ["/usr/bin/python3", "--version"]}),
        },
    }
    with pytest.raises(PermissionError):
        await coordinator._execute_tool(
            run["id"], owner, run, 0, time.monotonic() + 15, forged, tools
        )

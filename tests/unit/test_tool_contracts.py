from __future__ import annotations

from typing import Any

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.git_tools import GIT_TOOL
from xaa.runtime.tool_contracts import (
    COMMAND_TOOL_CONTRACT,
    PLAN_TOOL_CONTRACT,
    WORKSPACE_TOOL_CONTRACTS,
)
from xaa.storage.runs import RunStore


def test_workspace_contracts_declare_execution_boundaries() -> None:
    required = {
        "name",
        "version",
        "operation",
        "permissions",
        "effect",
        "input_schema",
        "output_schema",
        "timeout_seconds",
        "cancellation",
        "retry",
        "limits",
        "audit",
        "internal_only",
    }
    for external_name, contract in WORKSPACE_TOOL_CONTRACTS.items():
        assert required <= contract.keys()
        assert contract["name"].startswith("workspace.")
        assert contract["version"] == "1"
        assert contract["timeout_seconds"] == 10
        assert contract["permissions"]
        assert contract["effect"] in {"read", "create", "conditional_write", "delete"}
        assert contract["input_schema"]["type"] == "object"
        assert contract["output_schema"]["type"] == "object"
        assert contract["limits"]["max_output_bytes"] == 1_048_576
        assert {"run_id", "call_id", "tool", "version"} <= set(contract["audit"])
        if contract["effect"] != "read":
            assert "workspace.write" in contract["permissions"]
            assert "Never replay" in contract["retry"]
        if external_name == "workspace_delete_file":
            assert contract["internal_only"] is True
        else:
            assert contract["internal_only"] is False


def test_coordinator_exposes_workspace_schemas_from_contracts(workspace_tmp_path) -> None:
    store = RunStore(workspace_tmp_path / "state.sqlite3")
    run = store.create_run(
        objective="Review a workspace",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
    )
    current = store.get_run(run["id"])
    assert current is not None
    exposed = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in Coordinator(store, LMStudioProvider())._tools(run["id"], current)
    }
    for name, contract in WORKSPACE_TOOL_CONTRACTS.items():
        if contract["internal_only"]:
            continue
        assert exposed[name] == contract["input_schema"]
    assert exposed["plan_save"] == PLAN_TOOL_CONTRACT["input_schema"]


def test_plan_tool_contract_declares_durable_graph_write_boundaries() -> None:
    contract = PLAN_TOOL_CONTRACT
    assert contract["name"] == "plan.save"
    assert contract["version"] == "1"
    assert contract["permissions"] == ["task.delegate"]
    assert contract["effect"] == "persistent_plan_write"
    assert contract["input_schema"]["type"] == "object"
    assert len(contract["output_schema"]["oneOf"]) == 2
    assert contract["limits"]["tasks"] == 64
    assert "run_deadline" in contract["timeout"]["outer_deadline"]
    assert "No automatic retry" in contract["retry"]
    assert {"run_id", "call_id", "version", "task_ids"} <= set(contract["audit"])


def test_workspace_contract_schemas_are_serializable() -> None:
    import json

    encoded = json.dumps(WORKSPACE_TOOL_CONTRACTS, sort_keys=True)
    decoded: dict[str, Any] = json.loads(encoded)
    assert set(decoded) == set(WORKSPACE_TOOL_CONTRACTS)


def test_bounded_command_contract_declares_execution_boundaries() -> None:
    contract = COMMAND_TOOL_CONTRACT
    assert contract["name"] == "workspace.run_command"
    assert contract["version"] == "1"
    assert contract["permissions"] == ["workspace.command"]
    assert contract["effect"] == "ephemeral_sandbox_execution"
    assert contract["input_schema"]["type"] == "object"
    assert contract["output_schema"]["type"] == "object"
    assert contract["timeout"]["outer_deadline"] == "run_deadline"
    assert contract["timeout"]["cleanup_is_outside_execution_budget"] is True
    assert contract["limits"]["combined_output_bytes"] == 1_048_576
    assert contract["limits"]["command_seconds_max"] == 30
    assert "Never automatically replay" in contract["retry"]
    assert {"run_id", "call_id", "execution_id", "profile_sha256"} <= set(contract["audit"])


def test_git_tool_contract_declares_read_limits_and_audit() -> None:
    required = {
        "name",
        "version",
        "input_schema",
        "output_schema",
        "permissions",
        "effect",
        "timeout_seconds",
        "cancellation",
        "retry",
        "limits",
        "audit",
    }
    assert required <= GIT_TOOL.keys()
    assert GIT_TOOL["version"] == "2"
    assert GIT_TOOL["permissions"] == ["workspace.read"]
    assert GIT_TOOL["effect"] == "read"
    assert GIT_TOOL["timeout_seconds"] == 10
    assert GIT_TOOL["limits"] == {"excerpt_bytes": 4096, "artifact_bytes": 8 * 1024 * 1024}
    assert {"run_id", "call_id", "tool", "version", "artifact_sha256"} <= set(GIT_TOOL["audit"])

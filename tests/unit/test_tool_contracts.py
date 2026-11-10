from __future__ import annotations

from typing import Any

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.tool_contracts import COMMAND_TOOL_CONTRACT, WORKSPACE_TOOL_CONTRACTS
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

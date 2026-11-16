from __future__ import annotations

import json
from typing import Any

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.executor import (
    ReadFileOutput,
    WorkspaceCreateOutput,
    WorkspaceDeleteOutput,
    WorkspaceWriteOutput,
)
from xaa.runtime.git_tools import GIT_TOOL
from xaa.runtime.specialists import RUN_READY_CONTRACT, RUN_READY_TOOL, RunReadyResult
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.tool_contracts import (
    COMMAND_TOOL_CONTRACT,
    PLAN_TOOL_CONTRACT,
    WORKSPACE_TOOL_CONTRACTS,
)
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


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


def test_workspace_read_output_schema_matches_strict_runtime_model() -> None:
    contract = WORKSPACE_TOOL_CONTRACTS["workspace_read_file"]
    assert contract["output_schema"] == ReadFileOutput.model_json_schema()
    result = {
        "path": "README.md",
        "text": "untrusted bytes",
        "truncated": False,
        "trust": "untrusted_workspace_content",
        "sha256": "a" * 64,
    }
    assert ReadFileOutput.model_validate(result).model_dump(mode="json") == result


def test_workspace_mutation_output_schemas_match_strict_runtime_models() -> None:
    expected = {
        "workspace_create_file": WorkspaceCreateOutput,
        "workspace_apply_patch": WorkspaceWriteOutput,
        "workspace_replace_text": WorkspaceWriteOutput,
        "workspace_write_file": WorkspaceWriteOutput,
        "workspace_delete_file": WorkspaceDeleteOutput,
    }
    for name, model in expected.items():
        assert WORKSPACE_TOOL_CONTRACTS[name]["output_schema"] == model.model_json_schema()


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


def test_run_ready_contract_declares_bounded_specialist_execution() -> None:
    contract = RUN_READY_CONTRACT
    required = {
        "name",
        "version",
        "operation",
        "permissions",
        "effect",
        "data_access",
        "input_schema",
        "output_schema",
        "timeout",
        "cancellation",
        "retry",
        "limits",
        "audit",
    }
    assert required <= contract.keys()
    assert contract["name"] == "plan.run_ready"
    assert contract["version"] == "1"
    assert contract["permissions"] == ["task.delegate"]
    assert contract["effect"] == "bounded_specialist_execution"
    assert "private snapshots" in contract["data_access"]["workspace"]
    assert "review candidates" in contract["data_access"]["persistent"]
    assert contract["input_schema"]["type"] == "object"
    assert contract["output_schema"]["type"] == "object"
    assert contract["output_schema"]["properties"]["tasks"]["maxItems"] == 64
    assert contract["limits"]["tasks_per_call"] == 2
    assert contract["limits"]["max_output_bytes"] == 128 * 1024
    assert contract["timeout"]["outer_deadline"] == "run_deadline"
    assert "recovery" in contract["cancellation"]
    assert "No automatic task replay" in contract["retry"]
    assert {"run_id", "call_id", "tool", "version", "task_ids", "statuses"} <= set(
        contract["audit"]
    )
    worst_case = RunReadyResult(
        tasks=[{"task_id": "😀" * 128, "status": "queued"} for _ in range(64)],
        verification="Candidates require independent review.",
    ).model_dump(mode="json")
    assert (
        len(json.dumps(worst_case, ensure_ascii=True).encode("ascii"))
        <= contract["limits"]["max_output_bytes"]
    )


def test_coordinator_exposes_run_ready_schema_from_contract(workspace_tmp_path) -> None:
    store = RunStore(workspace_tmp_path / "run-ready.sqlite3")
    run = store.create_run(
        objective="Inspect a workspace",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
    )
    token = store.acquire_lease(run["id"], "test-worker")
    TaskStore(store).save_plan(
        run["id"],
        token,
        [TaskDefinition(id="inspect", objective="Read the fixture README", max_seconds=1)],
    )
    current = store.get_run(run["id"])
    assert current is not None
    exposed = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in Coordinator(store, LMStudioProvider())._tools(run["id"], current)
    }
    assert exposed[RUN_READY_TOOL["function"]["name"]] == RUN_READY_CONTRACT["input_schema"]


def test_workspace_contract_schemas_are_serializable() -> None:
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

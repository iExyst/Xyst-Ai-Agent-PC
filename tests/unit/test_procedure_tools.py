from __future__ import annotations

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.procedure_tools import (
    PROCEDURE_CATALOG_CONTRACT,
    PROCEDURE_CATALOG_RESULT_BYTES,
    PROCEDURE_READ_RESULT_BYTES,
    PROCEDURE_RESOURCE_READ_CONTRACT,
    PROCEDURE_VIEW_CONTRACT,
    ProcedureCatalogArguments,
    bounded_procedure_catalog,
    bounded_procedure_read_result,
)
from xaa.storage.runs import RunStore


def test_procedure_catalog_contract_declares_bounded_read() -> None:
    contract = PROCEDURE_CATALOG_CONTRACT
    assert contract["name"] == "procedure.catalog"
    assert contract["version"] == "1"
    assert contract["permissions"] == ["procedure.catalog.read"]
    assert contract["effect"] == "read"
    assert contract["input_schema"] == ProcedureCatalogArguments.model_json_schema()
    assert contract["output_schema"]["oneOf"][0]["properties"]["procedures"]["maxItems"] == 50
    assert contract["limits"]["result_bytes"] == PROCEDURE_CATALOG_RESULT_BYTES
    assert contract["timeout_seconds"] == 10
    assert "No automatic retry" in contract["retry"]
    assert {"run_id", "call_id", "tool", "version"} <= set(contract["audit"])


def test_procedure_catalog_schema_is_used_by_coordinator(workspace_tmp_path) -> None:
    store = RunStore(workspace_tmp_path / "procedure-tools.sqlite3")
    run = store.create_run(
        objective="Inspect procedure metadata",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )
    current = store.get_run(run["id"])
    assert current is not None
    exposed = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in Coordinator(store, LMStudioProvider())._tools(run["id"], current)
    }
    assert exposed["procedure_catalog"] == PROCEDURE_CATALOG_CONTRACT["input_schema"]


def test_procedure_catalog_result_falls_back_when_serialized_limit_is_exceeded() -> None:
    bounded = {"procedures": [], "next_offset": None, "selection": "explicit_user_only"}
    bounded["trust"] = "untrusted_procedure_metadata"
    assert bounded_procedure_catalog(bounded) is bounded

    oversized = {
        "procedures": [],
        "next_offset": None,
        "selection": "explicit_user_only",
        "trust": "untrusted_procedure_metadata",
        "description": "é" * PROCEDURE_CATALOG_RESULT_BYTES,
    }
    result = bounded_procedure_catalog(oversized)
    assert result["status"] == "output_limit"
    assert result["trust"] == "untrusted_procedure_metadata"


def test_selected_procedure_reads_enforce_final_serialized_output_limit() -> None:
    assert PROCEDURE_VIEW_CONTRACT["limits"]["result_bytes"] == PROCEDURE_READ_RESULT_BYTES
    assert PROCEDURE_RESOURCE_READ_CONTRACT["limits"]["result_bytes"] == (
        PROCEDURE_READ_RESULT_BYTES
    )
    oversized = {
        "content": "é" * PROCEDURE_READ_RESULT_BYTES,
        "trust": "untrusted_selected_procedure",
    }
    result = bounded_procedure_read_result(
        oversized,
        trust="untrusted_selected_procedure",
    )
    assert result["status"] == "output_limit"
    assert result["trust"] == "untrusted_selected_procedure"

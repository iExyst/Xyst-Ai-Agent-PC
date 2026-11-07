import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "live_task_workspaces_diagnostic",
    Path(__file__).resolve().parents[2] / "scripts" / "live_task_workspaces_diagnostic.py",
)
assert spec is not None and spec.loader is not None
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def inventory():
    return {
        "models": [
            {
                "key": "fixture",
                "loaded_instances": [
                    {"id": "fixture", "config": {"context_length": 2048}},
                    {"id": "fixture:2", "config": {"context_length": 4096}},
                ],
            }
        ]
    }


def test_selected_instance_context_checks_exact_identity():
    assert diagnostic.selected_instance_context(inventory(), "fixture", "fixture:2") == 4096
    with pytest.raises(ValueError, match="missing or ambiguous"):
        diagnostic.selected_instance_context(inventory(), "fixture", "fixture:3")
    duplicate = inventory()
    duplicate["models"][0]["loaded_instances"].append(
        {"id": "fixture:2", "config": {"context_length": 4096}}
    )
    with pytest.raises(ValueError, match="missing or ambiguous"):
        diagnostic.selected_instance_context(duplicate, "fixture", "fixture:2")


@pytest.mark.parametrize(
    "conservative,matching_limit", [(False, True), (True, True), (True, False)]
)
async def test_fixture_routes_selected_instance_to_saved_run_and_diagnostic(
    workspace_tmp_path, monkeypatch, conservative, matching_limit
):
    class Provider:
        base_url = "http://127.0.0.1:1234"

        def __init__(self, **kwargs):
            assert kwargs == {"exact_context": not conservative}
            self._context_limits = {"fixture:2": 4096 if matching_limit else 2048}

        def context_limit_for(self, instance):
            return self._context_limits.get(instance)

        async def models(self):
            return [SimpleNamespace(id="fixture")]

        async def close(self):
            pass

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, path):
            assert path == "/api/v1/models"
            return SimpleNamespace(raise_for_status=lambda: None, json=inventory)

    class Coordinator:
        def __init__(self, store, provider):
            self.store = store

        async def diagnose_run_tools(self, run_id, parent, model):
            assert model == "fixture:2"
            assert self.store.get_run(run_id)["model"] == model
            raise RuntimeError("stop after route verification")

    monkeypatch.setattr(Path, "cwd", lambda: workspace_tmp_path)
    monkeypatch.setattr(diagnostic, "LMStudioProvider", Provider)
    monkeypatch.setattr(diagnostic.httpx, "AsyncClient", Client)
    monkeypatch.setattr(diagnostic, "Coordinator", Coordinator)
    root = workspace_tmp_path / ".acceptance" / "routing"
    expected = (
        "stop after route verification"
        if matching_limit
        else "Conservative admission has no matching instance context limit"
    )
    with pytest.raises(RuntimeError, match=expected):
        await diagnostic.diagnose(
            root, "fixture", False, context_length=4096, conservative_context=conservative
        )
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    assert report["context_admission"] == ("conservative" if conservative else "exact_sdk")
    if not matching_limit:
        assert "run_id" not in report
        assert report["failure_stage"] == "model_inventory"
        return
    assert report["selected_instance_id"] == "fixture:2"
    assert report["selected_context_length"] == 4096
    assert report["task_model_routes"] == {"alpha": "fixture:2", "beta": "fixture:2"}
    assert report["failure_stage"] == "parent_tool_diagnostic"

"""Real model specialists edit private variants; explicit fixture review resolves integration conflicts."""

import argparse
import ast
import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.states import RunState
from xaa.runtime.task_commands import command_evidence
from xaa.runtime.task_graph import TaskDefinition
from xaa.runtime.task_integration import IntegrationResolution, preview
from xaa.runtime.task_review import TaskReviewDecision, candidate_evidence, review_candidate
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore
from xaa.storage.task_workspaces import TaskWorkspaces
from xaa.storage.tasks import TaskStore


def verify_literal_module(data: bytes, value: str) -> None:
    tree = ast.parse(data.decode("utf-8"))
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef)
    function = tree.body[0]
    assert function.name == "value" and not function.decorator_list and not function.args.args
    assert len(function.body) == 1 and isinstance(function.body[0], ast.Return)
    assert (
        isinstance(function.body[0].value, ast.Constant) and function.body[0].value.value == value
    )


def selected_instance_context(inventory: object, model: str, instance: str) -> int:
    """Verify the selected identifier and actual context in fresh native inventory."""
    if not isinstance(inventory, dict):
        raise TypeError("Native model inventory is invalid.")
    raw_models: object = cast(dict[str, Any], inventory).get("models")
    if not isinstance(raw_models, list):
        raise TypeError("Native model inventory has no model list.")
    matches: list[int] = []
    for entry in cast(list[object], raw_models):
        if not isinstance(entry, dict) or cast(dict[str, Any], entry).get("key") != model:
            continue
        instances: object = cast(dict[str, Any], entry).get("loaded_instances")
        if not isinstance(instances, list):
            raise TypeError("Native model inventory has no loaded instance list.")
        for loaded in cast(list[object], instances):
            if not isinstance(loaded, dict) or cast(dict[str, Any], loaded).get("id") != instance:
                continue
            config: object = cast(dict[str, Any], loaded).get("config")
            context: object = (
                cast(dict[str, Any], config).get("context_length")
                if isinstance(config, dict)
                else None
            )
            if type(context) is not int or context <= 0:
                raise ValueError("Selected instance has no valid context length.")
            matches.append(context)
    if len(matches) != 1:
        raise ValueError("Selected instance is missing or ambiguous in native inventory.")
    return matches[0]


async def diagnose(
    root: Path,
    model: str,
    prepare_only: bool,
    distribution: str | None = None,
    context_length: int = 16384,
    conservative_context: bool = False,
) -> None:
    root = root.resolve()
    root.relative_to((Path.cwd() / ".acceptance").resolve())
    project = root / "project"
    project.mkdir(parents=True, exist_ok=False)
    original = b'def value():\n    return "original"\n'
    (project / "shared.py").write_bytes(original)
    codes = {name: name + "-" + uuid4().hex for name in ("alpha", "beta")}
    validator = b"import runpy, sys\nvalue = runpy.run_path('shared.py')['value']()\nassert value == sys.argv[1], repr(value)\nprint('PRIVATE_VALIDATED ' + value)\n"
    grant = None
    if distribution is not None:
        transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)
        grant = {"distribution": distribution, "profile_sha256": transport.profile_sha256}
        (project / "validate.py").write_bytes(validator)
    store = RunStore(root / "data" / "XAA" / "xaa.sqlite3")
    run_id = ""
    parent = ""
    tasks = TaskStore(store)
    provider = LMStudioProvider(exact_context=not conservative_context)
    instance = None
    owned_instance = False
    report: dict[str, Any] = {
        "passed": False,
        "model": model,
        "root": str(root),
        "codes": codes,
        "scope": "Real source model specialists with private snapshot working trees; scripted fixture plan and explicitly automated review, no autonomous reviewer/native inference claim",
        "fixture_review_automated": not prepare_only,
        "prepare_only": prepare_only,
        "validation_distribution": distribution,
        "requested_context_length": context_length,
        "context_admission": "conservative" if conservative_context else "exact_sdk",
    }
    stage = "model_inventory"
    try:
        if not any(item.id == model for item in await provider.models()):
            raise RuntimeError("Selected model is not installed; no automatic download")
        async with httpx.AsyncClient(base_url=provider.base_url, timeout=10) as client:
            inventory_response = await client.get("/api/v1/models")
            inventory_response.raise_for_status()
        raw_inventory: object = inventory_response.json()
        if not isinstance(raw_inventory, dict):
            raise TypeError(
                "LM Studio native inventory is incomplete; refusing model lifecycle changes."
            )
        inventory = cast(dict[str, Any], raw_inventory)
        raw_models: object = inventory.get("models")
        if not isinstance(raw_models, list):
            raise TypeError(
                "LM Studio native inventory is incomplete; refusing model lifecycle changes."
            )
        model_entry: dict[str, Any] | None = None
        for raw_model_entry in cast(list[object], raw_models):
            if not isinstance(raw_model_entry, dict):
                continue
            typed_model_entry = cast(dict[str, Any], raw_model_entry)
            if typed_model_entry.get("key") == model:
                model_entry = typed_model_entry
                break
        if model_entry is None:
            raise RuntimeError(
                f"LM Studio native inventory omitted {model!r}; refusing model lifecycle changes."
            )
        raw_loaded: object = model_entry.get("loaded_instances")
        if not isinstance(raw_loaded, list):
            raise TypeError(
                f"LM Studio native inventory omitted loaded instances for {model!r}; refusing model lifecycle changes."
            )
        loaded: list[dict[str, Any]] = []
        for item in cast(list[object], raw_loaded):
            if isinstance(item, dict):
                loaded.append(cast(dict[str, Any], item))
        existing: dict[str, Any] | None = None
        for item in loaded:
            raw_config: object = item.get("config")
            config = cast(dict[str, Any], raw_config) if isinstance(raw_config, dict) else {}
            if isinstance(item.get("id"), str) and config.get("context_length") == context_length:
                existing = item
                break
        if existing is not None:
            instance_id = existing.get("id")
            assert isinstance(instance_id, str)
            instance = instance_id
            report["reused_preloaded_instance"] = True
        elif loaded:
            contexts: list[object] = []
            for item in loaded:
                raw_config = item.get("config")
                if isinstance(raw_config, dict):
                    contexts.append(cast(dict[str, Any], raw_config).get("context_length"))
            raise RuntimeError(
                f"{model} is already loaded at contexts {contexts}; refusing to load a second instance at {context_length}."
            )
        else:
            instance = await provider.load_model(model, context_length=context_length)
            owned_instance = True
            report["reused_preloaded_instance"] = False
        await provider.models()
        async with httpx.AsyncClient(base_url=provider.base_url, timeout=10) as client:
            verification_response = await client.get("/api/v1/models")
            verification_response.raise_for_status()
        actual_context = selected_instance_context(verification_response.json(), model, instance)
        if actual_context != context_length:
            raise RuntimeError("Selected instance context differs from requested context.")
        if conservative_context and provider.context_limit_for(instance) != actual_context:
            raise RuntimeError("Conservative admission has no matching instance context limit.")
        report["selected_instance_id"] = instance
        report["selected_context_length"] = actual_context
        stage = "fixture_setup"
        run_id = store.create_run(
            objective="Compare alternative implementations in private specialist copies and explicitly choose which version to integrate.",
            acceptance_criteria=[],
            workspace=str(project),
            mode="workspace",
            model=instance,
            max_iterations=16,
            max_seconds=600,
            max_output_tokens=512,
            command_grant=grant,
        )["id"]
        report["run_id"] = run_id
        acquired = store.acquire_lease(run_id, "specialist-variant-fixture", seconds=600)
        assert acquired is not None
        parent = acquired
        tasks = TaskStore(store)
        tasks.save_plan(
            run_id,
            parent,
            [
                TaskDefinition(
                    id=name,
                    objective=f"Use the portable relative path 'shared.py' exactly for every file tool; never add a leading slash or use an absolute path. Read shared.py, then use workspace_write_lines with separate source lines and final_newline true to change value() so it returns exactly {code!r}. Keep only this one function in the module. Copy the sha256 from the read result. Your changes belong only in your private working tree. Supply the actual Python file text: after JSON decoding, content must have real line breaks and normal Python quotes, not literal backslash-n sequences or extra backslashes before quotes. After writing, read shared.py once to check the actual saved text. If that read confirms the requested text and value, do not write again. "
                    + (
                        f"Then call workspace_run_command with argv ['/usr/bin/python3', '-I', 'validate.py', '{code}'] and max_seconds 15 to run the provided validator against your private edits. The provided validate.py is already in your private tree; do not list directories, inspect it, or alter it. Inspect the actual exit code and output. On success, immediately submit the candidate answer with that evidence and no further tool calls. On failure, fix shared.py and rerun validation within the saved allowance. "
                        if distribution
                        else ""
                    )
                    + "Finally submit a candidate naming shared.py and its new return value. Do not create other files or repeat completed writes.",
                    acceptance_criteria=(
                        f"Private shared.py defines value() returning exactly {code}",
                    ),
                    role="developer",
                    permissions=("workspace.read", "workspace.write", "workspace.command")
                    if distribution
                    else ("workspace.read", "workspace.write"),
                    max_iterations=8,
                    max_seconds=300,
                    max_output_tokens=512,
                )
                for name, code in codes.items()
            ],
        )
        task_routes = {row["local_id"]: row["model_route"] for row in tasks.records(run_id)}
        if set(task_routes) != set(codes) or any(
            route != instance for route in task_routes.values()
        ):
            raise RuntimeError("Specialist routes do not match the selected loaded instance.")
        report["task_model_routes"] = task_routes
        stage = "parent_tool_diagnostic"
        store.transition(run_id, RunState.PLANNING, owner=parent)
        await Coordinator(store, provider).diagnose_run_tools(run_id, parent, instance)
        store.transition(run_id, RunState.RUNNING, owner=parent)
        stage = "specialist_execution"
        statuses = await SpecialistRunner(store, provider).run_ready(
            run_id, parent, deadline=time.monotonic() + 360
        )
        assert len(statuses) == 2 and all(item["status"] == "verifying" for item in statuses), (
            statuses
        )
        proposed: dict[str, str] = {}
        for name, code in codes.items():
            with store.connection() as connection:
                _, files = TaskWorkspaces(store).view(connection, run_id, name)
            assert set(files) == ({"shared.py", "validate.py"} if distribution else {"shared.py"})
            verify_literal_module(files["shared.py"], code)
            proposed[name] = hashlib.sha256(files["shared.py"]).hexdigest()
            if distribution:
                assert files["validate.py"] == validator
                evidence = command_evidence(store, run_id, name)
                assert any(
                    item["succeeded"]
                    and item["matches_current_private_tree"]
                    and item["argv"] == ["/usr/bin/python3", "-I", "validate.py", code]
                    and "PRIVATE_VALIDATED " + code in item["stdout_excerpt"]
                    for item in evidence
                )
        assert (project / "shared.py").read_bytes() == original
        assert store.conversation_messages(run_id) == [] and not store.unresolved_tool_calls(run_id)
        store.transition(run_id, RunState.VERIFYING, owner=parent)
        store.release_lease(run_id, parent)
        report.update(
            statuses=statuses,
            proposed_sha256=proposed,
            original_unchanged_before_review=True,
            private_variants_independently_validated=True,
            private_command_validation_verified=distribution is not None,
        )
        if not prepare_only:

            def decision(
                name: str, resolutions: list[IntegrationResolution] | None = None
            ) -> TaskReviewDecision:
                evidence = candidate_evidence(store, run_id, name)["evidence"]
                return TaskReviewDecision(
                    decision="accept",
                    rationale="Automated fixture review: private AST and complete diff independently inspected; explicit selected version for integration.",
                    confirmed_criteria=[0],
                    evidence_call_ids=[evidence[0]["call_id"]],
                    changes_confirmed=True,
                    resolutions=resolutions or [],
                    validation_call_ids=[
                        item["call_id"]
                        for item in command_evidence(store, run_id, name)
                        if item["succeeded"] and item["matches_current_private_tree"]
                    ]
                    if distribution
                    else [],
                )

            await review_candidate(store, run_id, "alpha", decision("alpha"))
            verify_literal_module((project / "shared.py").read_bytes(), codes["alpha"])
            refused = False
            try:
                await review_candidate(store, run_id, "beta", decision("beta"))
            except ValueError as error:
                assert "conflict" in str(error).lower()
                refused = True
            assert refused
            verify_literal_module((project / "shared.py").read_bytes(), codes["alpha"])
            owner = store.acquire_lease(run_id, "preview-fixture", seconds=180)
            assert owner is not None
            snapshot = await preview(store, run_id, "beta", owner)
            store.release_lease(run_id, owner)
            assert snapshot.changes[0].conflict
            await review_candidate(
                store,
                run_id,
                "beta",
                decision(
                    "beta",
                    [
                        IntegrationResolution(
                            path="shared.py",
                            expected_current_sha256=snapshot.changes[0].current_sha256,
                            choice="proposed",
                        )
                    ],
                ),
            )
            verify_literal_module((project / "shared.py").read_bytes(), codes["beta"])
            assert all(item["status"] == "completed" for item in tasks.records(run_id))
            report.update(
                conflict_refused_without_choice=True,
                chosen_beta_integrated=True,
                final_module_static_validation_passed=True,
                task_statuses=[
                    {"task_id": row["local_id"], "status": row["status"]}
                    for row in tasks.records(run_id)
                ],
            )
        assert store.conversation_messages(run_id) == [] and not store.unresolved_tool_calls(run_id)
        report.update(
            passed=True,
            parent_completion_claimed=False,
            unresolved_calls=0,
            parent_history_unchanged=True,
            usage=store.token_usage(run_id),
        )
    except Exception as exc:
        current_run = store.get_run(run_id) if run_id else None
        task_results: list[dict[str, Any]] = []
        for task in tasks.records(run_id) if run_id else []:
            raw_result = task.get("result")
            try:
                result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
            except json.JSONDecodeError:
                result = {"unparsed_result": str(raw_result)[:512]}
            task_results.append(
                {"task_id": task["local_id"], "status": task["status"], "result": result}
            )
        report.update(
            failure_stage=stage,
            error_type=type(exc).__name__,
            error_message=str(exc)[:512],
            run_state=current_run["state"] if current_run else None,
            unresolved_calls=len(store.unresolved_tool_calls(run_id)) if run_id else 0,
            task_results=task_results,
        )
        admission_failures = [
            event["payload"]
            for event in (store.list_events(run_id) if run_id else [])
            if event["event_type"] == "context.admission_failed"
        ]
        if admission_failures:
            report["context_admission_failures"] = admission_failures[-8:]
        if isinstance(exc.__cause__, httpx.HTTPStatusError):
            response = exc.__cause__.response
            report["provider_http_status"] = response.status_code
            try:
                body: object = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                error: object = cast(dict[str, Any], body).get("error")
                if isinstance(error, dict):
                    detail = cast(dict[str, Any], error).get("message")
                else:
                    detail = error
                if isinstance(detail, str):
                    report["provider_error_detail"] = detail[:512]
        raise
    finally:
        if run_id and parent:
            store.release_lease(run_id, parent)
        try:
            if owned_instance and instance is not None:
                await provider.unload_model(instance)
                report["owned_model_unloaded"] = True
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            print(f"Private specialist workspace evidence: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(".acceptance") / str(uuid4()))
    parser.add_argument("--model", default="qwen/qwen3.5-9b")
    parser.add_argument("--context-length", type=int, default=16384)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument(
        "--conservative-context",
        action="store_true",
        help="Use the default conservative context admission instead of exact SDK counting.",
    )
    parser.add_argument(
        "--distribution", help="Explicit installed WSL distribution for private validation"
    )
    args = parser.parse_args()
    asyncio.run(
        diagnose(
            args.root,
            args.model,
            args.prepare_only,
            args.distribution,
            args.context_length,
            args.conservative_context,
        )
    )

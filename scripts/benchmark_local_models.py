"""Small repeatable real-model workspace benchmark; never selects a production default."""

import argparse
import asyncio
import ctypes
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from xaa.providers.contracts import TokenUsage, validate_completion
from xaa.providers.lm_studio import AdmissionCallback, LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.directory_listing import ListDirectoryArguments
from xaa.runtime.executor import (
    ApplyPatchArguments,
    CreateFileArguments,
    ReadFileArguments,
    ReplaceTextArguments,
    WriteFileArguments,
)
from xaa.runtime.git_tools import GitArguments
from xaa.runtime.planning import SavePlanArguments
from xaa.runtime.specialists import RunReadyArguments
from xaa.runtime.workspace_search import SearchArguments
from xaa.storage.runs import RunStore


class Echo(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    value: str


SCHEMAS: dict[str, type[BaseModel]] = {
    "xaa_echo": Echo,
    "workspace_read_file": ReadFileArguments,
    "workspace_replace_text": ReplaceTextArguments,
    "workspace_write_file": WriteFileArguments,
    "workspace_create_file": CreateFileArguments,
    "workspace_apply_patch": ApplyPatchArguments,
    "workspace_list_directory": ListDirectoryArguments,
    "workspace_search": SearchArguments,
    "workspace_git_inspect": GitArguments,
    "plan_save": SavePlanArguments,
    "plan_run_ready": RunReadyArguments,
}


class MeasuredProvider(LMStudioProvider):
    def __init__(self, address: str) -> None:
        super().__init__(address)
        self.latencies: list[float] = []
        self.proposed = self.valid = self.invalid_envelopes = self.request_errors = 0
        self.tokens = 0
        self.unmeasured_usage = 0

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        on_admission: AdmissionCallback | None = None,
        require_exact_context: bool = False,
    ) -> dict[str, Any]:
        started = time.monotonic()
        try:
            response = await super().chat(
                model=model,
                messages=messages,
                tools=tools,
                max_output_tokens=max_output_tokens,
                on_admission=on_admission,
                require_exact_context=require_exact_context,
            )
            try:
                choice = validate_completion(response)
                allowed = {tool["function"]["name"] for tool in tools or []}
                for call in choice.message.tool_calls or []:
                    self.proposed += 1
                    try:
                        SCHEMAS[call.function.name].model_validate_json(call.function.arguments)
                        self.valid += call.function.name in allowed
                    except (ValueError, KeyError):
                        pass  # Recorded as a schema-invalid/unavailable proposal, not silently accepted.
                if response.get("usage") is not None:
                    self.tokens += TokenUsage.model_validate(response["usage"]).total_tokens
                else:
                    self.unmeasured_usage += 1
            except (ValueError, KeyError, TypeError):
                self.invalid_envelopes += 1
            return response
        except Exception:
            self.request_errors += 1
            raise
        finally:
            self.latencies.append(time.monotonic() - started)


class MemoryStatus(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("load", ctypes.c_uint32),
        *[
            (name, ctypes.c_uint64)
            for name in [
                "total_physical",
                "available_physical",
                "total_page",
                "available_page",
                "total_virtual",
                "available_virtual",
                "extended",
            ]
        ],
    ]


def resources() -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "scope": "host-wide point-in-time; not process-attributed or peak usage"
    }
    if os.name == "nt":
        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        memory = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
        memory.argtypes = [ctypes.POINTER(MemoryStatus)]
        memory.restype = ctypes.c_int
        if memory(ctypes.byref(status)):
            snapshot["physical_memory_total_bytes"] = status.total_physical
            snapshot["physical_memory_used_bytes"] = (
                status.total_physical - status.available_physical
            )
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,utilization.gpu,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        snapshot["nvidia_gpu_rows"] = result.stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        snapshot["gpu_sample"] = "unavailable"
    return snapshot


async def trial(root: Path, model: str, case: str, repetition: int, address: str) -> dict[str, Any]:
    folder = root / f"trial-{uuid4()}"
    workspace = folder / "workspace"
    workspace.mkdir(parents=True)
    nonce = f"FIXTURE-{uuid4().hex}"
    initial = (nonce + "\nvalue=1\n").encode()
    expected = (nonce + "\nvalue=2\n").encode() if case == "targeted_edit" else initial
    target = workspace / "input.txt"
    target.write_bytes(initial)
    (workspace / "neighbor.txt").write_bytes(b"preserve")
    store = RunStore(folder / "runs.sqlite3")
    objective = "Read input.txt with workspace_read_file and report the exact FIXTURE code in it. Do not edit files."
    if case == "targeted_edit":
        objective = "Read input.txt with workspace_read_file. Use workspace_replace_text to replace the unique old_text value=1 with new_text value=2 using the returned sha256. Preserve all other bytes, including the FIXTURE code and newlines. Report the result."
    run = store.create_run(
        objective=objective,
        acceptance_criteria=["Exact fixture result and preserved neighbor"],
        workspace=str(workspace),
        mode="workspace" if case == "targeted_edit" else "observe",
        model=model,
        max_iterations=6,
        max_seconds=45,
        file_checks=[{"path": "input.txt", "expected_content": expected.decode()}]
        if case == "targeted_edit"
        else [],
    )
    provider = MeasuredProvider(address)
    before = await asyncio.to_thread(resources)
    error = None
    started = time.monotonic()
    try:
        await provider.models()
        await Coordinator(store, provider).execute(run["id"])
    except Exception as exc:  # noqa: BLE001 - benchmark records failed trials and continues without fallback
        error = type(exc).__name__
    finally:
        await provider.close()
    duration = time.monotonic() - started
    events = store.list_events(run["id"])
    history = store.conversation_messages(run["id"])
    requested_tool = "workspace_replace_text" if case == "targeted_edit" else "workspace_read_file"
    requested_ids = {
        call["id"]
        for message in history
        for call in message.get("tool_calls", [])
        if call["function"]["name"] == requested_tool
    }
    follows_tool = any(
        event["event_type"] == "tool.completed" and event["payload"].get("call_id") in requested_ids
        for event in events
    )
    actual = target.read_bytes()
    saved = store.get_run(run["id"])
    assert saved is not None
    answer = history[-1].get("content", "") if history else ""
    passed = bool(
        saved["state"] == "verifying"
        and actual == expected
        and (workspace / "neighbor.txt").read_bytes() == b"preserve"
        and follows_tool
        and (case != "read_nonce" or nonce in answer)
        and not store.unresolved_tool_calls(run["id"])
    )
    return {
        "model": model,
        "case": case,
        "repetition": repetition,
        "passed": passed,
        "state": saved["state"],
        "error_class": error,
        "last_state_reason": next(
            (
                event["payload"].get("reason")
                for event in reversed(events)
                if event["event_type"] == "run.state_changed"
            ),
            None,
        ),
        "run_id": run["id"],
        "evidence": str(folder),
        "wall_seconds": duration,
        "request_wall_seconds": provider.latencies,
        "requests_including_diagnostic": len(provider.latencies),
        "schema_valid_tool_proposals": provider.valid,
        "parseable_tool_proposals": provider.proposed,
        "invalid_completion_envelopes": provider.invalid_envelopes,
        "request_errors": provider.request_errors,
        "tokens_including_diagnostic": provider.tokens,
        "responses_without_measured_usage": provider.unmeasured_usage,
        "exact_file_bytes": actual == expected,
        "requested_tool_used": follows_tool,
        "host_before": before,
        "host_after": await asyncio.to_thread(resources),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models", nargs="+", default=["qwen2.5-0.5b-instruct", "qwen3-0.6b", "qwen/qwen3.5-9b"]
    )
    parser.add_argument("--repetitions", type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 10:
        raise ValueError("Use 1 through 10 repetitions.")
    address = os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234")
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    loader = LMStudioProvider(address)
    trials: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "benchmark_complete": False,
        "production_default_selected": False,
        "context_length": 16384,
        "trial_limits": {"iterations": 6, "seconds": 45},
        "hardware": {"platform": platform.platform(), "processor": platform.processor()},
        "trials": trials,
        "limitations": [
            "Small fixture sample, no production-default recommendation.",
            "No streaming, cancellation/recovery, complex coding or multi-agent reliability coverage.",
            "Host resource snapshots are not per-process attribution or peak measurements.",
            "Schema validity measures parseable proposals; invalid completion envelopes are reported separately.",
            "Request timing/token metrics include each trial's fresh harmless capability diagnostic; load time is separate.",
        ],
    }

    def save() -> None:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    save()
    try:
        installed = {model.id for model in await loader.models()}
        if set(args.models) - installed:
            raise ValueError(
                "Benchmark requested a model not already installed; no downloads are allowed."
            )
        for model in args.models:
            instance = None
            load_started = time.monotonic()
            try:
                instance = await loader.load_model(model, context_length=16384)
                load_seconds = time.monotonic() - load_started
                for repetition in range(1, args.repetitions + 1):
                    for case in ["read_nonce", "targeted_edit"]:
                        result = await trial(root, model, case, repetition, address)
                        result["model_load_seconds"] = load_seconds
                        trials.append(result)
                        save()
                        print(
                            json.dumps(
                                {
                                    key: result[key]
                                    for key in [
                                        "model",
                                        "case",
                                        "repetition",
                                        "passed",
                                        "state",
                                        "wall_seconds",
                                        "error_class",
                                    ]
                                }
                            ),
                            flush=True,
                        )
            finally:
                if instance is not None:
                    await loader.unload_model(instance)
        summary: list[dict[str, Any]] = []
        for model in args.models:
            rows = [row for row in trials if row["model"] == model]
            latencies = [value for row in rows for value in row["request_wall_seconds"]]
            proposals = sum(row["parseable_tool_proposals"] for row in rows)
            valid = sum(row["schema_valid_tool_proposals"] for row in rows)
            summary.append(
                {
                    "model": model,
                    "completed": sum(row["passed"] for row in rows),
                    "trials": len(rows),
                    "schema_valid_tool_proposals": valid,
                    "parseable_tool_proposals": proposals,
                    "invalid_completion_envelopes": sum(
                        row["invalid_completion_envelopes"] for row in rows
                    ),
                    "median_request_wall_seconds": statistics.median(latencies)
                    if latencies
                    else None,
                }
            )
        report.update(benchmark_complete=True, summary=summary)
        save()
        print(
            json.dumps({"benchmark_complete": True, "summary": summary, "evidence": str(root)}),
            flush=True,
        )
    finally:
        await loader.close()


if __name__ == "__main__":
    asyncio.run(main())

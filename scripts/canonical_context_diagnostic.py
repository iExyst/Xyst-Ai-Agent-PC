"""Audit SDK count parity and public-message persistence through a real workspace run."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import AdmissionCallback, LMStudioProvider
from xaa.providers.tokenization import PromptCount, PromptCountRequest
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


class AuditedProvider(LMStudioProvider):
    def __init__(self) -> None:
        super().__init__(exact_context=True)
        self.rows: list[dict[str, Any]] = []
        self.extra_fields: set[str] = set()
        self.last_count: PromptCount | None = None

    async def _count_prompt_owned(self, request: PromptCountRequest) -> PromptCount:
        self.last_count = await super()._count_prompt_owned(request)
        return self.last_count

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
        response = await super().chat(
            model=model,
            messages=messages,
            tools=tools,
            max_output_tokens=max_output_tokens,
            on_admission=on_admission,
            require_exact_context=require_exact_context,
        )
        counted = self.last_count
        assert counted is not None
        raw = response["choices"][0]["message"]
        self.extra_fields.update(set(raw) - {"role", "content", "tool_calls"})
        actual = response["usage"]["prompt_tokens"]
        self.rows.append(
            {
                "request_index": len(self.rows) + 1,
                "sdk_prompt_tokens": counted.prompt_tokens,
                "http_prompt_tokens": actual,
                "match": counted.prompt_tokens == actual,
            }
        )
        if counted.prompt_tokens != actual:
            raise RuntimeError("SDK/HTTP prompt count mismatch in canonical run.")
        return response


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    nonce = "CANONICAL-" + uuid4().hex
    fixture = root / "input.txt"
    original = (nonce + "\n").encode()
    fixture.write_bytes(original)
    stamp = fixture.stat().st_mtime_ns
    store = RunStore(root / "runs.sqlite3")
    provider = AuditedProvider()
    instance = None
    run_id: str | None = None
    report: dict[str, Any] = {
        "passed": False,
        "model": "qwen/qwen3.5-9b",
        "scope": "automatic SDK admission and durable inference-linked token holds",
        "run_deadline_seconds": 240,
    }
    try:
        await provider.models()
        instance = await provider.load_model("qwen/qwen3.5-9b", context_length=32768)
        run = store.create_run(
            objective="Read input.txt using workspace_read_file and report the exact CANONICAL code in it. Do not save or run a plan.",
            acceptance_criteria=["Exact code, unchanged file"],
            workspace=str(root),
            mode="observe",
            model=instance,
            max_iterations=4,
            max_seconds=240,
            max_output_tokens=1024,
            max_total_tokens=20000,
        )
        current_run_id = run["id"]
        run_id = current_run_id
        report["run_id"] = current_run_id
        try:
            await Coordinator(store, provider).execute(current_run_id)
        except Exception as exc:  # noqa: BLE001 - acceptance reports bounded failure classes
            # Preserve a useful bounded acceptance result when model inference or
            # the tool diagnostic fails, instead of leaving only a traceback.
            mismatch = any(row["match"] is False for row in provider.rows)
            report["failure_type"] = (
                "SDKHTTPPromptCountMismatch" if mismatch else type(exc).__name__
            )
            report["failure_stage"] = "coordinator_execution"
        events = store.list_events(run["id"])
        event_types = [event["event_type"] for event in events]
        if "failure_type" in report:
            report["failure_stage"] = (
                "normal_model_prompt_count_parity"
                if any(row["match"] is False for row in provider.rows)
                else "harmless_tool_diagnostic_inference"
                if "diagnostic.inference.started" in event_types
                and "diagnostic.inference.completed" not in event_types
                else "context_admission"
                if "context.admission_failed" in event_types
                else "coordinator_execution"
            )
        messages = store.conversation_messages(run["id"])
        persisted = [
            e["payload"]["message"]
            for e in events
            if e["event_type"] in {"model.message", "task.model.message"}
        ]
        public_only = all(set(m) <= {"role", "content", "tool_calls"} for m in persisted)
        reservations = [e["payload"] for e in events if e["event_type"] == "tokens.reserved"]
        assert len(reservations) == len(provider.rows)
        saved = store.get_run(run["id"])
        assert saved is not None
        report.update(
            state=saved["state"],
            counts=provider.rows,
            durable_reservations=len(reservations),
            reservation_events=[
                event["payload"] for event in events if event["event_type"] == "tokens.reserved"
            ],
            event_types=event_types,
            raw_nonpublic_field_names=sorted(provider.extra_fields),
            new_journal_messages_public_only=public_only,
            fixture_bytes_unchanged=fixture.read_bytes() == original,
            fixture_mtime_unchanged=fixture.stat().st_mtime_ns == stamp,
        )
        report["passed"] = (
            saved["state"] == "verifying"
            and nonce in (messages[-1].get("content") or "")
            and public_only
            and "reasoning_content" in provider.extra_fields
            and any(e["event_type"] == "tool.completed" for e in events)
            and report["fixture_bytes_unchanged"]
            and report["fixture_mtime_unchanged"]
            and not store.unresolved_tool_calls(run["id"])
        )
        if not report["passed"]:
            raise SystemExit(1)
    finally:
        report["counts"] = provider.rows
        if run_id is not None:
            saved = store.get_run(run_id)
            events = store.list_events(run_id)
            report.setdefault("state", saved["state"] if saved is not None else None)
            report.setdefault(
                "reservation_events",
                [event["payload"] for event in events if event["event_type"] == "tokens.reserved"],
            )
            report.setdefault("event_types", [event["event_type"] for event in events])
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()
            (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

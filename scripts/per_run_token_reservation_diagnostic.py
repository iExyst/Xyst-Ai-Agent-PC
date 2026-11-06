"""Real LM Studio vertical slice for run-scoped prompt reservations.

Runs one simple no-workspace answer through Coordinator with process-wide exact
context disabled, and records sanitized reservation/count evidence in .acceptance.
"""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.providers.tokenization import PromptCount, PromptCountRequest
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


class AuditedProvider(LMStudioProvider):
    def __init__(self) -> None:
        super().__init__(exact_context=False)
        self.counts: list[PromptCount] = []

    async def _count_prompt_owned(self, request: PromptCountRequest) -> PromptCount:
        result = await super()._count_prompt_owned(request)
        self.counts.append(result)
        return result


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    store = RunStore(root / "runs.sqlite3")
    provider = AuditedProvider()
    instance: str | None = None
    report: dict[str, Any] = {
        "passed": False,
        "process_wide_exact_context": False,
        "run_scoped_reservations": True,
        "model_family": "Qwen3.5 9B",
        "workspace_attached": False,
    }
    try:
        instance = await provider.load_model("qwen/qwen3.5-9b", context_length=8192)
        run = store.create_run(
            objective="Reply with exactly RESERVED_OK and no other text.",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=instance,
            max_iterations=3,
            max_seconds=90,
            max_total_tokens=10_000,
            max_output_tokens=512,
            pre_inference_token_reservations=True,
        )
        run_id = run["id"]
        await Coordinator(store, provider).execute(run_id)
        events = store.list_events(run_id)
        started = {
            event["payload"]["inference_id"]
            for event in events
            if event["event_type"] in {"model.inference.started", "task.inference.started"}
        }
        reservations = {
            event["payload"]["inference_id"]: event["payload"]["reserved_tokens"]
            for event in events
            if event["event_type"] == "tokens.reserved"
        }
        usage = store.usage_report(run_id)
        saved = store.get_run(run_id)
        passed = (
            len(provider.counts) >= 1
            and started == set(reservations)
            and all(amount > 0 for amount in reservations.values())
            and usage["outstanding_reservations"] == 0
            and usage["total_tokens"] > 0
            and saved is not None
            and saved["state"] in {"completed", "verifying"}
        )
        report.update(
            passed=passed,
            run_id=run_id,
            final_state=saved["state"] if saved else None,
            inference_count=len(started),
            sdk_count_count=len(provider.counts),
            all_inferences_reserved=started == set(reservations),
            reservation_count=len(reservations),
            reserved_tokens=list(reservations.values()),
            measured_tokens=usage["total_tokens"],
            outstanding_reservations=usage["outstanding_reservations"],
            sdk_prompt_counts=[count.prompt_tokens for count in provider.counts],
            sdk_context_lengths=[count.context_length for count in provider.counts],
        )
        if not passed:
            raise RuntimeError("Per-run live reservation acceptance did not satisfy its checks.")
    finally:
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()
            report_path = root / "report.json"
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({**report, "evidence": str(report_path.parent)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

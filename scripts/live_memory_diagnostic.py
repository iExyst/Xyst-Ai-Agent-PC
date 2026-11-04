"""Verify explicit saved-preference recall and deletion with a real local model."""

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.memory import MemoryStore
from xaa.storage.runs import RunStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    store = RunStore(root / "memory.sqlite3")
    memories = MemoryStore(store)
    label = f"XAA-{uuid4().hex}"
    preference = memories.save_preference(
        "local-user",
        f"My saved verification label is {label}. When asked for my saved verification label, report it exactly.",
    )
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance = None
    answers: list[str] = []
    states: list[str] = []
    recalled_ids: list[list[str]] = []
    try:
        instance = await provider.load_model(model, context_length=16384)
        for index in range(2):
            if index:
                memories.delete("local-user", preference["id"])
            run = store.create_run(
                objective="What is my saved verification label? If none is provided, say it is unavailable.",
                acceptance_criteria=[],
                workspace=None,
                mode="observe",
                model=model,
            )
            await Coordinator(store, provider).execute(run["id"])
            saved = store.get_run(run["id"])
            states.append(saved["state"] if saved else "missing")
            events = store.list_events(run["id"])
            model_messages = [
                event["payload"]["message"]
                for event in events
                if event["event_type"] == "model.message"
            ]
            answers.append(model_messages[-1].get("content") or "" if model_messages else "")
            recalled_ids.append(
                [
                    identity
                    for event in events
                    if event["event_type"] == "memory.recalled"
                    for identity in event["payload"]["ids"]
                ]
            )
        passed = (
            label in answers[0]
            and label not in answers[1]
            and "unavailable" in answers[1].lower()
            and states == ["verifying", "verifying"]
            and recalled_ids == [[preference["id"]], []]
            and not memories.search("local-user", label)
        )
        report = {
            "passed": passed,
            "model": model,
            "saved_label": label,
            "before_delete": answers[0],
            "after_delete": answers[1],
            "states": states,
            "recalled_ids": recalled_ids,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))
        if not passed:
            raise RuntimeError("Live preference recall/deletion acceptance failed.")
    finally:
        if instance is not None:
            await provider.unload_model(instance)
        await provider.close()


if __name__ == "__main__":
    asyncio.run(main())

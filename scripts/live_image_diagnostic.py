"""Verify real LM Studio image perception through the persisted run path."""

import asyncio
import base64
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path
from uuid import uuid4

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def make_fixture_png() -> bytes:
    """Build a plain diagram with two red circles and one blue square."""
    width, height = 160, 100
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            color = (255, 255, 255)
            if (x - 42) ** 2 + (y - 50) ** 2 <= 16**2 or (x - 82) ** 2 + (y - 50) ** 2 <= 16**2:
                color = (220, 35, 35)
            elif 116 <= x < 146 and 35 <= y < 65:
                color = (35, 70, 220)
            pixels.extend(color)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(pixels), level=9))
        + _png_chunk(b"IEND", b"")
    )


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    image = make_fixture_png()
    image_path = root / "two-red-circles.png"
    image_path.write_bytes(image)
    store = RunStore(root / "image.sqlite3")
    model = os.environ.get("LM_STUDIO_MODEL", "qwen/qwen3.5-9b")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance: str | None = None
    report: dict[str, object] = {
        "passed": False,
        "model": model,
        "fixture": str(image_path),
        "fixture_bytes": len(image),
        "fixture_sha256": hashlib.sha256(image).hexdigest(),
        "evidence": str(root),
    }
    try:
        discovered = await provider.models()
        selected = next((candidate for candidate in discovered if candidate.id == model), None)
        if selected is None:
            raise RuntimeError("Configured vision model is not present in LM Studio discovery.")
        report["discovered_vision"] = selected.capabilities.vision
        if not selected.capabilities.vision:
            raise RuntimeError("LM Studio does not advertise vision for the selected model.")
        instance = await provider.load_model(model, context_length=16384)
        run = store.create_run(
            objective="Inspect the attached image. Count the red circles. Reply with one short line giving the count and color, with no explanation.",
            acceptance_criteria=[
                "Report the visible number of red circles and identify them as red."
            ],
            workspace=None,
            mode="observe",
            model=model,
            max_iterations=2,
            max_seconds=180,
            max_output_tokens=512,
            image_bytes=image,
            image_media_type="image/png",
        )
        await Coordinator(store, provider).execute(run["id"])
        saved = store.get_run(run["id"])
        history = store.conversation_messages(run["id"])
        answer = history[-1].get("content", "") if history else ""
        calls = [
            call["function"]["name"]
            for message in history
            for call in message.get("tool_calls", [])
        ]
        restored = store.image_data_url(run["id"])
        restored_bytes = (
            base64.b64decode(restored.split(",", 1)[1], validate=True) if restored else b""
        )
        normalized = answer.casefold()
        count_present = "two" in normalized or "2" in normalized
        passed = (
            saved is not None
            and saved["state"] == "verifying"
            and count_present
            and "red" in normalized
            and "circle" in normalized
            and not calls
            and restored_bytes == image
            and not store.unresolved_tool_calls(run["id"])
        )
        report.update(
            passed=passed,
            run_id=run["id"],
            run_state=saved["state"] if saved else None,
            max_output_tokens=512,
            answer=answer,
            tool_calls=calls,
            restored_image_bytes=len(restored_bytes),
            restored_image_sha256=hashlib.sha256(restored_bytes).hexdigest(),
            unresolved_tool_calls=store.unresolved_tool_calls(run["id"]),
        )
        if not passed:
            raise RuntimeError("Real-model image perception or artifact-integrity check failed.")
    except Exception as exc:
        report["error"] = type(exc).__name__
        raise
    finally:
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            await provider.close()


if __name__ == "__main__":
    asyncio.run(main())

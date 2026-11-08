"""Run a browser read through the engine at its installed Windows path."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class WorkerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ok: bool
    result: dict[str, Any]
    error: str | None = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    args = parser.parse_args()
    engine = args.engine.resolve(strict=True)
    request = {
        "url": "https://example.com/",
        "approved_hosts": ["example.com"],
        "links": [],
        "screenshot": True,
    }
    completed = subprocess.run(
        [str(engine), "--browser-worker"],
        input=json.dumps(request, separators=(",", ":")).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode != 0 or len(completed.stdout) > 1_048_576:
        raise RuntimeError("Installed browser worker exited without a bounded result.")
    response = WorkerResult.model_validate_json(completed.stdout)
    if not response.ok:
        raise RuntimeError(response.error or "Installed browser worker failed.")
    result = response.result
    screenshot_value = result.pop("screenshot_png", None)
    if not isinstance(screenshot_value, str):
        raise TypeError("Installed browser worker returned no screenshot.")
    screenshot = base64.b64decode(screenshot_value, validate=True)
    if (
        result.get("title") != "Example Domain"
        or result.get("trust") != "untrusted_web_content"
        or not screenshot.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise RuntimeError("Installed browser worker returned invalid page or screenshot evidence.")

    report: dict[str, Any] = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "engine_path": str(engine),
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "browser": "Microsoft Edge via installed Playwright worker",
        "result": {
            "status": "ok",
            "title": result["title"],
            "trust": result["trust"],
            "content_characters": len(result.get("content", "")),
            "screenshot_bytes": len(screenshot),
            "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
        },
        "model_inference_requested": False,
    }
    acceptance = Path(".acceptance") / f"installed-browser-{uuid.uuid4().hex}"
    acceptance.mkdir(parents=True, exist_ok=False)
    report_path = acceptance / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(report_path)}, indent=2))


if __name__ == "__main__":
    main()

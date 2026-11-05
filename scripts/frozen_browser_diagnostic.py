"""Launch a real browser request through the packaged one-file engine worker."""

import base64
import hashlib
import json
import subprocess
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
    executable = Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe")
    request = {
        "url": "https://example.com/",
        "approved_hosts": ["example.com"],
        "links": [],
        "screenshot": True,
    }
    completed = subprocess.run(
        [str(executable.resolve()), "--browser-worker"],
        input=json.dumps(request, separators=(",", ":")).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=60,
        check=False,
        creationflags=0x08000000,
    )
    if completed.returncode != 0 or len(completed.stdout) > 1_048_576:
        raise RuntimeError("Frozen browser worker exited without a bounded result.")
    response = WorkerResult.model_validate_json(completed.stdout)
    if not response.ok:
        raise RuntimeError(response.error or "Frozen browser worker failed.")
    result = response.result
    screenshot = base64.b64decode(result.pop("screenshot_png"), validate=True)
    if result.get("title") != "Example Domain" or not screenshot.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RuntimeError("Frozen browser worker returned invalid page or screenshot evidence.")
    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "frozen_engine_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "browser": "Microsoft Edge via packaged Playwright worker",
        "result": {
            "status": "ok",
            "title": result["title"],
            "trust": result["trust"],
            "content_characters": len(result["content"]),
            "screenshot_bytes": len(screenshot),
            "screenshot_sha256": hashlib.sha256(screenshot).hexdigest(),
        },
    }
    target = Path("docs/releases/evidence/browser-frozen-live.json")
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

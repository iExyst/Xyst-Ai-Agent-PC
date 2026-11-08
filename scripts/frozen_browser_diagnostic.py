"""Launch a real browser request through the packaged one-file engine worker."""

import base64
import hashlib
import json
import os
import subprocess
import time
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
    cancel_request = {
        "url": "https://httpbin.org/delay/10",
        "approved_hosts": ["httpbin.org"],
        "links": [],
        "screenshot": False,
    }
    worker = subprocess.Popen(
        [str(executable.resolve()), "--browser-worker"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=0x08000000,
    )
    assert worker.stdin is not None
    worker.stdin.write(json.dumps(cancel_request, separators=(",", ":")).encode())
    worker.stdin.close()
    time.sleep(4)
    if worker.poll() is not None:
        raise RuntimeError("Frozen browser worker exited before the cancellation window.")
    cancelled_at = time.monotonic()
    taskkill = subprocess.run(
        [
            os.path.join(os.environ["SystemRoot"], "System32", "taskkill.exe"),
            "/PID",
            str(worker.pid),
            "/T",
            "/F",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=8,
        check=False,
        creationflags=0x08000000,
    )
    worker.wait(timeout=8)
    cancellation_elapsed_ms = round((time.monotonic() - cancelled_at) * 1000)
    if taskkill.returncode != 0 or cancellation_elapsed_ms >= 8000:
        raise RuntimeError("Frozen browser worker process tree did not stop within its bound.")
    if worker.stdout is not None:
        worker.stdout.close()

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
        "cancellation": {
            "url": cancel_request["url"],
            "process_tree_terminated": True,
            "exit_code": worker.returncode,
            "cleanup_elapsed_ms": cancellation_elapsed_ms,
        },
    }
    run_id = os.environ.get("GITHUB_RUN_ID", str(os.getpid()))
    acceptance = Path(".acceptance") / f"browser-frozen-cancel-{run_id}"
    acceptance.mkdir(parents=True, exist_ok=False)
    (acceptance / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    target = Path("docs/releases/evidence/browser-frozen-live.json")
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

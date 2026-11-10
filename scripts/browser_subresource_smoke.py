"""Verify live Edge blocks off-host page subresources for an approved site."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class BlockedSubresources(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    count: int
    resource_types: dict[str, int]


class BrowserWorkerResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    title: str
    content: str
    blocked_subresources: BlockedSubresources


class BrowserWorkerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    ok: bool
    result: BrowserWorkerResult | None = None
    error: str | None = None


def main() -> None:
    request = {
        "url": "https://en.wikipedia.org/wiki/HTTPS",
        "approved_hosts": ["en.wikipedia.org"],
        "links": [],
        "screenshot": False,
        "inspect_forms": False,
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PATH", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    environment["PATH"] = os.path.join(os.environ["SystemRoot"], "System32")
    completed = subprocess.run(
        [sys.executable, "-I", "-m", "xaa.runtime.browser_worker"],
        input=json.dumps(request, separators=(",", ":")).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=45,
        check=False,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode != 0:
        raise RuntimeError("The managed Edge worker exited without a result.")
    response = BrowserWorkerResponse.model_validate_json(completed.stdout)
    result = response.result
    if not response.ok or result is None:
        raise RuntimeError(f"The managed Edge navigation failed: {response.error!r}")
    blocked = result.blocked_subresources
    if (
        result.title != "HTTPS - Wikipedia"
        or "Hypertext Transfer Protocol Secure" not in result.content
        or blocked.count < 1
        or not blocked.resource_types
    ):
        raise AssertionError("The live page did not prove off-host subresources were blocked.")

    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "browser": "Microsoft Edge via the managed Playwright worker",
        "platform": platform.platform(),
        "approved_host": "en.wikipedia.org",
        "navigation_url": request["url"],
        "page_loaded": True,
        "off_host_subresources_blocked": True,
        "blocked_subresource_count": blocked.count,
        "blocked_resource_types": blocked.resource_types,
        "model_inference_requested": False,
        "source_sha256": {
            "browser_subresource_smoke.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "browser_worker.py": hashlib.sha256(
                Path("engine/xaa/runtime/browser_worker.py").read_bytes()
            ).hexdigest(),
        },
    }
    destination = Path(".acceptance") / f"browser-subresource-edge-{uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(destination / "report.json")}, indent=2))


if __name__ == "__main__":
    main()

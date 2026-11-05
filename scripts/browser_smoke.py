"""Live managed Edge acceptance check against example.com."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from xaa.runtime.browser import run_approved_browser
from xaa.storage.runs import RunStore


async def main() -> None:
    with TemporaryDirectory() as directory:
        store = RunStore(Path(directory) / "browser-smoke.sqlite3")
        run = store.create_run(
            objective="Open Example Domain in a disposable browser and capture evidence.",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=["example.com"],
            approved_browser=True,
        )
        token = store.acquire_lease(run["id"], "managed-browser-live-smoke")
        if token is None:
            raise RuntimeError("Could not acquire the smoke run lease.")
        started = time.monotonic()
        result = await run_approved_browser(
            store,
            run["id"],
            token,
            "live-browser-smoke-1",
            {"url": "https://example.com/", "screenshot": True},
            ["example.com"],
            deadline=time.monotonic() + 40,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        assert result.get("status") == "ok", result
        assert result.get("title") == "Example Domain", result
        assert "documentation examples" in str(result.get("content", "")), result
        artifact = cast(dict[str, Any], result.get("screenshot_artifact"))
        assert isinstance(artifact, dict) and artifact.get("media_type") == "image/png", result
        report: dict[str, Any] = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "browser": "Microsoft Edge via Playwright channel=msedge",
            "approved_hosts": ["example.com"],
            "result": {
                "status": result["status"],
                "url": result["url"],
                "title": result["title"],
                "trust": result["trust"],
                "content_characters": len(result["content"]),
                "screenshot_artifact": {
                    "media_type": artifact["media_type"],
                    "size_bytes": artifact["size_bytes"],
                    "sha256": artifact["sha256"],
                },
            },
            "elapsed_ms": elapsed_ms,
            "source_sha256": hashlib.sha256(
                Path("engine/xaa/runtime/browser.py").read_bytes()
                + Path("engine/xaa/runtime/browser_worker.py").read_bytes()
            ).hexdigest(),
        }
        target = Path("docs/releases/evidence/browser-live.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

"""Live acceptance check for the exact-host public web fetch tool."""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from xaa.runtime.web_fetch import fetch_approved_web_page
from xaa.storage.runs import RunStore


async def main() -> None:
    with TemporaryDirectory() as directory:
        store = RunStore(Path(directory) / "smoke.sqlite3")
        run = store.create_run(
            objective="Fetch the Example Domain page",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=["example.com"],
        )
        token = store.acquire_lease(run["id"], "web-fetch-live-smoke")
        if token is None:
            raise RuntimeError("Could not acquire the smoke run lease.")
        started = time.monotonic()
        result = await fetch_approved_web_page(
            store,
            run["id"],
            token,
            "live-web-fetch-smoke-1",
            "https://example.com/",
            ["example.com"],
            deadline=time.monotonic() + 15,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        assert result.get("status") == "ok", result
        assert result.get("title") == "Example Domain", result
        assert result.get("trust") == "untrusted_web_content", result
        assert "documentation examples" in str(result.get("content", "")), result
        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "approved_hosts": ["example.com"],
            "result": {
                "status": result["status"],
                "url": result["url"],
                "title": result["title"],
                "trust": result["trust"],
                "content_characters": len(result["content"]),
                "truncated": result["truncated"],
            },
            "elapsed_ms": elapsed_ms,
            "source_sha256": hashlib.sha256(
                Path("engine/xaa/runtime/web_fetch.py").read_bytes()
            ).hexdigest(),
        }
        target = Path("docs/releases/evidence/web-fetch-live.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

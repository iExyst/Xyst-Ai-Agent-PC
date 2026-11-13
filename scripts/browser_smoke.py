"""Live Windows Edge acceptance check for the managed browser read tool."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from xaa.runtime.browser import run_approved_browser
from xaa.storage.runs import RunStore


async def main() -> None:
    proxy_diagnostics: dict[str, Any] = {}
    with TemporaryDirectory(prefix="xaa-browser-live-") as directory:
        root = Path(directory)
        database = root / "runs.sqlite3"
        store = RunStore(database)
        run = store.create_run(
            objective="Read the public Example Domain page in managed Edge",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=["example.com"],
            approved_browser=True,
        )
        token = store.acquire_lease(run["id"], "browser-live-smoke", seconds=120)
        if token is None:
            raise RuntimeError("Could not acquire the smoke run lease.")

        started = time.monotonic()
        result = await run_approved_browser(
            store,
            run["id"],
            token,
            "browser-live-smoke-1",
            {"url": "https://example.com/", "links": [], "screenshot": True},
            ["example.com"],
            deadline=time.monotonic() + 32,
            proxy_diagnostics=proxy_diagnostics,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        assert result.get("status") == "ok", result
        assert result.get("url") == "https://example.com/", result
        assert result.get("title") == "Example Domain", result
        assert "documentation examples" in str(result.get("content", "")), result
        assert result.get("trust") == "untrusted_web_content", result
        if proxy_diagnostics.get("forwarded_connections", 0) < 1 or proxy_diagnostics.get(
            "forwarded_hosts"
        ) != ["example.com"]:
            raise AssertionError(
                f"Real Edge did not forward through the approved proxy: {proxy_diagnostics}"
            )
        artifact_value = result.get("screenshot_artifact")
        assert isinstance(artifact_value, dict), result
        artifact: dict[str, Any] = cast(dict[str, Any], artifact_value)
        assert artifact.get("media_type") == "image/png", artifact
        assert 0 < int(artifact.get("size_bytes", 0)) <= 512 * 1024, artifact
        assert artifact.get("provenance") == "browser.screenshot:untrusted_web_content", artifact

        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT tool_name, state, result FROM tool_calls WHERE provider_call_id=?",
                ("browser-live-smoke-1",),
            ).fetchone()
        if row is None or row[0] != "browser.run" or row[1] != "completed":
            raise AssertionError(f"Browser result was not durably journaled: {row!r}")
        saved_result = json.loads(row[2])
        if saved_result.get("status") != "ok" or saved_result.get("trust") != (
            "untrusted_web_content"
        ):
            raise AssertionError("Saved browser result did not retain its status and trust label.")

        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "browser": "managed Microsoft Edge worker subprocess",
            "approved_hosts": ["example.com"],
            "proxy_diagnostics": proxy_diagnostics,
            "result": {
                "status": result["status"],
                "url": result["url"],
                "title": result["title"],
                "trust": result["trust"],
                "content_characters": len(str(result["content"])),
                "screenshot_artifact": artifact,
                "journal_state": row[1],
            },
            "elapsed_ms": elapsed_ms,
            "source_sha256": {
                "browser.py": hashlib.sha256(
                    Path("engine/xaa/runtime/browser.py").read_bytes()
                ).hexdigest(),
                "browser_worker.py": hashlib.sha256(
                    Path("engine/xaa/runtime/browser_worker.py").read_bytes()
                ).hexdigest(),
            },
        }
        target = Path("docs/releases/evidence/browser-live.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

"""Click one owner-granted public disclosure control in managed Microsoft Edge."""

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

from xaa.runtime.browser import run_approved_browser_interaction
from xaa.storage.runs import RunStore

HOSTS = ["www.w3.org", "aria-at.w3.org"]
URL = "https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/examples/disclosure-navigation/"
BUTTON = "Academics"


async def main() -> None:
    with TemporaryDirectory(prefix="xaa-browser-interaction-live-") as directory:
        database = Path(directory) / "runs.sqlite3"
        store = RunStore(database)
        run = store.create_run(
            objective="Open the public disclosure navigation example",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=HOSTS,
            approved_browser=True,
            approved_browser_interaction=True,
        )
        token = store.acquire_lease(run["id"], "browser-interaction-live-smoke", seconds=120)
        if token is None:
            raise RuntimeError("Could not acquire the browser interaction smoke lease.")
        started = time.monotonic()
        result = await run_approved_browser_interaction(
            store,
            run["id"],
            token,
            "browser-interaction-live-smoke-1",
            {"url": URL, "button_name": BUTTON},
            HOSTS,
            deadline=time.monotonic() + 32,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        assert result.get("status") == "ok", result
        assert result.get("clicked_button") == BUTTON, result
        assert isinstance(result.get("button_expanded_before"), bool), result
        assert isinstance(result.get("button_expanded"), bool), result
        assert result["button_expanded_before"] != result["button_expanded"], result
        content = str(result.get("content", ""))
        assert result.get("trust") == "untrusted_web_content", result
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT tool_name, state, result FROM tool_calls WHERE provider_call_id=?",
                ("browser-interaction-live-smoke-1",),
            ).fetchone()
        if row is None or row[0] != "browser.interact" or row[1] != "completed":
            raise AssertionError(f"Button interaction was not durably journaled: {row!r}")
        saved = json.loads(row[2])
        if saved.get("clicked_button") != BUTTON or saved.get("trust") != ("untrusted_web_content"):
            raise AssertionError("Saved interaction result lost its button or trust label.")

        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "browser": "managed Microsoft Edge worker subprocess",
            "approved_hosts": HOSTS,
            "grants": {
                "browser_read": True,
                "browser_interaction": True,
                "form_submission": False,
            },
            "result": {
                "status": result["status"],
                "url": result["url"],
                "title": result["title"],
                "clicked_button": result["clicked_button"],
                "button_expanded_before_click": result["button_expanded_before"],
                "button_expanded_after_click": result["button_expanded"],
                "trust": result["trust"],
                "content_characters": len(content),
                "journal_tool": row[0],
                "journal_state": row[1],
            },
            "elapsed_ms": elapsed_ms,
            "source_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (
                    Path("engine/xaa/runtime/browser.py"),
                    Path("engine/xaa/runtime/browser_worker.py"),
                )
            },
        }
        target = Path("docs/releases/evidence/browser-interaction-live.json")
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

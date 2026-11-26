"""Live fill-only form acceptance in managed Microsoft Edge; never submit the form."""

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
from typing import Any

from xaa.runtime.browser import run_approved_browser_form_fill
from xaa.storage.runs import RunStore

HOST = "httpbin.org"
URL = f"https://{HOST}/forms/post"
FIELDS = ["custname", "custemail"]


async def main() -> None:
    with TemporaryDirectory(prefix="xaa-browser-form-fill-live-") as directory:
        database = Path(directory) / "runs.sqlite3"
        store = RunStore(database)
        run = store.create_run(
            objective="Fill visible fields on the public HTTPBin example without submitting",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=[HOST],
            approved_browser=True,
            approved_browser_interaction=True,
        )
        token = store.acquire_lease(run["id"], "browser-form-fill-live-smoke", seconds=120)
        if token is None:
            raise RuntimeError("Could not acquire the browser fill smoke lease.")
        started = time.monotonic()
        result = await run_approved_browser_form_fill(
            store,
            run["id"],
            token,
            "browser-form-fill-live-smoke-1",
            {
                "url": URL,
                "form_index": 0,
                "values": [
                    {"name": "custname", "value": "XAA fill-only acceptance"},
                    {"name": "custemail", "value": "fill-only@example.invalid"},
                ],
            },
            [HOST],
            deadline=time.monotonic() + 32,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        assert result.get("status") == "ok", result
        assert result.get("trust") == "untrusted_web_content", result
        assert result.get("filled_fields") == FIELDS, result
        assert result.get("submitted") is False, result
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT tool_name, arguments, state, result FROM tool_calls WHERE provider_call_id=?",
                ("browser-form-fill-live-smoke-1",),
            ).fetchone()
        if row is None or row[0] != "browser.fill_form" or row[2] != "completed":
            raise AssertionError(f"Form fill was not durably journaled: {row!r}")
        journal_arguments = json.loads(row[1])
        if journal_arguments.get("field_names") != FIELDS:
            raise AssertionError("The browser journal did not retain the filled field names.")
        if "XAA fill-only acceptance" in row[1] or "fill-only@example.invalid" in row[1]:
            raise AssertionError("The browser journal retained a field value.")
        saved = json.loads(row[3])
        if saved != result or saved.get("submitted") is not False:
            raise AssertionError("The saved fill result differs or reports a submission.")

        report: dict[str, Any] = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "browser": "managed Microsoft Edge worker subprocess",
            "approved_hosts": [HOST],
            "grants": {
                "browser_read": True,
                "browser_interaction": True,
                "form_submission": False,
            },
            "result": {
                "status": result["status"],
                "url": result.get("url"),
                "title": result.get("title"),
                "filled_fields": result["filled_fields"],
                "submitted": result["submitted"],
                "trust": result["trust"],
                "content_characters": len(str(result.get("content", ""))),
                "journal_tool": row[0],
                "journal_field_names": journal_arguments["field_names"],
                "field_values_in_journal": False,
                "journal_state": row[2],
            },
            "elapsed_ms": elapsed_ms,
            "source_sha256": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (
                    Path("engine/xaa/runtime/browser.py"),
                    Path("engine/xaa/runtime/browser_worker.py"),
                    Path("engine/xaa/runtime/coordinator.py"),
                )
            },
        }
        target = Path("docs/releases/evidence/browser-form-fill-live.json")
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

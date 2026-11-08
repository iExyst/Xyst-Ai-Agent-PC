"""Cancel and time out live Edge navigations; verify durable tool results."""

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
from uuid import uuid4

from xaa.runtime.browser import run_approved_browser
from xaa.storage.runs import RunStore


async def main() -> None:
    with TemporaryDirectory(prefix="browser-cancel-smoke-", dir=".acceptance") as temporary:
        database = Path(temporary) / "cancel.sqlite3"
        store = RunStore(database)
        run = store.create_run(
            objective="Verify cancellation stops a live managed Edge navigation",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=["httpbin.org"],
            approved_browser=True,
        )
        token = store.acquire_lease(run["id"], "browser-cancel-smoke", seconds=90)
        if token is None:
            raise RuntimeError("Could not acquire the browser cancellation smoke lease.")

        call_id = uuid4().hex
        task = asyncio.create_task(
            run_approved_browser(
                store,
                run["id"],
                token,
                call_id,
                {"url": "https://httpbin.org/delay/10"},
                ["httpbin.org"],
                deadline=time.monotonic() + 30,
            )
        )
        # Allow Edge to launch and begin the intentionally delayed HTTPS navigation.
        await asyncio.sleep(4)
        cancelled_at = time.monotonic()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=8)
        except asyncio.CancelledError:
            pass
        elapsed_ms = round((time.monotonic() - cancelled_at) * 1000)
        if task.cancelled() is not True:
            raise AssertionError("The managed browser task did not remain cancelled.")

        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT tool_name, state, result FROM tool_calls WHERE provider_call_id=?",
                (call_id,),
            ).fetchone()
        if row is None or row[0] != "browser.run" or row[1] != "completed":
            raise AssertionError(f"Cancellation was not durably journaled: {row!r}")
        saved = json.loads(row[2])
        if saved != {"status": "cancelled", "trust": "untrusted_web_content"}:
            raise AssertionError(f"Unexpected saved cancellation result: {saved!r}")
        if elapsed_ms >= 8000:
            raise AssertionError(f"Owned Edge worker cleanup exceeded its bound: {elapsed_ms} ms")

        timeout_run = store.create_run(
            objective="Verify timeout stops a live managed Edge navigation",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=["httpbin.org"],
            approved_browser=True,
        )
        timeout_token = store.acquire_lease(timeout_run["id"], "browser-timeout-smoke", seconds=90)
        if timeout_token is None:
            raise RuntimeError("Could not acquire the browser timeout smoke lease.")
        timeout_call_id = uuid4().hex
        timeout_started = time.monotonic()
        timeout_result = await run_approved_browser(
            store,
            timeout_run["id"],
            timeout_token,
            timeout_call_id,
            {"url": "https://httpbin.org/delay/10"},
            ["httpbin.org"],
            deadline=time.monotonic() + 4,
        )
        timeout_elapsed_ms = round((time.monotonic() - timeout_started) * 1000)
        with closing(sqlite3.connect(database)) as connection:
            timeout_row = connection.execute(
                "SELECT tool_name, state, result FROM tool_calls WHERE provider_call_id=?",
                (timeout_call_id,),
            ).fetchone()
        if timeout_row is None or timeout_row[0] != "browser.run" or timeout_row[1] != "completed":
            raise AssertionError(f"Timeout was not durably journaled: {timeout_row!r}")
        timeout_saved = json.loads(timeout_row[2])
        if timeout_saved != timeout_result or timeout_result != {
            "status": "read_failed",
            "error": "Browser navigation exceeded its time limit.",
            "trust": "untrusted_web_content",
        }:
            raise AssertionError(f"Unexpected saved timeout result: {timeout_saved!r}")
        if timeout_elapsed_ms >= 8000:
            raise AssertionError(
                f"Timed-out Edge worker cleanup exceeded its bound: {timeout_elapsed_ms} ms"
            )

    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "browser": "Microsoft Edge via managed Playwright worker",
        "url": "https://httpbin.org/delay/10",
        "cancelled": True,
        "journal_state": row[1],
        "saved_result": saved,
        "cleanup_elapsed_ms": elapsed_ms,
        "timeout": {
            "url": "https://httpbin.org/delay/10",
            "deadline_seconds": 4,
            "elapsed_ms": timeout_elapsed_ms,
            "journal_state": timeout_row[1],
            "saved_result": timeout_saved,
        },
        "source_sha256": {
            "browser_cancel_smoke.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "browser.py": hashlib.sha256(
                Path("engine/xaa/runtime/browser.py").read_bytes()
            ).hexdigest(),
            "processes.py": hashlib.sha256(
                Path("engine/xaa/runtime/processes.py").read_bytes()
            ).hexdigest(),
        },
    }
    destination = Path(".acceptance") / f"browser-cancel-edge-{uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(destination / "report.json")}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

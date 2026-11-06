"""Live acceptance for opt-in form structure inspection in managed Edge."""

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
    host = "httpbin.org"
    url = f"https://{host}/forms/post"
    with TemporaryDirectory(prefix="xaa-browser-forms-live-") as directory:
        database = Path(directory) / "runs.sqlite3"
        store = RunStore(database)
        run = store.create_run(
            objective="Inspect visible controls on the public HTTPBin form page",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=[host],
            approved_browser=True,
        )
        token = store.acquire_lease(run["id"], "browser-forms-live-smoke", seconds=120)
        if token is None:
            raise RuntimeError("Could not acquire the browser form smoke lease.")
        started = time.monotonic()
        result = await run_approved_browser(
            store,
            run["id"],
            token,
            "browser-forms-live-smoke-1",
            {"url": url, "inspect_forms": True},
            [host],
            deadline=time.monotonic() + 32,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        assert result.get("status") == "ok", result
        forms = cast(list[dict[str, Any]], result.get("forms"))
        controls = [control for form in forms for control in form["controls"]]
        names = {control["name"] for control in controls}
        assert "custname" in names and "custemail" in names, result
        assert all("value" not in control for control in controls), result
        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT state, result FROM tool_calls WHERE provider_call_id=?",
                ("browser-forms-live-smoke-1",),
            ).fetchone()
        if row is None or row[0] != "completed":
            raise AssertionError(f"Form inspection result was not durably journaled: {row!r}")
        saved = json.loads(row[1])
        if saved.get("forms") != forms or any(
            "value" in control for form in saved["forms"] for control in form["controls"]
        ):
            raise AssertionError("Saved form metadata differs or contains a control value.")

        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "browser": "managed Microsoft Edge worker subprocess",
            "approved_hosts": [host],
            "result": {
                "status": result["status"],
                "trust": result["trust"],
                "forms": len(forms),
                "controls": len(controls),
                "control_names": sorted(name for name in names if name),
                "control_values_in_output": False,
                "journal_state": row[0],
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
        target = Path("docs/releases/evidence/browser-forms-live.json")
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

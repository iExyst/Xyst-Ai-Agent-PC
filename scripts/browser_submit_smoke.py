"""Live acceptance for the explicitly approved managed-browser form submit path."""

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
from uuid import uuid4

from xaa.runtime.browser import run_approved_browser, run_approved_browser_submission
from xaa.storage.runs import RunStore


async def main() -> None:
    host = "httpbin.org"
    url = f"https://{host}/forms/post"
    marker = f"XAA harmless browser form test {uuid4()}"
    email = "xaa-browser-submit@example.invalid"
    with TemporaryDirectory(prefix="xaa-browser-submit-live-") as directory:
        database = Path(directory) / "runs.sqlite3"
        store = RunStore(database)
        run = store.create_run(
            objective="Submit synthetic values to the public HTTPBin echo form",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=[host],
            approved_browser=True,
            approved_browser_submission=True,
        )
        token = store.acquire_lease(run["id"], "browser-submit-live-smoke", seconds=120)
        if token is None:
            raise RuntimeError("Could not acquire the browser submission smoke lease.")

        inspect = await run_approved_browser(
            store,
            run["id"],
            token,
            "browser-submit-inspect",
            {"url": url, "inspect_forms": True},
            [host],
            deadline=time.monotonic() + 32,
        )
        if inspect.get("status") != "ok":
            raise AssertionError(f"HTTPBin form inspection failed: {inspect!r}")
        forms = cast(list[dict[str, Any]], inspect.get("forms"))
        candidates = [
            (index, form)
            for index, form in enumerate(forms)
            if form.get("method") == "post"
            and any(control.get("name") == "custname" for control in form["controls"])
            and any(control.get("name") == "custemail" for control in form["controls"])
        ]
        if len(candidates) != 1:
            raise AssertionError("Expected exactly one visible HTTPBin POST echo form.")
        form_index, form = candidates[0]
        submitters = form["submitters"]
        if len(submitters) != 1 or submitters[0].get("disabled"):
            raise AssertionError(f"Expected one enabled HTTPBin submitter: {submitters!r}")
        submitter_label = str(submitters[0].get("label", "")).strip()
        if not submitter_label:
            raise AssertionError("HTTPBin submitter has no accessible or visible label.")

        started = time.monotonic()
        result = await run_approved_browser_submission(
            store,
            run["id"],
            token,
            "browser-submit-live-smoke",
            {
                "url": url,
                "form_index": form_index,
                "values": [
                    {"name": "custname", "value": marker},
                    {"name": "custemail", "value": email},
                    {
                        "name": "comments",
                        "value": "Synthetic acceptance value; no message was sent.",
                    },
                ],
                "submitter_label": submitter_label,
            },
            [host],
            deadline=time.monotonic() + 32,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if result.get("status") != "ok" or result.get("submitted") is not True:
            raise AssertionError(f"HTTPBin form submission did not complete: {result!r}")
        if result.get("submitted_fields") != ["custname", "custemail", "comments"]:
            raise AssertionError(f"Submitted field audit is incorrect: {result!r}")
        content = str(result.get("content", ""))
        if marker not in content or email not in content:
            raise AssertionError(
                "HTTPBin echo response did not contain the synthetic submitted values."
            )

        with closing(sqlite3.connect(database)) as connection:
            row = connection.execute(
                "SELECT tool_name, state, result FROM tool_calls WHERE provider_call_id=?",
                ("browser-submit-live-smoke",),
            ).fetchone()
        if row is None or row[0] != "browser.submit" or row[1] != "completed":
            raise AssertionError(f"Submitted result was not durably journaled: {row!r}")
        saved = json.loads(row[2])
        if (
            saved.get("submitted") is not True
            or saved.get("submitted_fields") != ["custname", "custemail", "comments"]
            or marker not in saved.get("content", "")
        ):
            raise AssertionError("Journaled submit result does not match the echo response.")

        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "browser": "managed Microsoft Edge worker subprocess",
            "approved_hosts": [host],
            "form_url": url,
            "submitter_label": submitter_label,
            "result": {
                "status": result["status"],
                "trust": result["trust"],
                "submitted": result["submitted"],
                "submitted_fields": result["submitted_fields"],
                "synthetic_marker_echoed": marker in content,
                "synthetic_email_echoed": email in content,
                "response_is_untrusted": result["trust"] == "untrusted_web_content",
                "journal_tool": row[0],
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
                "browser_submit_smoke.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
        }
        target = Path("docs/releases/evidence/browser-submit-live.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

"""Live Windows Edge acceptance for the managed browser download tool."""

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

from xaa.runtime.browser import run_approved_browser_download
from xaa.storage.runs import RunStore


async def main() -> None:
    host = "www.rfc-editor.org"
    url = "https://www.rfc-editor.org/rfc/rfc9110"
    link_name = "Plain Text"
    with TemporaryDirectory(prefix="xaa-browser-download-live-") as directory:
        root = Path(directory)
        database = root / "runs.sqlite3"
        store = RunStore(database)
        run = store.create_run(
            objective="Download the official RFC 9110 plain-text document through managed Edge",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
            approved_web_hosts=[host],
            approved_browser=True,
            approved_browser_download=True,
        )
        token = store.acquire_lease(run["id"], "browser-download-live-smoke", seconds=120)
        if token is None:
            raise RuntimeError("Could not acquire the browser download acceptance lease.")

        started = time.monotonic()
        result = await run_approved_browser_download(
            store,
            run["id"],
            token,
            "browser-download-live-smoke-1",
            {"url": url, "link_name": link_name},
            [host],
            deadline=time.monotonic() + 45,
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        if result.get("status") != "ok" or result.get("trust") != "untrusted_web_content":
            raise AssertionError(f"Managed Edge download failed: {result!r}")

        artifact = result.get("download_artifact")
        if not isinstance(artifact, dict):
            raise TypeError("Managed Edge did not register the downloaded artifact.")
        artifact = cast(dict[str, Any], artifact)
        saved_artifact = store.get_artifact(str(artifact.get("id", "")))
        if saved_artifact is None:
            raise AssertionError("Downloaded artifact is missing from run storage.")
        payload = (root / "artifacts" / str(saved_artifact["storage_key"])).read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if (
            len(payload) != artifact.get("size_bytes")
            or digest != artifact.get("sha256")
            or b"RFC 9110" not in payload
        ):
            raise AssertionError("Downloaded bytes, digest, or RFC marker did not match.")

        with closing(sqlite3.connect(database)) as connection:
            journal = connection.execute(
                "SELECT tool_name, state, result FROM tool_calls WHERE provider_call_id=?",
                ("browser-download-live-smoke-1",),
            ).fetchone()
        if journal is None or journal[0] != "browser.download" or journal[1] != "completed":
            raise AssertionError(f"Download result was not durably journaled: {journal!r}")
        saved_result = json.loads(journal[2])
        if saved_result.get("download_artifact", {}).get("sha256") != digest:
            raise AssertionError("Durable journal does not identify the verified artifact.")

        report: dict[str, Any] = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "browser": "managed Microsoft Edge worker subprocess",
            "approved_hosts": [host],
            "page_url": url,
            "visible_link_name": link_name,
            "result": {
                "status": result["status"],
                "trust": result["trust"],
                "suggested_filename": artifact.get("provenance"),
                "size_bytes": len(payload),
                "sha256": digest,
                "artifact_integrity_verified": True,
                "rfc_9110_marker_verified": True,
                "journal_tool": journal[0],
                "journal_state": journal[1],
            },
            "elapsed_ms": elapsed_ms,
            "model_inference_requested": False,
            "source_sha256": {
                "browser.py": hashlib.sha256(
                    Path("engine/xaa/runtime/browser.py").read_bytes()
                ).hexdigest(),
                "browser_worker.py": hashlib.sha256(
                    Path("engine/xaa/runtime/browser_worker.py").read_bytes()
                ).hexdigest(),
                "browser_download_smoke.py": hashlib.sha256(
                    Path(__file__).read_bytes()
                ).hexdigest(),
            },
        }
        target = Path("docs/releases/evidence/browser-download-live.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

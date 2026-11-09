"""Verify managed Edge accepts approved redirects and rejects off-host redirects."""

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
from typing import cast
from urllib.parse import urlencode
from uuid import uuid4

from xaa.runtime.browser import run_approved_browser
from xaa.storage.runs import RunStore


async def run_case(
    database: Path,
    *,
    name: str,
    destination: str,
    approved_hosts: list[str],
    start_url: str | None = None,
) -> dict[str, object]:
    start_url = start_url or (
        "https://postman-echo.com/redirect-to?" + urlencode({"url": destination})
    )
    store = RunStore(database)
    run = store.create_run(
        objective="Verify managed browser redirect policy",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
        approved_web_hosts=approved_hosts,
        approved_browser=True,
    )
    token = store.acquire_lease(run["id"], f"browser-redirect-{name}", seconds=90)
    if token is None:
        raise RuntimeError(f"Could not acquire the {name} smoke run lease.")

    call_id = uuid4().hex
    started = time.monotonic()
    result = await run_approved_browser(
        store,
        run["id"],
        token,
        call_id,
        {"url": start_url},
        approved_hosts,
        deadline=time.monotonic() + 30,
    )
    elapsed_ms = round((time.monotonic() - started) * 1000)

    with closing(sqlite3.connect(database)) as connection:
        row = connection.execute(
            "SELECT tool_name, state, result FROM tool_calls WHERE provider_call_id=?",
            (call_id,),
        ).fetchone()
    if row is None or row[0] != "browser.run" or row[1] != "completed":
        raise AssertionError(f"The {name} redirect result was not durably journaled: {row!r}")
    saved = json.loads(row[2])
    if saved.get("status") != result.get("status") or saved.get("trust") != (
        "untrusted_web_content"
    ):
        raise AssertionError(f"The {name} redirect result changed during persistence.")

    return {
        "case": name,
        "start_url": start_url,
        "approved_hosts": approved_hosts,
        "destination": destination,
        "status": result.get("status"),
        "error": result.get("error"),
        "final_url": result.get("url"),
        "title": result.get("title"),
        "content_has_fixture_marker": "Herman Melville - Moby-Dick"
        in str(result.get("content", "")),
        "blocked_subresources": result.get("blocked_subresources", {}),
        "journal_state": row[1],
        "elapsed_ms": elapsed_ms,
    }


async def main() -> None:
    with TemporaryDirectory(prefix="browser-redirect-smoke-", dir=".acceptance") as temporary:
        root = Path(temporary)
        allowed = await run_case(
            root / "allowed.sqlite3",
            name="approved_redirect",
            destination="https://httpbin.org/html",
            approved_hosts=["postman-echo.com", "httpbin.org"],
        )
        if (
            allowed["status"] != "ok"
            or allowed["final_url"] != "https://httpbin.org/html"
            or allowed["content_has_fixture_marker"] is not True
        ):
            raise AssertionError(f"Approved redirect did not reach the approved content: {allowed}")

        blocked = await run_case(
            root / "blocked.sqlite3",
            name="unapproved_redirect",
            destination="https://www.iana.org/",
            approved_hosts=["postman-echo.com"],
        )
        if blocked["status"] != "read_failed" or blocked["final_url"] is not None:
            raise AssertionError(f"An off-host redirect was not blocked: {blocked}")

        subresources = await run_case(
            root / "subresources.sqlite3",
            name="unapproved_subresources",
            destination="https://httpbin.org/",
            approved_hosts=["postman-echo.com", "httpbin.org"],
        )
        blocked_resources = cast(dict[str, object], subresources.get("blocked_subresources", {}))
        blocked_count = blocked_resources.get("count", 0)
        if (
            subresources["status"] != "ok"
            or not isinstance(blocked_count, int)
            or blocked_count < 1
        ):
            raise AssertionError(
                f"Off-host subresources were not blocked and recorded: {subresources}"
            )

        approved_chain_destination = "https://example.com/"
        approved_chain_second_hop = "https://httpbin.org/redirect-to?" + urlencode(
            {"url": approved_chain_destination}
        )
        approved_chain_start = "https://postman-echo.com/redirect-to?" + urlencode(
            {"url": approved_chain_second_hop}
        )
        approved_chain = await run_case(
            root / "approved-chain.sqlite3",
            name="approved_multi_hop_redirect",
            destination=approved_chain_destination,
            approved_hosts=["postman-echo.com", "httpbin.org", "example.com"],
            start_url=approved_chain_start,
        )
        if (
            approved_chain["status"] != "ok"
            or approved_chain["final_url"] != approved_chain_destination
            or approved_chain["title"] != "Example Domain"
        ):
            raise AssertionError(
                f"Approved multi-hop redirect did not reach the approved content: {approved_chain}"
            )

        blocked_chain_destination = "https://example.org/"
        blocked_chain_second_hop = "https://httpbin.org/redirect-to?" + urlencode(
            {"url": blocked_chain_destination}
        )
        blocked_chain_start = "https://postman-echo.com/redirect-to?" + urlencode(
            {"url": blocked_chain_second_hop}
        )
        blocked_chain = await run_case(
            root / "blocked-chain.sqlite3",
            name="blocked_multi_hop_redirect",
            destination=blocked_chain_destination,
            approved_hosts=["postman-echo.com", "httpbin.org"],
            start_url=blocked_chain_start,
        )
        if blocked_chain["status"] != "read_failed" or blocked_chain["final_url"] is not None:
            raise AssertionError(
                f"An unapproved destination in a redirect chain was not blocked: {blocked_chain}"
            )

    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "browser": "Microsoft Edge via managed Playwright worker",
        "fixture_service": "Postman Echo redirect-to endpoint",
        "cases": [allowed, blocked, subresources, approved_chain, blocked_chain],
        "source_sha256": {
            "browser_redirect_smoke.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "browser.py": hashlib.sha256(
                Path("engine/xaa/runtime/browser.py").read_bytes()
            ).hexdigest(),
            "browser_worker.py": hashlib.sha256(
                Path("engine/xaa/runtime/browser_worker.py").read_bytes()
            ).hexdigest(),
        },
    }
    destination = Path(".acceptance") / f"browser-redirect-edge-{uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(destination / "report.json")}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

"""Exercise managed-browser popup cleanup with the installed Microsoft Edge."""

from __future__ import annotations

import asyncio
import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from playwright.async_api import async_playwright
from xaa.runtime.browser_worker import close_unexpected_page, guard_browser_route


async def main() -> None:
    started = time.monotonic()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel="msedge",
            headless=True,
            args=[
                "--disable-quic",
                "--disable-background-networking",
                "--disable-webrtc",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--no-first-run",
            ],
        )
        browser_version = browser.version
        try:
            context = await browser.new_context(
                accept_downloads=False,
                service_workers="block",
                permissions=[],
                ignore_https_errors=False,
            )
            try:
                approved_hosts = {"example.com"}
                await context.route(
                    "**/*", lambda route: guard_browser_route(route, approved_hosts)
                )
                primary = await context.new_page()
                context.on("page", lambda opened: close_unexpected_page(primary, opened))
                await primary.set_content(
                    "<!doctype html><title>Popup lifecycle fixture</title><p>primary stays open</p>"
                )
                async with primary.expect_popup(timeout=5000) as popup_info:
                    await primary.evaluate("() => window.open('about:blank', '_blank')")
                popup = await popup_info.value
                if not popup.is_closed():
                    await popup.wait_for_event("close", timeout=5000)
                if popup.is_closed() is not True:
                    raise AssertionError("Managed browser left the unexpected Edge page open.")
                if primary.is_closed():
                    raise AssertionError("Managed browser closed the primary Edge page.")
                if await primary.title() != "Popup lifecycle fixture":
                    raise AssertionError("Primary Edge page was not retained after popup close.")
                await context.close()
            finally:
                if not context.is_closed():
                    await context.close()
        finally:
            await browser.close()

    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "browser": "Microsoft Edge via Playwright channel=msedge",
        "browser_version": browser_version,
        "platform": platform.platform(),
        "fixture": "about:blank popup opened by the primary page; no external navigation",
        "primary_page_retained": True,
        "unexpected_popup_closed": True,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "source_sha256": {
            "browser_popup_smoke.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "browser_worker.py": hashlib.sha256(
                Path("engine/xaa/runtime/browser_worker.py").read_bytes()
            ).hexdigest(),
        },
    }
    destination = Path(".acceptance") / f"browser-popup-edge-{uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(destination / "report.json")}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

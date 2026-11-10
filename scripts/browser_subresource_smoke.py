"""Verify live Edge blocks off-host page subresources for an approved site."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from playwright.async_api import async_playwright
from pydantic import BaseModel, ConfigDict
from xaa.runtime.browser import browser_worker_environment
from xaa.runtime.browser_worker import guard_browser_route


class BlockedSubresources(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    count: int
    resource_types: dict[str, int]


class BrowserWorkerResult(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    title: str
    content: str
    blocked_subresources: BlockedSubresources


class BrowserWorkerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    ok: bool
    result: BrowserWorkerResult | None = None
    error: str | None = None


async def verify_blocked_frame_reporting() -> tuple[str, dict[str, int]]:
    approved_hosts = {"developer.mozilla.org"}
    blocked_resources: dict[str, int] = {}
    blocked_main_navigations: dict[str, int] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            channel="msedge",
            headless=True,
            env=cast(dict[str, str | float | bool], browser_worker_environment()),
            args=["--disable-quic", "--disable-background-networking", "--no-first-run"],
        )
        try:
            context = await browser.new_context(
                accept_downloads=False,
                service_workers="block",
                permissions=[],
                ignore_https_errors=False,
            )
            try:
                page = await context.new_page()
                await context.route(
                    "**/*",
                    lambda route: guard_browser_route(
                        route,
                        approved_hosts,
                        blocked_resources,
                        main_frame=page.main_frame,
                        blocked_main_navigations=blocked_main_navigations,
                    ),
                )
                await page.goto(
                    "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/iframe",
                    wait_until="domcontentloaded",
                    timeout=20000,
                )
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2500)
                content = await page.locator("body").inner_text(timeout=5000)
                if "inline frame element" not in (await page.title()).lower():
                    raise AssertionError("The live iframe reference page did not load.")
                if "frame" not in blocked_resources or blocked_resources["frame"] < 1:
                    raise AssertionError(
                        "The Edge route guard did not report blocked iframe loads."
                    )
                if blocked_main_navigations:
                    raise AssertionError("The approved top-level page navigation was blocked.")
                if not content:
                    raise AssertionError("The live iframe reference page had no visible text.")
                return browser.version, dict(blocked_resources)
            finally:
                await context.close()
        finally:
            await browser.close()


def main() -> None:
    request = {
        "url": "https://en.wikipedia.org/wiki/HTTPS",
        "approved_hosts": ["en.wikipedia.org"],
        "links": [],
        "screenshot": False,
        "inspect_forms": False,
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PATH", "PYTHONPATH", "VIRTUAL_ENV"}
    }
    environment["PATH"] = os.path.join(os.environ["SystemRoot"], "System32")
    completed = subprocess.run(
        [sys.executable, "-I", "-m", "xaa.runtime.browser_worker"],
        input=json.dumps(request, separators=(",", ":")).encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=45,
        check=False,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode != 0:
        raise RuntimeError("The managed Edge worker exited without a result.")
    response = BrowserWorkerResponse.model_validate_json(completed.stdout)
    result = response.result
    if not response.ok or result is None:
        raise RuntimeError(f"The managed Edge navigation failed: {response.error!r}")
    blocked = result.blocked_subresources
    if (
        result.title != "HTTPS - Wikipedia"
        or "Hypertext Transfer Protocol Secure" not in result.content
        or blocked.count < 1
        or not blocked.resource_types
    ):
        raise AssertionError("The live page did not prove off-host subresources were blocked.")
    browser_version, blocked_frame_resources = asyncio.run(verify_blocked_frame_reporting())

    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "browser": "Microsoft Edge via the managed Playwright worker",
        "platform": platform.platform(),
        "approved_host": "en.wikipedia.org",
        "navigation_url": request["url"],
        "page_loaded": True,
        "off_host_subresources_blocked": True,
        "blocked_subresource_count": blocked.count,
        "blocked_resource_types": blocked.resource_types,
        "blocked_frame_page_browser_version": browser_version,
        "blocked_frame_check": "real Edge context using the shared route guard; blocked requests are aborted before network",
        "blocked_frame_page_resource_types": blocked_frame_resources,
        "blocked_child_frame_documents_reported": blocked_frame_resources.get("frame", 0) > 0,
        "model_inference_requested": False,
        "source_sha256": {
            "browser_subresource_smoke.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "browser.py": hashlib.sha256(
                Path("engine/xaa/runtime/browser.py").read_bytes()
            ).hexdigest(),
            "browser_worker.py": hashlib.sha256(
                Path("engine/xaa/runtime/browser_worker.py").read_bytes()
            ).hexdigest(),
        },
    }
    destination = Path(".acceptance") / f"browser-subresource-edge-{uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(destination / "report.json")}, indent=2))


if __name__ == "__main__":
    main()

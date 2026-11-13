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
from typing import Any, cast
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


async def verify_off_host_subresources_fixture() -> tuple[str, dict[str, int]]:
    """Test the page-level guard with a deterministic document in real Edge."""
    approved_hosts = {"xaa-approved.example"}
    blocked_resources: dict[str, int] = {}
    blocked_main_navigations: dict[str, int] = {}
    document = """
        <!doctype html><title>XAA subresource fixture</title>
        <p>approved document loaded</p>
        <img src="https://xaa-blocked.example/pixel.png" alt="blocked image">
        <script src="https://xaa-blocked.example/marker.js"></script>
        <iframe title="blocked frame" src="https://xaa-blocked.example/frame"></iframe>
        <script>document.body.dataset.thirdPartyScriptRan = String(window.xaaThirdPartyLoaded === true)</script>
    """
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
            )
            try:
                page = await context.new_page()

                async def route_request(route: Any) -> None:
                    if route.request.url == "https://xaa-approved.example/subresource-check":
                        await route.fulfill(status=200, content_type="text/html", body=document)
                        return
                    await guard_browser_route(
                        route,
                        approved_hosts,
                        blocked_resources,
                        main_frame=page.main_frame,
                        blocked_main_navigations=blocked_main_navigations,
                    )

                await context.route("**/*", route_request)
                await page.goto(
                    "https://xaa-approved.example/subresource-check",
                    wait_until="load",
                    timeout=10000,
                )
                if await page.title() != "XAA subresource fixture":
                    raise AssertionError("The approved deterministic page did not load.")
                if await page.locator("p").inner_text() != "approved document loaded":
                    raise AssertionError("The approved deterministic page text was not visible.")
                if blocked_resources != {"frame": 1, "image": 1, "script": 1}:
                    raise AssertionError(
                        f"Off-host fixture resources were not blocked: {blocked_resources}"
                    )
                if blocked_main_navigations:
                    raise AssertionError("The approved fixture navigation was blocked.")
                if (
                    await page.locator("body").get_attribute("data-third-party-script-ran")
                    != "false"
                ):
                    raise AssertionError("The blocked third-party script modified the page.")
                return browser.version, dict(blocked_resources)
            finally:
                await context.close()
        finally:
            await browser.close()


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


async def verify_nested_frame_resource_chain() -> tuple[str, dict[str, int]]:
    """Exercise frame and resource blocking in a three-level Edge frame tree."""
    approved_hosts = {"xaa-approved.example"}
    blocked_resources: dict[str, int] = {}
    blocked_main_navigations: dict[str, int] = {}
    documents = {
        "/root": """
            <!doctype html><title>XAA root fixture</title>
            <iframe title="approved child" src="https://xaa-approved.example/child"></iframe>
        """,
        "/child": """
            <!doctype html><title>XAA child fixture</title>
            <iframe title="approved grandchild" src="https://xaa-approved.example/grandchild"></iframe>
        """,
        "/grandchild": """
            <!doctype html><title>XAA grandchild fixture</title>
            <img src="https://xaa-blocked.example/pixel.png" alt="blocked image">
            <script src="https://xaa-blocked.example/marker.js"></script>
            <iframe title="blocked nested frame" src="https://xaa-blocked.example/frame"></iframe>
            <script>document.body.dataset.thirdPartyScriptRan = String(window.xaaThirdPartyLoaded === true)</script>
        """,
    }
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
            )
            try:
                page = await context.new_page()

                async def route_request(route: Any) -> None:
                    request_url = route.request.url
                    if request_url.startswith("https://xaa-approved.example/"):
                        path = request_url.removeprefix("https://xaa-approved.example")
                        body = documents.get(path)
                        if body is not None:
                            await route.fulfill(status=200, content_type="text/html", body=body)
                        else:
                            await route.fulfill(status=404, body="missing fixture")
                        return
                    await guard_browser_route(
                        route,
                        approved_hosts,
                        blocked_resources,
                        main_frame=page.main_frame,
                        blocked_main_navigations=blocked_main_navigations,
                    )

                await context.route("**/*", route_request)
                await page.goto(
                    "https://xaa-approved.example/root",
                    wait_until="load",
                    timeout=10000,
                )
                await page.wait_for_function(
                    "document.querySelector('iframe')?.contentDocument?.querySelector('iframe')?.contentDocument?.title === 'XAA grandchild fixture'",
                    timeout=5000,
                )
                await page.wait_for_timeout(100)
                frame_urls = {frame.url for frame in page.frames}
                expected_frames = {
                    "https://xaa-approved.example/root",
                    "https://xaa-approved.example/child",
                    "https://xaa-approved.example/grandchild",
                }
                if not expected_frames.issubset(frame_urls):
                    raise AssertionError(f"Approved nested frames did not all load: {frame_urls}")
                if blocked_resources != {"frame": 1, "image": 1, "script": 1}:
                    raise AssertionError(
                        f"Unexpected nested-frame denial evidence: {blocked_resources}"
                    )
                if blocked_main_navigations:
                    raise AssertionError("The approved top-level fixture navigation was blocked.")
                grandchild = next(
                    frame
                    for frame in page.frames
                    if frame.url == "https://xaa-approved.example/grandchild"
                )
                if (
                    await grandchild.locator("body").get_attribute("data-third-party-script-ran")
                    != "false"
                ):
                    raise AssertionError(
                        "A blocked third-party script changed the nested document."
                    )
                return browser.version, dict(blocked_resources)
            finally:
                await context.close()
        finally:
            await browser.close()


def main() -> None:
    browser_version, blocked_types = asyncio.run(verify_off_host_subresources_fixture())
    live_wikipedia_result: dict[str, object] | None = None
    if os.environ.get("XAA_BROWSER_LIVE_NETWORK") == "1":
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
        live_wikipedia_result = {
            "url": request["url"],
            "title": result.title,
            "blocked_subresources": blocked.count,
            "resource_types": blocked.resource_types,
        }
        live_browser_version, live_frame_resources = asyncio.run(verify_blocked_frame_reporting())
        live_wikipedia_result["frame_check_browser_version"] = live_browser_version
        live_wikipedia_result["frame_resource_types"] = live_frame_resources
    nested_browser_version, nested_blocked_resources = asyncio.run(
        verify_nested_frame_resource_chain()
    )

    report = {
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "browser": "Microsoft Edge via the managed Playwright worker",
        "platform": platform.platform(),
        "approved_host": "xaa-approved.example",
        "navigation_url": "https://xaa-approved.example/subresource-check",
        "page_source": "deterministic Edge page fixture",
        "page_title": "XAA subresource fixture",
        "page_loaded": True,
        "off_host_subresources_blocked": True,
        "blocked_resource_types": blocked_types,
        "page_check_browser_version": browser_version,
        "blocked_child_frame_documents_reported": blocked_types.get("frame", 0) > 0,
        "live_wikipedia_check": live_wikipedia_result,
        "nested_frame_chain_browser_version": nested_browser_version,
        "nested_frame_chain_depth": 3,
        "nested_frame_chain_off_host_blocked_resource_types": nested_blocked_resources,
        "nested_frame_chain_passed": nested_blocked_resources
        == {
            "frame": 1,
            "image": 1,
            "script": 1,
        },
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

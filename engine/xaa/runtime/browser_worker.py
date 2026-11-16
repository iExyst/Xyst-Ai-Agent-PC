"""Disposable, network-pinned Playwright worker for approved public web reads."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import socket
import sys
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from xaa.runtime.web_fetch import normalize_approved_hosts, validate_web_target

MAX_INPUT = 1_048_576
MAX_OUTPUT = 1_048_576
MAX_PAGE_TEXT = 20_000
MAX_SCREENSHOT = 512 * 1024
MAX_PROXY_BYTES = 2 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_PROXY_CONNECTIONS = 32


class BrowserFormValue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(max_length=4096)


class BrowserFormSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    form_index: int = Field(ge=0, lt=20)
    values: list[BrowserFormValue] = Field(min_length=1, max_length=20)
    submitter_label: str = Field(min_length=1, max_length=200)


class BrowserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=9, max_length=2048)
    approved_hosts: list[str] = Field(min_length=1, max_length=10)
    links: list[str] = Field(default_factory=list, max_length=5)
    screenshot: bool = False
    inspect_forms: bool = False
    submission: BrowserFormSubmission | None = None
    download_link_name: str | None = Field(default=None, min_length=1, max_length=200)
    download_directory: str | None = Field(default=None, min_length=1, max_length=2048)

    @field_validator("approved_hosts")
    @classmethod
    def clean_hosts(cls, value: list[str]) -> list[str]:
        return normalize_approved_hosts(value)

    @model_validator(mode="after")
    def validate_download_request(self) -> BrowserRequest:
        if (self.download_link_name is None) != (self.download_directory is None):
            raise ValueError(
                "Browser download link and managed destination must be supplied together."
            )
        return self

    @field_validator("links")
    @classmethod
    def clean_links(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("Link names cannot be empty.")
        if any(len(item) > 200 for item in cleaned):
            raise ValueError("Link names cannot exceed 200 characters.")
        return cleaned


def validate_url(url: str, hosts: set[str]) -> str:
    validate_web_target(url, hosts)
    if any(ord(char) < 32 for char in url):
        raise ValueError("Browser URL contains a control character.")
    return url


async def guard_browser_route(
    route: Any,
    hosts: set[str],
    blocked_subresources: dict[str, int] | None = None,
    *,
    main_frame: Any | None = None,
    blocked_main_navigations: dict[str, int] | None = None,
    submission_state: dict[str, Any] | None = None,
) -> None:
    try:
        method = route.request.method
        permitted_submission = False
        if method == "POST" and submission_state is not None:
            request_frame = getattr(route.request, "frame", None)
            is_top_level_navigation = main_frame is not None and (
                request_frame is main_frame
                or (
                    request_frame is not None
                    and route.request.is_navigation_request()
                    and request_frame.parent_frame is None
                )
            )
            permitted_submission = (
                submission_state.get("armed") is True
                and submission_state.get("used") is False
                and is_top_level_navigation
                and getattr(route.request, "resource_type", None) == "document"
                and route.request.is_navigation_request()
                and route.request.url == submission_state.get("target_url")
            )
            if permitted_submission:
                submission_state["used"] = True
        if method not in {"GET", "HEAD"} and not permitted_submission:
            raise PermissionError("Browser requests that can submit data are blocked.")
        validate_web_target(route.request.url, hosts)
    except (ValueError, PermissionError):
        resource_type = getattr(route.request, "resource_type", "document")
        request_frame = getattr(route.request, "frame", None)
        is_top_level_navigation = main_frame is not None and (
            request_frame is main_frame
            or (
                request_frame is not None
                and route.request.is_navigation_request()
                and request_frame.parent_frame is None
            )
        )
        if (
            resource_type == "document"
            and main_frame is not None
            and is_top_level_navigation
            and blocked_main_navigations is not None
        ):
            blocked_main_navigations["count"] = blocked_main_navigations.get("count", 0) + 1
        elif blocked_subresources is not None and resource_type != "document":
            blocked_subresources[resource_type] = blocked_subresources.get(resource_type, 0) + 1
        elif (
            blocked_subresources is not None
            and resource_type == "document"
            and main_frame is not None
            and not is_top_level_navigation
        ):
            blocked_subresources["frame"] = blocked_subresources.get("frame", 0) + 1
        await route.abort("blockedbyclient")
    else:
        await route.continue_()


def close_unexpected_page(main_page: Any, opened_page: Any) -> asyncio.Task[None] | None:
    """Close popups/new tabs; browser reads return content from the primary page only."""
    if opened_page is main_page:
        return None
    return asyncio.create_task(opened_page.close())


async def _submit_visible_form(
    page: Any,
    submission: BrowserFormSubmission,
    hosts: set[str],
    submission_state: dict[str, Any],
) -> list[str]:
    forms = page.locator("form")
    visible_forms: list[Any] = []
    for index in range(await forms.count()):
        form = forms.nth(index)
        if await form.is_visible():
            visible_forms.append(form)
            if len(visible_forms) >= 20:
                break
    if submission.form_index >= len(visible_forms):
        raise ValueError("The requested visible form does not exist.")
    form = visible_forms[submission.form_index]
    if await form.evaluate("form => form.method.toLowerCase()") != "post":
        raise PermissionError("Only POST forms can be submitted.")
    target = (await form.get_attribute("target") or "").strip().lower()
    if target not in {"", "_self"}:
        raise PermissionError("Forms targeting another window are blocked.")
    enctype = await form.evaluate("form => form.enctype.toLowerCase()")
    if enctype != "application/x-www-form-urlencoded":
        raise PermissionError("Only URL-encoded form submissions are supported.")
    if await form.locator('input[type="file"]').count():
        raise PermissionError("Forms containing file inputs cannot be submitted.")

    action_attribute = await form.get_attribute("action")
    page_url = cast(str, page.url)
    action_url: str = urljoin(page_url, action_attribute or page_url)
    action_parts = urlsplit(action_url)
    action_url = urlunsplit(
        (
            action_parts.scheme,
            action_parts.netloc,
            action_parts.path,
            action_parts.query,
            "",
        )
    )
    validate_url(action_url, hosts)
    page_host = (urlsplit(page_url).hostname or "").lower().rstrip(".")
    action_host: str = (action_parts.hostname or "").lower().rstrip(".")
    if not page_host or action_host != page_host:
        raise PermissionError("Form submission must stay on the current page hostname.")

    for item in submission.values:
        escaped_name = item.name.replace("\\", "\\\\").replace('"', '\\"')
        control = form.locator(f'[name="{escaped_name}"]')
        if await control.count() != 1 or not await control.is_visible():
            raise ValueError("Each supplied field name must match one visible form control.")
        details = await control.evaluate(
            """element => ({
              tag: element.tagName.toLowerCase(),
              type: (element.type || '').toLowerCase(),
              disabled: element.disabled === true,
              readOnly: element.readOnly === true,
              multiple: element.multiple === true
            })"""
        )
        if details["disabled"] or details["readOnly"]:
            raise PermissionError("Disabled and read-only form fields cannot be filled.")
        if details["tag"] == "textarea":
            await control.fill(item.value, timeout=5000)
        elif details["tag"] == "select":
            if details["multiple"]:
                raise PermissionError("Multiple-choice fields cannot be submitted.")
            await control.select_option(label=item.value, timeout=5000)
        elif details["tag"] == "input" and details["type"] in {
            "text",
            "email",
            "search",
            "tel",
            "url",
            "number",
            "date",
            "time",
            "month",
            "week",
            "datetime-local",
        }:
            await control.fill(item.value, timeout=5000)
        else:
            raise PermissionError("This form control type is not allowed for submission.")

    selectors = 'button, input[type="submit"], input[type="image"]'
    submitters = form.locator(selectors)
    matching: list[Any] = []
    for index in range(await submitters.count()):
        candidate = submitters.nth(index)
        if not await candidate.is_visible() or await candidate.is_disabled():
            continue
        overrides = await candidate.evaluate(
            """element => ({
              formAction: element.getAttribute('formaction'),
              formMethod: (element.getAttribute('formmethod') || '').toLowerCase(),
              formTarget: (element.getAttribute('formtarget') || '').toLowerCase(),
              type: (element.getAttribute('type') || (element.tagName.toLowerCase() === 'button' ? 'submit' : '')).toLowerCase(),
              labels: Array.from(element.labels || []).map(label => (label.innerText || '').trim()).filter(Boolean).join(' '),
              text: element.tagName.toLowerCase() === 'button' ? (element.innerText || '').trim() : '',
              value: element.value || '',
              ariaLabel: element.getAttribute('aria-label') || ''
            })"""
        )
        label = (
            overrides["labels"] or overrides["ariaLabel"] or overrides["text"] or overrides["value"]
        )
        if (
            overrides["type"] in {"submit", "image"}
            and not overrides["formAction"]
            and overrides["formMethod"] in {"", "post"}
            and overrides["formTarget"] in {"", "_self"}
            and label.strip() == submission.submitter_label
        ):
            matching.append(candidate)
    if len(matching) != 1:
        raise ValueError("The submitter label must identify exactly one visible form submitter.")

    submission_state["target_url"] = action_url
    submission_state["armed"] = True
    async with page.expect_navigation(wait_until="domcontentloaded", timeout=10000):
        await matching[0].click(timeout=8000)
    if submission_state.get("used") is not True:
        raise PermissionError("The form did not make its one permitted POST navigation.")
    return [item.name for item in submission.values]


async def resolve_public(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    answers = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = sorted(
        {address for answer in answers if isinstance((address := answer[4][0]), str)}
    )
    parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
    if not parsed_addresses or any(
        not address.is_global or address.is_multicast or address.is_unspecified
        for address in parsed_addresses
    ):
        raise PermissionError("Browser host resolved to a private or non-public address.")
    return addresses


async def _pump(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, budget: dict[str, int]
) -> None:
    while chunk := await reader.read(16384):
        budget["bytes"] += len(chunk)
        if budget["bytes"] > budget["max_bytes"]:
            break
        writer.write(chunk)
        await writer.drain()
    writer.close()


async def _proxy_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    hosts: set[str],
    budget: dict[str, int],
    forwarded_hosts: set[str],
) -> None:
    upstream_writer: asyncio.StreamWriter | None = None
    try:
        budget["connections"] += 1
        if budget["connections"] > MAX_PROXY_CONNECTIONS:
            return
        header = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), timeout=3)
        if len(header) > 8192:
            return
        request_line = header.split(b"\r\n", 1)[0].decode("ascii")
        method, authority, protocol = request_line.split(" ")
        if method != "CONNECT" or protocol not in {"HTTP/1.0", "HTTP/1.1"}:
            return
        host, separator, port = authority.rpartition(":")
        if not separator or port != "443" or not host:
            return
        hostname = host.strip("[]").encode("idna").decode("ascii").lower().rstrip(".")
        if hostname not in hosts:
            return
        addresses = await resolve_public(hostname)
        last_error: OSError | None = None
        for address in addresses:
            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(address, 443), timeout=5
                )
                break
            except OSError as error:
                last_error = error
        else:
            if last_error:
                raise last_error
            return
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_writer.drain()
        budget["forwarded_connections"] += 1
        forwarded_hosts.add(hostname)
        await asyncio.gather(
            _pump(client_reader, upstream_writer, budget),
            _pump(upstream_reader, client_writer, budget),
        )
    except (
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
        UnicodeError,
        ValueError,
        OSError,
        TimeoutError,
    ):
        pass
    finally:
        if upstream_writer is not None:
            upstream_writer.close()
            try:
                await upstream_writer.wait_closed()
            except OSError:
                pass
        client_writer.close()
        try:
            await client_writer.wait_closed()
        except OSError:
            pass


async def run(request: BrowserRequest) -> dict[str, Any]:
    from playwright.async_api import async_playwright

    hosts = set(request.approved_hosts)
    validate_url(request.url, hosts)
    budget = {
        "bytes": 0,
        "connections": 0,
        "forwarded_connections": 0,
        "max_bytes": MAX_PROXY_BYTES + MAX_DOWNLOAD_BYTES
        if request.download_directory is not None
        else MAX_PROXY_BYTES,
    }
    forwarded_hosts: set[str] = set()
    blocked_subresources: dict[str, int] = {}
    blocked_main_navigations = {"count": 0}
    blocked_main_hosts: list[str] = []
    submission_state: dict[str, Any] = {"armed": False, "used": False, "target_url": None}
    server = await asyncio.start_server(
        lambda reader, writer: _proxy_client(reader, writer, hosts, budget, forwarded_hosts),
        "127.0.0.1",
        0,
    )
    socket_info = server.sockets[0].getsockname()
    proxy_url = f"http://127.0.0.1:{socket_info[1]}"
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                channel="msedge",
                headless=True,
                proxy={"server": proxy_url, "bypass": "<-loopback>"},
                args=[
                    "--disable-quic",
                    "--disable-background-networking",
                    "--disable-webrtc",
                    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                    "--no-first-run",
                ],
            )
            try:
                context = await browser.new_context(
                    accept_downloads=request.download_directory is not None,
                    service_workers="block",
                    permissions=[],
                    ignore_https_errors=False,
                )
                try:
                    page = await context.new_page()

                    def record_unapproved_main_request(request: Any) -> None:
                        if not request.is_navigation_request():
                            return
                        request_url = getattr(request, "url", None)
                        if not isinstance(request_url, str):
                            return
                        hostname: str | None = urlsplit(request_url).hostname
                        if hostname is not None and hostname.lower().rstrip(".") not in hosts:
                            blocked_main_hosts.append(hostname.lower().rstrip("."))

                    page.on("request", record_unapproved_main_request)
                    await context.route(
                        "**/*",
                        lambda route: guard_browser_route(
                            route,
                            hosts,
                            blocked_subresources,
                            main_frame=page.main_frame,
                            blocked_main_navigations=blocked_main_navigations,
                            submission_state=submission_state,
                        ),
                    )
                    context.on("page", lambda opened: close_unexpected_page(page, opened))
                    try:
                        await page.goto(request.url, wait_until="domcontentloaded", timeout=15000)
                    except PlaywrightError as error:
                        if blocked_main_navigations["count"] or blocked_main_hosts:
                            raise PermissionError(
                                "Browser blocked a top-level navigation outside the approved host list."
                            ) from error
                        raise
                    if blocked_main_hosts:
                        raise PermissionError(
                            "Browser blocked a top-level navigation outside the approved host list."
                        )
                    for name in request.links:
                        link = page.get_by_role("link", name=name, exact=True)
                        if await link.count() != 1:
                            raise ValueError(
                                "Each requested link name must match exactly one visible link."
                            )
                        await link.click(timeout=8000)
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    if blocked_main_navigations["count"] or blocked_main_hosts:
                        raise PermissionError(
                            "Browser blocked a top-level navigation outside the approved host list."
                        )
                    submitted_fields: list[str] | None = None
                    if request.submission is not None:
                        submitted_fields = await _submit_visible_form(
                            page, request.submission, hosts, submission_state
                        )
                    download_info: dict[str, Any] | None = None
                    if request.download_link_name is not None:
                        if request.download_directory is None:
                            raise ValueError("A managed download directory is required.")
                        directory = Path(request.download_directory).resolve(strict=True)
                        if not directory.is_dir():
                            raise ValueError("Managed download destination is not a directory.")
                        link = page.get_by_role("link", name=request.download_link_name, exact=True)
                        if await link.count() != 1 or not await link.is_visible():
                            raise ValueError(
                                "Download link name must match exactly one visible link."
                            )
                        async with page.expect_download(timeout=10000) as pending_download:
                            await link.click(timeout=8000)
                        download = await pending_download.value
                        downloaded = directory / "download.bin"
                        await download.save_as(downloaded)
                        resolved = downloaded.resolve(strict=True)
                        if resolved.parent != directory or not resolved.is_file():
                            raise PermissionError("Browser download escaped managed staging.")
                        size = resolved.stat().st_size
                        if size > MAX_DOWNLOAD_BYTES:
                            resolved.unlink(missing_ok=True)
                            raise ValueError("Browser download exceeded the 8 MiB limit.")
                        digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
                        download_info = {
                            "suggested_filename": download.suggested_filename[:200],
                            "size_bytes": size,
                            "sha256": digest,
                        }
                    text = (await page.locator("body").inner_text(timeout=5000))[:MAX_PAGE_TEXT]
                    result: dict[str, Any] = {
                        "url": urlsplit(page.url)._replace(query="", fragment="").geturl(),
                        "title": (await page.title())[:500],
                        "content": text,
                        "truncated": len(text) >= MAX_PAGE_TEXT,
                        "submitted": submitted_fields is not None,
                        "submitted_fields": submitted_fields,
                        "trust": "untrusted_web_content",
                        "blocked_subresources": {
                            "count": sum(blocked_subresources.values()),
                            "resource_types": dict(sorted(blocked_subresources.items())),
                        },
                        "_proxy_diagnostics": {
                            "forwarded_connections": budget["forwarded_connections"],
                            "forwarded_hosts": sorted(forwarded_hosts),
                        },
                    }
                    if request.inspect_forms:
                        result["forms"] = await page.locator("form").evaluate_all(
                            """forms => {
                              const visible = element => element.getClientRects().length > 0;
                              let remaining = 100;
                              return forms.slice(0, 20).filter(visible).map((form, index) => {
                                const controls = Array.from(form.querySelectorAll('input, select, textarea'))
                                  .filter(visible).slice(0, remaining);
                                remaining -= controls.length;
                                const submitters = Array.from(form.querySelectorAll('button, input[type="submit"], input[type="image"]'))
                                  .filter(visible)
                                  .filter(control => {
                                    const type = (control.getAttribute('type') || (control.tagName.toLowerCase() === 'button' ? 'submit' : '')).toLowerCase();
                                    return type === 'submit' || type === 'image';
                                  })
                                  .slice(0, 20)
                                  .map(control => {
                                    const tag = control.tagName.toLowerCase();
                                    const type = (control.getAttribute('type') || 'submit').toLowerCase();
                                    const labels = Array.from(control.labels || [])
                                      .map(label => (label.innerText || '').trim()).filter(Boolean).join(' ');
                                    const text = tag === 'button' ? (control.innerText || '').trim() : '';
                                    return {
                                      tag,
                                      type,
                                      name: (control.getAttribute('name') || '').slice(0, 200),
                                      label: (labels || control.getAttribute('aria-label') || text || '').slice(0, 200),
                                      disabled: control.disabled,
                                      form_no_validate: control.formNoValidate
                                    };
                                  });
                                return {
                                  index,
                                  method: (form.getAttribute('method') || 'get').slice(0, 10).toLowerCase(),
                                  submitters,
                                  controls: controls.map(control => {
                                    const labels = Array.from(control.labels || [])
                                      .map(label => (label.innerText || '').trim()).filter(Boolean).join(' ');
                                    const tag = control.tagName.toLowerCase();
                                    const type = (control.getAttribute('type') || (tag === 'textarea' ? 'textarea' : tag)).slice(0, 30).toLowerCase();
                                    const options = tag === 'select'
                                      ? Array.from(control.options).slice(0, 20).map(option => (option.textContent || '').trim().slice(0, 200))
                                      : [];
                                    return {
                                      tag,
                                      type,
                                      name: (control.getAttribute('name') || '').slice(0, 200),
                                      label: (labels || control.getAttribute('aria-label') || '').slice(0, 200),
                                      required: control.hasAttribute('required'),
                                      disabled: control.disabled,
                                      read_only: control.readOnly === true,
                                      multiple: control.multiple === true,
                                      options
                                    };
                                  })
                                };
                              });
                            }"""
                        )
                    if download_info is not None:
                        result["download"] = download_info
                    if request.screenshot:
                        image = await page.screenshot(
                            type="png", full_page=False, animations="disabled"
                        )
                        if len(image) > MAX_SCREENSHOT:
                            raise ValueError("Browser screenshot exceeded its 512 KiB limit.")
                        result["screenshot_png"] = base64.b64encode(image).decode("ascii")
                    return result
                finally:
                    await context.close()
            finally:
                await browser.close()
    finally:
        server.close()
        await server.wait_closed()


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("Browser request exceeded its limit.")
        request = BrowserRequest.model_validate_json(raw)
        result = asyncio.run(run(request))
        encoded = json.dumps({"ok": True, "result": result}, separators=(",", ":")).encode()
        if len(encoded) > MAX_OUTPUT:
            raise ValueError("Browser output exceeded its limit.")
        sys.stdout.buffer.write(encoded)
    except (
        OSError,
        ValueError,
        PermissionError,
        TimeoutError,
        RuntimeError,
        ValidationError,
        PlaywrightError,
    ) as error:
        encoded = json.dumps(
            {"ok": False, "error": str(error)[:500]}, separators=(",", ":")
        ).encode()
        sys.stdout.buffer.write(encoded[:MAX_OUTPUT])


if __name__ == "__main__":
    main()

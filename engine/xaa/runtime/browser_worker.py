"""Disposable, network-pinned Playwright worker for approved public web reads."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import socket
import sys
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from xaa.runtime.web_fetch import normalize_approved_hosts, validate_web_target

MAX_INPUT = 1_048_576
MAX_OUTPUT = 1_048_576
MAX_PAGE_TEXT = 20_000
MAX_SCREENSHOT = 512 * 1024
MAX_PROXY_BYTES = 2 * 1024 * 1024
MAX_PROXY_CONNECTIONS = 32


class BrowserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(min_length=9, max_length=2048)
    approved_hosts: list[str] = Field(min_length=1, max_length=10)
    links: list[str] = Field(default_factory=list, max_length=5)
    screenshot: bool = False
    inspect_forms: bool = False

    @field_validator("approved_hosts")
    @classmethod
    def clean_hosts(cls, value: list[str]) -> list[str]:
        return normalize_approved_hosts(value)

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
        if budget["bytes"] > MAX_PROXY_BYTES:
            break
        writer.write(chunk)
        await writer.drain()
    writer.close()


async def _proxy_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    hosts: set[str],
    budget: dict[str, int],
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
    budget = {"bytes": 0, "connections": 0}
    server = await asyncio.start_server(
        lambda reader, writer: _proxy_client(reader, writer, hosts, budget), "127.0.0.1", 0
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
                    accept_downloads=False,
                    service_workers="block",
                    permissions=[],
                    ignore_https_errors=False,
                )
                try:

                    async def guard(route: Any) -> None:
                        try:
                            if route.request.method not in {"GET", "HEAD"}:
                                raise PermissionError(
                                    "Browser requests that can submit data are blocked."
                                )
                            validate_web_target(route.request.url, hosts)
                        except (ValueError, PermissionError):
                            await route.abort("blockedbyclient")
                        else:
                            await route.continue_()

                    await context.route("**/*", guard)
                    page = await context.new_page()
                    await page.goto(request.url, wait_until="domcontentloaded", timeout=15000)
                    for name in request.links:
                        link = page.get_by_role("link", name=name, exact=True)
                        if await link.count() != 1:
                            raise ValueError(
                                "Each requested link name must match exactly one visible link."
                            )
                        await link.click(timeout=8000)
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                    text = (await page.locator("body").inner_text(timeout=5000))[:MAX_PAGE_TEXT]
                    result: dict[str, Any] = {
                        "url": urlsplit(page.url)._replace(query="", fragment="").geturl(),
                        "title": (await page.title())[:500],
                        "content": text,
                        "truncated": len(text) >= MAX_PAGE_TEXT,
                        "trust": "untrusted_web_content",
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
                                return {
                                  index,
                                  method: (form.getAttribute('method') || 'get').slice(0, 10).toLowerCase(),
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
                                      options
                                    };
                                  })
                                };
                              });
                            }"""
                        )
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

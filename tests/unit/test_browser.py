from __future__ import annotations

import asyncio
import os
import socket
import sys
import time
from types import SimpleNamespace
from typing import Any

import pytest
from xaa.runtime import browser as browser_runtime
from xaa.runtime import browser_worker
from xaa.runtime.browser import (
    BrowserArguments,
    BrowserOutput,
    _execute_worker,
    _worker_environment,
)
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.web_fetch import validate_web_target
from xaa.storage.runs import RunStore


def test_browser_arguments_reject_unbounded_or_empty_link_actions() -> None:
    with pytest.raises(ValueError):
        BrowserArguments(url="https://docs.example.com", links=[""])
    with pytest.raises(ValueError):
        BrowserArguments(url="https://docs.example.com", links=["x"] * 6)


def test_browser_form_inspection_is_opt_in_and_output_contains_metadata_only() -> None:
    assert BrowserArguments(url="https://docs.example.com").inspect_forms is False
    assert BrowserArguments(url="https://docs.example.com", inspect_forms=True).inspect_forms
    payload = {
        "status": "ok",
        "trust": "untrusted_web_content",
        "forms": [
            {
                "index": 0,
                "method": "post",
                "controls": [
                    {
                        "tag": "input",
                        "type": "email",
                        "name": "email",
                        "label": "Email address",
                        "required": True,
                        "options": [],
                    }
                ],
            }
        ],
    }
    output = BrowserOutput.model_validate(payload)
    assert output.forms is not None
    assert output.forms[0].controls[0].label == "Email address"
    unsafe = {
        **payload,
        "forms": [
            {
                **payload["forms"][0],
                "controls": [{**payload["forms"][0]["controls"][0], "value": "private user input"}],
            }
        ],
    }
    with pytest.raises(ValueError, match="extra"):
        BrowserOutput.model_validate(unsafe)


def test_browser_worker_receives_only_os_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAA_TEST_BROWSER_SECRET", "must-not-be-inherited")
    assert "XAA_TEST_BROWSER_SECRET" not in _worker_environment()


def test_browser_url_uses_exact_approved_hosts_and_web_url_policy() -> None:
    hosts = {"docs.example.com"}
    assert browser_worker.validate_url("https://docs.example.com/guide", hosts)
    for url in (
        "http://docs.example.com/guide",
        "https://sub.docs.example.com/guide",
        "https://docs.example.com/?access_token=secret",
        "https://docs.example.com:444/guide",
    ):
        with pytest.raises((PermissionError, ValueError)):
            browser_worker.validate_url(url, hosts)
    with pytest.raises(PermissionError):
        validate_web_target("https://other.example.com/", hosts)


@pytest.mark.asyncio
async def test_browser_closes_new_pages_and_keeps_primary_page() -> None:
    primary = object()

    class Popup:
        closed = False

        async def close(self) -> None:
            self.closed = True

    popup = Popup()
    assert browser_worker.close_unexpected_page(primary, primary) is None
    task = browser_worker.close_unexpected_page(primary, popup)
    assert task is not None
    await task
    assert popup.closed


@pytest.mark.asyncio
async def test_browser_dns_rejects_mixed_public_and_private_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeLoop:
        async def getaddrinfo(self, host: str, port: int, *, type: int) -> list[Any]:
            del host, port, type
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
            ]

    monkeypatch.setattr(browser_worker.asyncio, "get_running_loop", lambda: FakeLoop())
    with pytest.raises(PermissionError, match="private or non-public"):
        await browser_worker.resolve_public("docs.example.com")


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "10.10.0.1",
        "100.64.0.1",
        "127.0.0.1",
        "169.254.169.254",
        "172.20.0.1",
        "192.0.2.1",
        "192.168.1.1",
        "224.0.0.1",
        "239.1.2.3",
        "::",
        "::1",
        "2001:db8::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
    ],
)
@pytest.mark.asyncio
async def test_browser_dns_rejects_non_global_address_classes(
    monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    family = socket.AF_INET6 if ":" in address else socket.AF_INET

    class FakeLoop:
        async def getaddrinfo(self, host: str, port: int, *, type: int) -> list[Any]:
            del host, port, type
            return [(family, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(browser_worker.asyncio, "get_running_loop", lambda: FakeLoop())
    with pytest.raises(PermissionError, match="private or non-public"):
        await browser_worker.resolve_public("docs.example.com")


@pytest.mark.parametrize(
    ("resource_type", "method", "url", "allowed"),
    [
        ("document", "GET", "https://docs.example.com/start", True),
        ("document", "GET", "https://outside.example.net/redirect-target", False),
        ("script", "GET", "https://outside.example.net/tracker.js", False),
        ("image", "HEAD", "https://docs.example.com/image.png", True),
        ("document", "POST", "https://docs.example.com/submit", False),
    ],
)
@pytest.mark.asyncio
async def test_browser_route_guard_applies_to_redirect_and_subresource_requests(
    resource_type: str, method: str, url: str, allowed: bool
) -> None:
    class FakeRequest:
        def __init__(self) -> None:
            self.resource_type = resource_type
            self.method = method
            self.url = url

    class FakeRoute:
        request = FakeRequest()
        action: tuple[str, str | None] | None = None

        async def abort(self, error_code: str) -> None:
            self.action = ("abort", error_code)

        async def continue_(self) -> None:
            self.action = ("continue", None)

    route = FakeRoute()
    blocked_subresources: dict[str, int] = {}
    await browser_worker.guard_browser_route(route, {"docs.example.com"}, blocked_subresources)
    assert route.action == (("continue", None) if allowed else ("abort", "blockedbyclient"))
    if resource_type == "script" and not allowed:
        assert blocked_subresources == {"script": 1}
    else:
        assert blocked_subresources == {}


@pytest.mark.asyncio
async def test_browser_cancellation_stops_owned_worker_process_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    if os.name != "nt":
        pytest.skip("Browser worker process-tree termination is Windows-specific.")
    marker = tmp_path / "child-running.txt"
    child_script = tmp_path / "child.py"
    pid_file = tmp_path / "child.pid"
    child_script.write_text(
        "from pathlib import Path\n"
        "import time\n"
        f"marker = Path({str(marker)!r})\n"
        "while True:\n"
        "    with marker.open('a', encoding='utf-8') as output:\n"
        "        output.write('alive\\n')\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    command_script = (
        "from pathlib import Path\n"
        "import subprocess, sys, time\n"
        f"child = subprocess.Popen([sys.executable, '-I', {str(child_script)!r}])\n"
        f"Path({str(pid_file)!r}).write_text(str(child.pid), encoding='ascii')\n"
        "time.sleep(60)\n"
    )
    original_create = asyncio.create_subprocess_exec
    spawned: list[asyncio.subprocess.Process] = []
    ready = asyncio.Event()

    async def spawn_worker(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
        del args
        process = await original_create(
            sys.executable,
            "-I",
            "-c",
            command_script,
            **kwargs,
        )
        spawned.append(process)
        ready.set()
        return process

    monkeypatch.setattr(
        browser_runtime,
        "asyncio",
        SimpleNamespace(
            create_subprocess_exec=spawn_worker,
            subprocess=asyncio.subprocess,
            timeout=asyncio.timeout,
        ),
    )
    task = asyncio.create_task(
        _execute_worker(
            {"url": "https://docs.example.com", "approved_hosts": ["docs.example.com"]},
            30,
        )
    )
    try:
        await asyncio.wait_for(ready.wait(), timeout=5)
        deadline = time.monotonic() + 5
        while (not pid_file.exists() or not marker.exists()) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        assert pid_file.exists() and marker.exists(), "The worker child did not start."
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        stopped_size = marker.stat().st_size
        await asyncio.sleep(0.25)
        assert marker.stat().st_size == stopped_size
        assert spawned[0].returncode is not None
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


def test_browser_tool_requires_distinct_run_grant_and_hosts(workspace_tmp_path) -> None:
    store = RunStore(workspace_tmp_path / "browser-grant.sqlite3")
    coordinator = Coordinator(store, object())  # type: ignore[arg-type]
    run = store.create_run(
        objective="Read docs",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        approved_web_hosts=["docs.example.com"],
    )
    names = {tool["function"]["name"] for tool in coordinator._tools(run["id"], run)}
    assert "browser_run" not in names
    browser_run = dict(run, approved_browser=True)
    assert "browser_run" in {
        tool["function"]["name"] for tool in coordinator._tools(run["id"], browser_run)
    }

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Self

import pytest
from xaa.runtime import browser as browser_runtime
from xaa.runtime import browser_worker
from xaa.runtime.browser import (
    BROWSER_DOWNLOAD_METADATA,
    BROWSER_METADATA,
    BROWSER_SUBMIT_METADATA,
    MAX_WORKER_BYTES,
    BrowserArguments,
    BrowserDownloadArguments,
    BrowserOutput,
    BrowserSubmitArguments,
    _bounded_result,
    _execute_worker,
    browser_worker_environment,
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
                        "disabled": False,
                        "read_only": False,
                        "multiple": False,
                        "options": [],
                    }
                ],
                "submitters": [
                    {
                        "tag": "button",
                        "type": "submit",
                        "name": "continue",
                        "label": "Continue",
                        "disabled": False,
                        "form_no_validate": False,
                    }
                ],
            }
        ],
    }
    output = BrowserOutput.model_validate(payload)
    assert output.forms is not None
    assert output.forms[0].controls[0].label == "Email address"
    assert output.forms[0].controls[0].disabled is False
    assert output.forms[0].submitters[0].label == "Continue"
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


def test_browser_submission_contract_is_bounded_and_has_separate_write_permission() -> None:
    valid = BrowserSubmitArguments(
        url="https://docs.example.com/contact",
        form_index=0,
        values=[{"name": "message", "value": "hello"}],
        submitter_label="Send",
    )
    assert valid.values[0].value == "hello"
    assert BROWSER_SUBMIT_METADATA["name"] == "browser.submit"
    assert BROWSER_SUBMIT_METADATA["permissions"] == ["web.browser.submit"]
    assert BROWSER_SUBMIT_METADATA["effect"] == "external_http_post"
    assert BROWSER_SUBMIT_METADATA["timeout_seconds"] == browser_runtime.BROWSER_TIMEOUT_SECONDS
    assert BROWSER_SUBMIT_METADATA["limits"]["total_value_bytes"] == 32 * 1024
    assert BROWSER_SUBMIT_METADATA["limits"]["tool_result_bytes"] == MAX_WORKER_BYTES
    assert {
        "run_id",
        "call_id",
        "tool",
        "version",
        "arguments",
        "result_or_failure",
    } <= set(BROWSER_SUBMIT_METADATA["audit"])
    assert "No automatic retry" in BROWSER_SUBMIT_METADATA["retry"]
    with pytest.raises(ValueError, match="unique"):
        BrowserSubmitArguments(
            url="https://docs.example.com/contact",
            form_index=0,
            values=[{"name": "a", "value": "one"}, {"name": "a", "value": "two"}],
            submitter_label="Send",
        )
    with pytest.raises(ValueError, match="32 KiB"):
        BrowserSubmitArguments(
            url="https://docs.example.com/contact",
            form_index=0,
            values=[{"name": f"a{index}", "value": "x" * 4096} for index in range(9)],
            submitter_label="Send",
        )


def test_browser_download_contract_uses_a_separate_bounded_managed_artifact_grant() -> None:
    arguments = BrowserDownloadArguments(
        url="https://docs.example.com/files",
        link_name="Download report",
    )
    assert arguments.link_name == "Download report"
    assert BROWSER_DOWNLOAD_METADATA["name"] == "browser.download"
    assert BROWSER_DOWNLOAD_METADATA["permissions"] == ["web.browser.download"]
    assert BROWSER_DOWNLOAD_METADATA["effect"] == "managed_artifact_write"
    assert BROWSER_DOWNLOAD_METADATA["limits"]["download_bytes"] == 8 * 1024 * 1024
    assert BrowserOutput(status="download_failed", trust="untrusted_web_content").status == (
        "download_failed"
    )
    with pytest.raises(ValueError):
        BrowserDownloadArguments(url="https://docs.example.com", link_name=" ")


def test_browser_contract_bounds_the_model_facing_result() -> None:
    assert BROWSER_METADATA["output_size_bytes"] == MAX_WORKER_BYTES
    assert BROWSER_METADATA["limits"]["tool_result_bytes"] == MAX_WORKER_BYTES
    assert BROWSER_METADATA["limits"]["option_characters"] == 200
    assert BROWSER_METADATA["limits"]["submitters_per_form"] == 20
    assert BrowserOutput(status="submission_failed", trust="untrusted_web_content").status == (
        "submission_failed"
    )
    output_properties = BROWSER_METADATA["output_schema"]["properties"]
    assert output_properties["url"]["anyOf"][0]["maxLength"] == 2048
    assert output_properties["title"]["anyOf"][0]["maxLength"] == 500
    assert (
        BROWSER_METADATA["output_schema"]["$defs"]["BrowserFormControl"]["properties"]["options"][
            "items"
        ]["maxLength"]
        == 200
    )
    oversized = _bounded_result(
        {"status": "ok", "trust": "untrusted_web_content", "content": "x" * MAX_WORKER_BYTES}
    )
    assert oversized["status"] == "read_failed"
    assert len(json.dumps(oversized).encode("ascii")) <= MAX_WORKER_BYTES
    uncertain_submission = _bounded_result(
        {"status": "ok", "trust": "untrusted_web_content", "content": "x" * MAX_WORKER_BYTES},
        failure_status="submission_failed",
        failure_error="The site may have received this submission.",
    )
    assert uncertain_submission["status"] == "submission_failed"
    assert "may have received" in uncertain_submission["error"]


def test_browser_worker_receives_only_os_runtime_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XAA_TEST_BROWSER_SECRET", "must-not-be-inherited")
    assert "XAA_TEST_BROWSER_SECRET" not in browser_worker_environment()


@pytest.mark.asyncio
async def test_browser_timeout_returns_actionable_error_and_journals_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = RunStore(tmp_path / "browser-timeout.sqlite3")
    run = store.create_run(
        objective="Verify managed browser timeout handling",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
        approved_web_hosts=["docs.example.com"],
        approved_browser=True,
    )
    token = store.acquire_lease(run["id"], "browser-timeout-test", seconds=30)
    assert token is not None

    async def timeout_worker(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        del payload, timeout
        raise TimeoutError

    monkeypatch.setattr(browser_runtime, "_execute_worker", timeout_worker)
    result = await browser_runtime.run_approved_browser(
        store,
        run["id"],
        token,
        "browser-timeout-call",
        {"url": "https://docs.example.com/slow"},
        ["docs.example.com"],
        deadline=time.monotonic() + 5,
    )

    assert result == {
        "status": "read_failed",
        "error": "Browser navigation exceeded its time limit.",
        "trust": "untrusted_web_content",
    }
    with closing(sqlite3.connect(tmp_path / "browser-timeout.sqlite3")) as connection:
        row = connection.execute(
            "SELECT state, result FROM tool_calls WHERE provider_call_id=?",
            ("browser-timeout-call",),
        ).fetchone()
    assert row is not None and row[0] == "completed"
    assert json.loads(row[1]) == result


@pytest.mark.asyncio
async def test_browser_download_commits_only_bounded_bytes_to_managed_run_artifacts(
    monkeypatch: pytest.MonkeyPatch, workspace_tmp_path
) -> None:
    store = RunStore(workspace_tmp_path / "browser-download.sqlite3")
    run = store.create_run(
        objective="Download the report",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path / "project"),
        mode="observe",
        model=None,
        approved_web_hosts=["docs.example.com"],
        approved_browser=True,
        approved_browser_download=True,
    )
    token = store.acquire_lease(run["id"], "browser-download-test", seconds=30)
    assert token is not None
    payload_bytes = b"untrusted downloaded report\n"

    async def worker(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        del timeout
        assert payload["download_link_name"] == "Download report"
        staging = Path(payload["download_directory"])
        (staging / "download.bin").write_bytes(payload_bytes)
        return {
            "url": "https://docs.example.com/files/report.txt",
            "title": "Reports",
            "content": "Download started.",
            "download": {
                "suggested_filename": "../../report.txt",
                "size_bytes": len(payload_bytes),
                "sha256": hashlib.sha256(payload_bytes).hexdigest(),
            },
        }

    monkeypatch.setattr(browser_runtime, "_execute_worker", worker)
    result = await browser_runtime.run_approved_browser_download(
        store,
        run["id"],
        token,
        "browser-download-call",
        {"url": "https://docs.example.com/files", "link_name": "Download report"},
        ["docs.example.com"],
        deadline=time.monotonic() + 5,
    )

    artifact_result = result["download_artifact"]
    artifact = store.get_artifact(artifact_result["id"])
    assert result["status"] == "ok"
    assert artifact is not None
    assert artifact["media_type"] == "application/octet-stream"
    assert artifact["provenance"] == "browser.download:report.txt"
    saved_file = workspace_tmp_path / "artifacts" / artifact["storage_key"]
    assert saved_file.read_bytes() == payload_bytes
    assert not (workspace_tmp_path / "project" / "report.txt").exists()
    with closing(sqlite3.connect(workspace_tmp_path / "browser-download.sqlite3")) as connection:
        row = connection.execute(
            "SELECT result FROM tool_calls WHERE provider_call_id=?",
            ("browser-download-call",),
        ).fetchone()
    assert row is not None and json.loads(row[0])["download_artifact"]["id"] == artifact["id"]


@pytest.mark.asyncio
async def test_private_proxy_diagnostics_are_removed_from_browser_tool_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    store = RunStore(tmp_path / "browser-proxy-diagnostics.sqlite3")
    run = store.create_run(
        objective="Verify private proxy diagnostics stay internal",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
        approved_web_hosts=["docs.example.com"],
        approved_browser=True,
    )
    token = store.acquire_lease(run["id"], "browser-proxy-diagnostics-test", seconds=30)
    assert token is not None

    async def worker(payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        del payload, timeout
        return {
            "url": "https://docs.example.com/",
            "title": "Docs",
            "content": "public page",
            "blocked_subresources": {"count": 0, "resource_types": {}},
            "_proxy_diagnostics": {
                "forwarded_connections": 1,
                "forwarded_hosts": ["docs.example.com"],
            },
        }

    monkeypatch.setattr(browser_runtime, "_execute_worker", worker)
    diagnostics: dict[str, Any] = {}
    result = await browser_runtime.run_approved_browser(
        store,
        run["id"],
        token,
        "browser-proxy-diagnostics-call",
        {"url": "https://docs.example.com/"},
        ["docs.example.com"],
        deadline=time.monotonic() + 5,
        proxy_diagnostics=diagnostics,
    )

    assert result["status"] == "ok"
    assert "_proxy_diagnostics" not in result
    assert diagnostics == {
        "forwarded_connections": 1,
        "forwarded_hosts": ["docs.example.com"],
    }
    with closing(sqlite3.connect(tmp_path / "browser-proxy-diagnostics.sqlite3")) as connection:
        row = connection.execute(
            "SELECT result FROM tool_calls WHERE provider_call_id=?",
            ("browser-proxy-diagnostics-call",),
        ).fetchone()
    assert row is not None and "_proxy_diagnostics" not in json.loads(row[0])


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


def test_browser_worker_requires_download_link_and_staging_directory_together(tmp_path) -> None:
    with pytest.raises(ValueError, match="supplied together"):
        browser_worker.BrowserRequest(
            url="https://docs.example.com/files",
            approved_hosts=["docs.example.com"],
            download_link_name="Download report",
        )
    request = browser_worker.BrowserRequest(
        url="https://docs.example.com/files",
        approved_hosts=["docs.example.com"],
        download_link_name="Download report",
        download_directory=str(tmp_path),
    )
    assert request.download_link_name == "Download report"


@pytest.mark.asyncio
async def test_browser_worker_saves_only_the_requested_link_to_managed_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    download_directory = tmp_path / "managed-downloads"
    download_directory.mkdir()
    payload_bytes = b"bounded browser download\n"
    context_options: dict[str, Any] = {}

    class FakeLink:
        async def count(self) -> int:
            return 1

        async def is_visible(self) -> bool:
            return True

        async def click(self, *, timeout: int) -> None:
            assert timeout == 8000

    class FakeLocator:
        async def inner_text(self, *, timeout: int) -> str:
            assert timeout == 5000
            return "Report page"

    class FakeRequest:
        def is_navigation_request(self) -> bool:
            return True

        frame = None

    class FakeResponse:
        status = 200
        url = "https://docs.example.com/files/report.txt"
        request = FakeRequest()

        async def all_headers(self) -> dict[str, str]:
            return {"content-type": "text/plain", "content-length": str(len(payload_bytes))}

        async def body(self) -> bytes:
            return payload_bytes

    class FakePage:
        main_frame = FakeRequest.frame
        url = "https://docs.example.com/files"

        def on(self, *_: object) -> None:
            return None

        async def goto(self, *_: object, **__: object) -> None:
            return None

        def get_by_role(self, *_: object, **__: object) -> FakeLink:
            return FakeLink()

        async def wait_for_event(self, event: str, *, predicate: Any = None, timeout: int) -> Any:
            assert timeout == 10000
            if event == "response":
                response = FakeResponse()
                assert predicate(response)
                return response
            assert event == "download"
            await asyncio.Future()

        def locator(self, selector: str) -> FakeLocator:
            assert selector == "body"
            return FakeLocator()

        async def title(self) -> str:
            return "Reports"

    class FakeContext:
        async def new_page(self) -> FakePage:
            return FakePage()

        async def route(self, *_: object) -> None:
            return None

        def on(self, *_: object) -> None:
            return None

        async def close(self) -> None:
            return None

    class FakeBrowser:
        async def new_context(self, **options: Any) -> FakeContext:
            context_options.update(options)
            return FakeContext()

        async def close(self) -> None:
            return None

    class FakeChromium:
        async def launch(self, **_: Any) -> FakeBrowser:
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("playwright.async_api.async_playwright", FakePlaywright)
    result = await browser_worker.run(
        browser_worker.BrowserRequest(
            url="https://docs.example.com/files",
            approved_hosts=["docs.example.com"],
            download_link_name="Download report",
            download_directory=str(download_directory),
        )
    )

    saved_path = download_directory / "download.bin"
    assert context_options["accept_downloads"] is True
    assert saved_path.read_bytes() == payload_bytes
    assert result["download"]["suggested_filename"] == "report.txt"
    assert result["download"]["size_bytes"] == len(payload_bytes)
    assert result["download"]["sha256"] == hashlib.sha256(payload_bytes).hexdigest()


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
async def test_browser_route_guard_allows_exactly_one_armed_same_url_document_post() -> None:
    class FakeFrame:
        parent_frame = None

    main_frame = FakeFrame()
    url = "https://docs.example.com/contact"

    class FakeRequest:
        def __init__(self, resource_type: str = "document", navigation: bool = True) -> None:
            self.method = "POST"
            self.resource_type = resource_type
            self.frame = main_frame
            self.url = url
            self._navigation = navigation

        def is_navigation_request(self) -> bool:
            return self._navigation

    class FakeRoute:
        def __init__(self, request: FakeRequest) -> None:
            self.request = request
            self.action: tuple[str, str | None] | None = None

        async def abort(self, error_code: str) -> None:
            self.action = ("abort", error_code)

        async def continue_(self) -> None:
            self.action = ("continue", None)

    state: dict[str, Any] = {"armed": True, "used": False, "target_url": url}
    first = FakeRoute(FakeRequest())
    await browser_worker.guard_browser_route(
        first, {"docs.example.com"}, main_frame=main_frame, submission_state=state
    )
    assert first.action == ("continue", None)
    assert state["used"] is True

    replay = FakeRoute(FakeRequest())
    await browser_worker.guard_browser_route(
        replay, {"docs.example.com"}, main_frame=main_frame, submission_state=state
    )
    assert replay.action == ("abort", "blockedbyclient")

    xhr_state = {"armed": True, "used": False, "target_url": url}
    xhr = FakeRoute(FakeRequest(resource_type="xhr", navigation=False))
    await browser_worker.guard_browser_route(
        xhr, {"docs.example.com"}, main_frame=main_frame, submission_state=xhr_state
    )
    assert xhr.action == ("abort", "blockedbyclient")


@pytest.mark.parametrize("is_main_frame", [True, False])
@pytest.mark.asyncio
async def test_browser_route_guard_reports_blocked_child_frames_separately_from_main_navigation(
    is_main_frame: bool,
) -> None:
    class FakeFrame:
        def __init__(self, parent_frame=None) -> None:
            self.parent_frame = parent_frame

    main_frame = FakeFrame()

    class FakeRequest:
        resource_type = "document"
        method = "GET"
        url = "https://outside.example.net/redirect-target"
        frame = main_frame if is_main_frame else FakeFrame(main_frame)

        def is_navigation_request(self) -> bool:
            return True

    class FakeRoute:
        request = FakeRequest()
        action: tuple[str, str | None] | None = None

        async def abort(self, error_code: str) -> None:
            self.action = ("abort", error_code)

        async def continue_(self) -> None:
            self.action = ("continue", None)

    blocked_main_navigations: dict[str, int] = {}
    blocked_subresources: dict[str, int] = {}
    await browser_worker.guard_browser_route(
        FakeRoute(),
        {"docs.example.com"},
        blocked_subresources,
        main_frame=main_frame,
        blocked_main_navigations=blocked_main_navigations,
    )
    assert blocked_main_navigations == ({"count": 1} if is_main_frame else {})
    assert blocked_subresources == ({} if is_main_frame else {"frame": 1})


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
    assert "browser_submit_form" not in names
    assert "browser_download_file" not in names
    browser_run = dict(run, approved_browser=True)
    exposed = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in coordinator._tools(run["id"], browser_run)
    }
    assert exposed["browser_run"] == BROWSER_METADATA["input_schema"]
    assert "browser_submit_form" not in exposed
    assert "browser_download_file" not in exposed
    submission_run = dict(browser_run, approved_browser_submission=True)
    submission_tools = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in coordinator._tools(run["id"], submission_run)
    }
    assert submission_tools["browser_submit_form"] == BROWSER_SUBMIT_METADATA["input_schema"]
    download_run = dict(browser_run, approved_browser_download=True)
    download_tools = {
        tool["function"]["name"]: tool["function"]["parameters"]
        for tool in coordinator._tools(run["id"], download_run)
    }
    assert "browser_submit_form" not in download_tools
    assert download_tools["browser_download_file"] == BROWSER_DOWNLOAD_METADATA["input_schema"]

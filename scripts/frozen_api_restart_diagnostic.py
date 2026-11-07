"""Restart the frozen API around an unresolved filesystem create."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from keyring.errors import KeyringError
from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE, CredentialStore
from xaa.storage.runs import RunStore

CRASH_DIRECTORY_PARTS = tuple(f"dir{index:02d}" for index in range(24))
CREATE_PATH = "/".join((*CRASH_DIRECTORY_PARTS, "created.txt"))
CREATE_CONTENT = "frozen-api-crash-boundary"
WRITE_BEFORE = b"frozen-api-write-before"
WRITE_CONTENT = "frozen-api-write-after:" + "z" * (65_536 - len("frozen-api-write-after:"))


class FixtureLMStudio(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, scenario: str) -> None:
        super().__init__(("127.0.0.1", 0), FixtureLMStudioHandler)
        self.scenario = scenario
        self.workspace_tool_requests = 0


class FixtureLMStudioHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, value: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/v1/models":
            self._json({"error": "not found"}, 404)
            return
        self._json({"data": [{"id": "restart-diagnostic-model"}]})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 1_048_576:
            self._json({"error": "invalid request size"}, 413)
            return
        request = cast(dict[str, Any], json.loads(self.rfile.read(length)))
        messages = cast(list[dict[str, Any]], request.get("messages", []))
        tools = cast(list[dict[str, Any]], request.get("tools", []))
        tool_names: set[str] = set()
        for tool in tools:
            function = cast(dict[str, Any], tool.get("function", {}))
            name = function.get("name")
            if isinstance(name, str):
                tool_names.add(name)
        if messages and messages[-1].get("role") == "tool":
            last = messages[-1]
            message = {"role": "assistant", "content": str(last.get("content", ""))}
            finish_reason = "stop"
        elif "xaa_echo" in tool_names:
            last = messages[-1] if messages else {}
            user_content = next(
                (
                    str(item.get("content", ""))
                    for item in reversed(messages)
                    if item.get("role") == "user"
                ),
                "",
            )
            match = re.search(r"Call xaa_echo with value ([a-f0-9]+),", user_content)
            if match is None:
                self._json({"error": "unexpected diagnostic prompt"}, 400)
                return
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "frozen-diagnostic-echo",
                        "type": "function",
                        "function": {
                            "name": "xaa_echo",
                            "arguments": json.dumps({"value": match.group(1)}),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        elif (
            "workspace_create_file" in tool_names
            and cast(FixtureLMStudio, self.server).scenario == "create"
        ):
            fixture = cast(FixtureLMStudio, self.server)
            fixture.workspace_tool_requests += 1
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "frozen-restart-create-1",
                        "type": "function",
                        "function": {
                            "name": "workspace_create_file",
                            "arguments": json.dumps(
                                {"path": CREATE_PATH, "content": CREATE_CONTENT}
                            ),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        elif (
            "workspace_write_file" in tool_names
            and cast(FixtureLMStudio, self.server).scenario == "write_result_crash"
        ):
            fixture = cast(FixtureLMStudio, self.server)
            fixture.workspace_tool_requests += 1
            message = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "frozen-restart-write-1",
                        "type": "function",
                        "function": {
                            "name": "workspace_write_file",
                            "arguments": json.dumps(
                                {
                                    "path": "fixture.txt",
                                    "content": WRITE_CONTENT,
                                    "expected_sha256": hashlib.sha256(WRITE_BEFORE).hexdigest(),
                                }
                            ),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            self._json({"error": "unexpected chat request"}, 400)
            return
        self._json(
            {
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        )


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_for_api(client: httpx.Client, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            response = client.get("/health", timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError("Frozen authenticated API did not become ready.")


def start_api(
    executable: Path,
    root: Path,
    api_port: int,
    lm_port: int,
    token: str,
    phase: str,
) -> subprocess.Popen[bytes]:
    log = (root / f"frozen-api-{phase}.log").open("wb")
    environment = os.environ.copy()
    for name in ("XAA_API_TOKEN", "XAA_API_PORT", "XAA_DATA_DIR", "LM_STUDIO_URL"):
        environment.pop(name, None)
    environment.update(
        {
            "XAA_API_TOKEN": token,
            "XAA_API_PORT": str(api_port),
            "XAA_DATA_DIR": str(root / "data"),
            "LM_STUDIO_URL": f"http://127.0.0.1:{lm_port}",
        }
    )
    try:
        return subprocess.Popen(
            [str(executable)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    finally:
        log.close()


def kill_api_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name != "nt":
        raise RuntimeError("Frozen API tree termination requires Windows.")
    result = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0 and process.poll() is None:
        raise RuntimeError("Could not terminate the frozen API and worker process tree.")
    process.wait(timeout=8)


def clean_launch_credentials(store: CredentialStore, token: str, api_url: str) -> bool:
    clean = True
    for profile, expected in (
        (API_LAUNCH_TOKEN_PROFILE, token),
        (API_LAUNCH_URL_PROFILE, api_url),
    ):
        try:
            if store.get(profile) == expected:
                store.delete(profile)
            elif store.get(profile) is not None:
                clean = False
        except KeyringError:
            clean = False
    return clean


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("Frozen API restart acceptance requires Windows.")
    executable = Path("build/native-dev/xaa-engine.exe").resolve()
    if not executable.is_file():
        raise FileNotFoundError("Build the frozen engine with npm run build:native:dev first.")
    credential_store = CredentialStore()
    if credential_store.get(API_LAUNCH_TOKEN_PROFILE) or credential_store.get(
        API_LAUNCH_URL_PROFILE
    ):
        raise RuntimeError("An API launch credential already exists; refusing to overwrite it.")

    scenario = os.environ.get("XAA_FROZEN_API_RESTART_SCENARIO", "create")
    if scenario not in {"create", "write_result_crash"}:
        raise ValueError("XAA_FROZEN_API_RESTART_SCENARIO must be create or write_result_crash.")

    root = (Path(".acceptance") / f"frozen-api-restart-{uuid4()}").resolve()
    root.mkdir(parents=True)
    workspace = root / "workspace"
    workspace.mkdir()
    if scenario == "create":
        workspace.joinpath(*CRASH_DIRECTORY_PARTS).mkdir(parents=True)
    else:
        (workspace / "fixture.txt").write_bytes(WRITE_BEFORE)
    token = secrets.token_urlsafe(48)
    api_port = free_port()
    fixture = FixtureLMStudio(scenario)
    lm_port = fixture.server_address[1]
    fake_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fake_thread.start()
    base_url = f"http://127.0.0.1:{api_port}"
    client = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})
    first: subprocess.Popen[bytes] | None = None
    second: subprocess.Popen[bytes] | None = None
    monitor: sqlite3.Connection | None = None
    lock: sqlite3.Connection | None = None
    target = (
        workspace.joinpath(*CRASH_DIRECTORY_PARTS, "created.txt")
        if scenario == "create"
        else workspace / "fixture.txt"
    )
    target_mtime: int | None = None
    report: dict[str, Any] = {
        "passed": False,
        "scenario": f"frozen API plus worker crash during {scenario}",
        "provider": "local OpenAI-compatible fixture; no LM Studio server/model used",
        "frozen_engine_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    try:
        first = start_api(executable, root, api_port, lm_port, token, "crash")
        wait_for_api(client, time.monotonic() + 30)
        response = client.post(
            "/runs",
            json={
                "objective": "Perform the frozen restart diagnostic filesystem action.",
                "acceptance_criteria": ["The interrupted create remains unconfirmed."],
                "workspace": str(workspace),
                "mode": "workspace",
                "model": "restart-diagnostic-model",
                "max_iterations": 3,
                "max_seconds": 180,
            },
            timeout=10,
        )
        response.raise_for_status()
        run_id = response.json()["id"]
        database = root / "data" / "XAA" / "xaa.sqlite3"
        journal = RunStore(database)
        monitor = sqlite3.connect(database, timeout=5)
        lock = sqlite3.connect(database, timeout=5, isolation_level=None)
        lock_deadline = time.monotonic() + 45
        while time.monotonic() < lock_deadline:
            if first.poll() is not None:
                raise RuntimeError("Frozen API exited before its create boundary.")
            event_types = {
                row[0]
                for row in monitor.execute(
                    "SELECT DISTINCT event_type FROM run_events WHERE run_id=? "
                    "AND event_type IN ('tool.intended','tool.executing','tool.completed')",
                    (run_id,),
                )
            }
            if scenario == "write_result_crash" and "tool.executing" in event_types:
                assert lock is not None
                lock.execute("BEGIN IMMEDIATE")
                if "tool.completed" in event_types:
                    raise RuntimeError(
                        "Frozen write completed before the journal lock was acquired."
                    )
                if target.read_bytes() == WRITE_CONTENT.encode():
                    time.sleep(0.1)
                    event_types = {
                        row[0]
                        for row in monitor.execute(
                            "SELECT DISTINCT event_type FROM run_events WHERE run_id=? "
                            "AND event_type IN ('tool.executing','tool.completed')",
                            (run_id,),
                        )
                    }
                    if (
                        "tool.executing" not in event_types
                        or "tool.completed" in event_types
                        or len(journal.unresolved_tool_calls(run_id)) != 1
                        or target.read_bytes() != WRITE_CONTENT.encode()
                    ):
                        raise RuntimeError(
                            "Frozen write did not pause after fsync before journaling."
                        )
                    target_mtime = target.stat().st_mtime_ns
                    kill_api_tree(first)
                    if first.returncode in (0, None):
                        raise RuntimeError("Frozen API did not terminate after the fsynced write.")
                    lock.rollback()
                    lock.close()
                    lock = None
                    time.sleep(0.2)
                    post_kill_events = journal.list_events(run_id)
                    if (
                        any(event["event_type"] == "tool.completed" for event in post_kill_events)
                        or len(journal.unresolved_tool_calls(run_id)) != 1
                        or target.read_bytes() != WRITE_CONTENT.encode()
                        or target.stat().st_mtime_ns != target_mtime
                    ):
                        raise RuntimeError(
                            "Frozen write advanced after its process tree was killed."
                        )
                    break
            elif scenario == "create" and "tool.intended" in event_types:
                assert lock is not None
                lock.execute("BEGIN IMMEDIATE")
                if "tool.executing" in event_types or "tool.completed" in event_types:
                    raise RuntimeError(
                        "Frozen create advanced before the result lock was acquired."
                    )
                size = target.stat().st_size if target.exists() else None
                if size == 0:
                    time.sleep(0.2)
                    event_types = {
                        row[0]
                        for row in monitor.execute(
                            "SELECT DISTINCT event_type FROM run_events WHERE run_id=? "
                            "AND event_type IN ('tool.executing','tool.completed')",
                            (run_id,),
                        )
                    }
                    if (
                        event_types
                        or len(journal.unresolved_tool_calls(run_id)) != 1
                        or target.stat().st_size != 0
                    ):
                        raise RuntimeError("Frozen worker advanced past the crash boundary.")
                    target_mtime = target.stat().st_mtime_ns
                    kill_api_tree(first)
                    if first.returncode in (0, None):
                        raise RuntimeError("Frozen API did not terminate at the boundary.")
                    lock.rollback()
                    lock.close()
                    lock = None
                    time.sleep(0.2)
                    post_kill_events = journal.list_events(run_id)
                    if (
                        any(
                            event["event_type"] in {"tool.executing", "tool.completed"}
                            for event in post_kill_events
                        )
                        or len(journal.unresolved_tool_calls(run_id)) != 1
                        or target.read_bytes() != b""
                        or target.stat().st_mtime_ns != target_mtime
                    ):
                        raise RuntimeError(
                            "Frozen worker advanced after its process tree was killed."
                        )
                    break
                lock.rollback()
                if "tool.executing" in event_types or "tool.completed" in event_types:
                    raise RuntimeError(
                        "Frozen create advanced before the boundary lock was acquired."
                    )
            if scenario == "write_result_crash":
                time.sleep(0.001)
        else:
            raise TimeoutError("Frozen API never reached the selected filesystem crash boundary.")

        saved = journal.get_run(run_id)
        if saved is None:
            raise RuntimeError("Frozen run disappeared after API termination.")
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), run_id),
            )
        second = start_api(executable, root, api_port, lm_port, token, "resume")
        wait_for_api(client, time.monotonic() + 30)
        recovery_deadline = time.monotonic() + 35
        while time.monotonic() < recovery_deadline:
            run_response = client.get(f"/runs/{run_id}", timeout=3)
            run_response.raise_for_status()
            current = run_response.json()
            events_response = client.get(f"/runs/{run_id}/events", timeout=3)
            events_response.raise_for_status()
            events = events_response.json()
            recovered = [event for event in events if event["event_type"] == "run.recovered"]
            if current["state"] == "blocked" and recovered:
                break
            if second.poll() is not None:
                raise RuntimeError("Restarted frozen API exited during recovery.")
            time.sleep(0.15)
        else:
            raise TimeoutError("Restarted frozen supervisor did not recover the abandoned run.")
        if recovered[0]["payload"]["reason"] != "tool_reconciliation_required":
            raise RuntimeError("Frozen supervisor did not quarantine the unresolved create.")
        resume_response = client.post(f"/runs/{run_id}/resume", timeout=5)
        resume_response.raise_for_status()
        while time.monotonic() < recovery_deadline:
            run_response = client.get(f"/runs/{run_id}", timeout=3)
            run_response.raise_for_status()
            current = run_response.json()
            events_response = client.get(f"/runs/{run_id}/events", timeout=3)
            events_response.raise_for_status()
            events = events_response.json()
            state_changes = [
                event for event in events if event["event_type"] == "run.state_changed"
            ]
            retried_reconciliation = any(
                event["payload"].get("from") == "blocked" and event["payload"].get("to") == "queued"
                for event in state_changes
            )
            if retried_reconciliation and (
                current["state"] == "blocked"
                or (scenario == "write_result_crash" and current["state"] == "verifying")
            ):
                break
            time.sleep(0.15)
        else:
            raise TimeoutError("Frozen API did not retry reconciliation on authenticated resume.")

        events = journal.list_events(run_id)
        unresolved = journal.unresolved_tool_calls(run_id)
        intents = [event for event in events if event["event_type"] == "tool.intended"]
        executions = [event for event in events if event["event_type"] == "tool.executing"]
        completions = [event for event in events if event["event_type"] == "tool.completed"]
        saved_messages = journal.conversation_messages(run_id)
        tool_names = [
            call.get("function", {}).get("name")
            for message in saved_messages
            if message.get("role") == "assistant"
            for call in message.get("tool_calls", [])
        ]
        report.update(
            passed=(
                first.returncode not in (0, None)
                and first.pid != second.pid
                and len(recovered) == 1
                and recovered[0]["payload"]["reason"] == "tool_reconciliation_required"
                and current["state"] == ("blocked" if scenario == "create" else "verifying")
                and len(unresolved) == (1 if scenario == "create" else 0)
                and len(intents) == 1
                and len(executions) == (0 if scenario == "create" else 1)
                and len(completions) == (0 if scenario == "create" else 1)
                and tool_names
                == ["workspace_create_file" if scenario == "create" else "workspace_write_file"]
                and target.read_bytes() == (b"" if scenario == "create" else WRITE_CONTENT.encode())
                and target.stat().st_mtime_ns == target_mtime
                and fixture.workspace_tool_requests == 1
            ),
            first_api_pid=first.pid,
            first_api_exit_code=first.returncode,
            restarted_api_pid=second.pid,
            run_id=run_id,
            state=current["state"],
            recovery_reason=recovered[0]["payload"]["reason"],
            unresolved_tool_calls=len(unresolved),
            tool_intents=len(intents),
            tool_executions=len(executions),
            tool_completions=len(completions),
            saved_assistant_tool_names=tool_names,
            fixture_model_workspace_requests=fixture.workspace_tool_requests,
            target_unchanged=target.read_bytes()
            == (b"" if scenario == "create" else WRITE_CONTENT.encode())
            and target.stat().st_mtime_ns == target_mtime,
            authenticated_resume_reconciliation=retried_reconciliation,
            final_decision=(
                "blocked; incomplete create remains unresolved for user inspection"
                if scenario == "create"
                else "verifying; fsynced write reconciled without replay"
            ),
        )

        shutdown_response = client.post("/shutdown", timeout=5)
        shutdown_response.raise_for_status()
        second.wait(timeout=10)
        report["restarted_api_shutdown_clean"] = second.returncode == 0
        report["passed"] = report["passed"] and second.returncode == 0
        second = None
        api_url = f"http://127.0.0.1:{api_port}"
        report["launch_credentials_cleaned"] = clean_launch_credentials(
            credential_store, token, api_url
        )
        report["passed"] = report["passed"] and report["launch_credentials_cleaned"]
        if not report["passed"]:
            raise RuntimeError("Frozen API restart acceptance invariants failed.")
    finally:
        if second is not None and second.poll() is None:
            try:
                client.post("/shutdown", timeout=2)
                second.wait(timeout=5)
            except (httpx.HTTPError, subprocess.TimeoutExpired):
                kill_api_tree(second)
        if first is not None and first.poll() is None:
            kill_api_tree(first)
        if lock is not None:
            lock.rollback()
            lock.close()
        if monitor is not None:
            monitor.close()
        api_url = f"http://127.0.0.1:{api_port}"
        report["launch_credentials_cleaned"] = clean_launch_credentials(
            credential_store, token, api_url
        )
        client.close()
        fixture.shutdown()
        fixture.server_close()
        fake_thread.join(timeout=3)
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))
    if not report["passed"]:
        raise RuntimeError("Frozen API restart acceptance failed.")


if __name__ == "__main__":
    main()

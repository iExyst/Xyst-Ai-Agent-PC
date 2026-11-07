"""Kill the frozen API after a specialist read is durable, then recover that specialist."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from frozen_api_restart_diagnostic import (
    clean_launch_credentials,
    kill_api_tree,
    start_api,
    wait_for_api,
)
from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE, CredentialStore
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


class SpecialistFixtureLMStudio(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, nonce: str) -> None:
        super().__init__(("127.0.0.1", 0), SpecialistFixtureHandler)
        self.nonce = nonce
        self.lock = threading.Lock()
        self.read_proposals = 0
        self.read_result_requests = 0
        self.result_request_blocked = threading.Event()
        self.release_first_result_request = threading.Event()


class SpecialistFixtureHandler(BaseHTTPRequestHandler):
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
        self._json({"data": [{"id": "frozen-specialist-restart-model"}]})

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
        fixture = cast(SpecialistFixtureLMStudio, self.server)
        last_tool_id = messages[-1].get("tool_call_id") if messages else None
        echo_result = bool(
            isinstance(last_tool_id, str)
            and any(
                call.get("id") == last_tool_id
                and call.get("function", {}).get("name") == "xaa_echo"
                for item in messages
                if item.get("role") == "assistant"
                for call in item.get("tool_calls", [])
            )
        )

        if echo_result:
            message = {
                "role": "assistant",
                "content": str(messages[-1].get("content", "")),
            }
            finish_reason = "stop"
        elif "xaa_echo" in tool_names:
            if messages and messages[-1].get("role") == "tool":
                message = {
                    "role": "assistant",
                    "content": str(messages[-1].get("content", "")),
                }
                finish_reason = "stop"
            else:
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
                    self._json({"error": "unexpected harmless diagnostic prompt"}, 400)
                    return
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "frozen-specialist-diagnostic-echo",
                            "type": "function",
                            "function": {
                                "name": "xaa_echo",
                                "arguments": json.dumps({"value": match.group(1)}),
                            },
                        }
                    ],
                }
                finish_reason = "tool_calls"
        elif messages and messages[-1].get("role") == "tool":
            serialized = json.dumps(
                [item for item in messages if item.get("role") == "tool"], ensure_ascii=False
            )
            if fixture.nonce not in serialized:
                self._json({"error": "saved specialist history omitted the read evidence"}, 409)
                return
            with fixture.lock:
                fixture.read_result_requests += 1
                followup_number = fixture.read_result_requests
            if followup_number == 1:
                fixture.result_request_blocked.set()
                if not fixture.release_first_result_request.wait(timeout=90):
                    self._json(
                        {"error": "restart diagnostic timed out at the specialist boundary"}, 504
                    )
                    return
                try:
                    self._json(
                        {"error": "the original API was killed at the read-result boundary"},
                        503,
                    )
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            message = {
                "role": "assistant",
                "content": f"Recovered candidate using the saved file read: {fixture.nonce}",
            }
            finish_reason = "stop"
        elif "workspace_read_file" in tool_names:
            with fixture.lock:
                fixture.read_proposals += 1
                call_number = fixture.read_proposals
            if call_number != 1:
                self._json({"error": "the specialist repeated its completed file read"}, 409)
                return
            message: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "frozen-specialist-read-once",
                        "type": "function",
                        "function": {
                            "name": "workspace_read_file",
                            "arguments": '{"path":"fixture.txt"}',
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            self._json({"error": "unexpected specialist inference request"}, 400)
            return

        self._json(
            {
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
            }
        )


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def seed_run(database: Path, workspace: Path) -> str:
    store = RunStore(database)
    run_id = store.create_run(
        objective="Recover a specialist after its authenticated API process exits.",
        acceptance_criteria=["Use the saved file-read result exactly once."],
        workspace=str(workspace),
        mode="workspace",
        model="frozen-specialist-restart-model",
        max_iterations=8,
        max_seconds=180,
    )["id"]
    parent = store.acquire_lease(run_id, "frozen-specialist-seed")
    if parent is None:
        raise RuntimeError("Could not acquire the disposable parent run lease.")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="reader",
                objective="Read fixture.txt and return its exact contents.",
                acceptance_criteria=("Return the exact file contents.",),
                role="researcher",
                max_iterations=4,
                max_seconds=90,
            )
        ],
    )
    for state in (RunState.PLANNING, RunState.RUNNING, RunState.VERIFYING):
        store.transition(run_id, state, owner=parent)
    store.release_lease(run_id, parent)
    return run_id


def wait_for_task_result(store: RunStore, run_id: str, deadline: float) -> list[dict[str, Any]]:
    while time.monotonic() < deadline:
        events = store.list_events(run_id)
        results = [
            event
            for event in events
            if event["event_type"] == "task.tool.result"
            and event["payload"].get("task_id") == "reader"
        ]
        if results:
            return results
        time.sleep(0.05)
    raise TimeoutError("The frozen specialist did not persist its file-read result.")


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("Frozen specialist restart acceptance requires Windows.")
    executable = Path(
        os.environ.get("XAA_FROZEN_API_EXECUTABLE", "build/native-dev/xaa-engine.exe")
    ).resolve()
    if not executable.is_file():
        raise FileNotFoundError(
            "Build the frozen engine first, or set XAA_FROZEN_API_EXECUTABLE to its path."
        )
    credential_store = CredentialStore()
    if credential_store.get(API_LAUNCH_TOKEN_PROFILE) or credential_store.get(
        API_LAUNCH_URL_PROFILE
    ):
        raise RuntimeError("An API launch credential already exists; refusing to overwrite it.")

    root = (Path(".acceptance") / f"frozen-specialist-restart-{uuid4()}").resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    nonce = f"frozen-specialist-evidence-{uuid4().hex}"
    fixture_path = workspace / "fixture.txt"
    fixture_path.write_text(nonce, encoding="utf-8")
    fixture_hash = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    token = secrets.token_urlsafe(48)
    api_port = free_port()
    fixture = SpecialistFixtureLMStudio(nonce)
    lm_port = fixture.server_address[1]
    fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fixture_thread.start()
    base_url = f"http://127.0.0.1:{api_port}"
    client = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})
    database = root / "data" / "XAA" / "xaa.sqlite3"
    run_id = seed_run(database, workspace)
    first = None
    second = None
    report: dict[str, Any] = {
        "passed": False,
        "scenario": "frozen authenticated API restart after a durable specialist file read",
        "provider": "local deterministic OpenAI-compatible fixture; no LM Studio used",
        "frozen_engine_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "run_id": run_id,
        "fixture_sha256": fixture_hash,
    }
    try:
        first = start_api(executable, root, api_port, lm_port, token, "specialist-crash")
        wait_for_api(client, time.monotonic() + 30)
        dispatch = client.post(f"/runs/{run_id}/plan/dispatch", timeout=5)
        dispatch.raise_for_status()
        store = RunStore(database)
        read_results = wait_for_task_result(store, run_id, time.monotonic() + 45)
        if not fixture.result_request_blocked.wait(timeout=20):
            raise TimeoutError("The specialist did not reach the saved-result inference boundary.")
        if len(read_results) != 1 or nonce not in json.dumps(read_results[0]["payload"]):
            raise RuntimeError(
                "The durable task result did not contain the fixture nonce exactly once."
            )
        kill_api_tree(first)
        if first.returncode in (0, None):
            raise RuntimeError("The first frozen API did not terminate at the specialist boundary.")
        fixture.release_first_result_request.set()
        with sqlite3.connect(database) as connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), run_id),
            )

        second = start_api(executable, root, api_port, lm_port, token, "specialist-recovery")
        wait_for_api(client, time.monotonic() + 30)
        recovery_deadline = time.monotonic() + 30
        recovery: dict[str, Any] | None = None
        while time.monotonic() < recovery_deadline:
            response = client.get(f"/runs/{run_id}/tasks/reader/recovery", timeout=5)
            if response.status_code == 200:
                recovery = response.json()
                break
            if response.status_code not in {409, 500}:
                response.raise_for_status()
            if second.poll() is not None:
                raise RuntimeError("The restarted frozen API exited while exposing recovery.")
            time.sleep(0.15)
        if recovery is None or recovery.get("safe_to_resume") is not True:
            raise RuntimeError(f"Frozen API did not expose safe specialist recovery: {recovery!r}")
        report["recovery_summary"] = recovery
        resume = client.post(
            f"/runs/{run_id}/tasks/reader/recovery", json={"decision": "resume"}, timeout=5
        )
        resume.raise_for_status()

        deadline = time.monotonic() + 45
        candidate: dict[str, Any] | None = None
        run_state = ""
        while time.monotonic() < deadline:
            if second.poll() is not None:
                raise RuntimeError("The restarted frozen API exited before specialist completion.")
            response = client.get(f"/runs/{run_id}/tasks/reader/candidate", timeout=5)
            if response.status_code == 200:
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("Frozen candidate endpoint returned a non-object response.")
                candidate = cast(dict[str, Any], payload)
                state_response = client.get(f"/runs/{run_id}", timeout=5)
                state_response.raise_for_status()
                run_state = state_response.json()["state"]
                if candidate.get("candidate") and run_state == "verifying":
                    break
            elif response.status_code not in {409, 422}:
                response.raise_for_status()
            time.sleep(0.15)
        if candidate is None or run_state != "verifying":
            raise TimeoutError("The frozen specialist did not recover to a reviewable candidate.")

        events = store.list_events(run_id)
        read_results = [
            event
            for event in events
            if event["event_type"] == "task.tool.result"
            and event["payload"].get("task_id") == "reader"
        ]
        final_candidate = candidate.get("candidate")
        fixture_unchanged = hashlib.sha256(fixture_path.read_bytes()).hexdigest() == fixture_hash
        report.update(
            {
                "passed": (
                    len(read_results) == 1
                    and isinstance(final_candidate, str)
                    and nonce in final_candidate
                    and fixture_unchanged
                    and fixture.read_proposals == 1
                    and fixture.read_result_requests == 2
                    and first.pid != second.pid
                    and first.returncode not in (0, None)
                ),
                "first_api_pid": first.pid,
                "first_api_exit_code": first.returncode,
                "restarted_api_pid": second.pid,
                "saved_specialist_read_results": len(read_results),
                "fixture_nonce_in_candidate": isinstance(final_candidate, str)
                and nonce in final_candidate,
                "fixture_unchanged": fixture_unchanged,
                "provider_read_proposals": fixture.read_proposals,
                "specialist_result_followups": fixture.read_result_requests,
                "recovered_run_state": run_state,
                "lease_expiry_accelerated": True,
            }
        )
        shutdown = client.post("/shutdown", timeout=5)
        shutdown.raise_for_status()
        second.wait(timeout=10)
        report["restarted_api_shutdown_clean"] = second.returncode == 0
        report["passed"] = report["passed"] and second.returncode == 0
        second = None
        if not report["passed"]:
            raise RuntimeError("Frozen specialist restart invariants failed.")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        raise
    finally:
        fixture.release_first_result_request.set()
        if second is not None and second.poll() is None:
            try:
                client.post("/shutdown", timeout=2)
                second.wait(timeout=5)
            except (httpx.HTTPError, TimeoutError):
                kill_api_tree(second)
        if first is not None and first.poll() is None:
            kill_api_tree(first)
        report["launch_credentials_cleaned"] = clean_launch_credentials(
            credential_store, token, base_url
        )
        report["passed"] = report.get("passed", False) and report["launch_credentials_cleaned"]
        fixture.shutdown()
        fixture.server_close()
        fixture_thread.join(timeout=3)
        client.close()
        report["evidence_directory"] = str(root)
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report, indent=2))
    if not report["passed"]:
        raise RuntimeError("Frozen specialist restart acceptance did not pass.")


if __name__ == "__main__":
    main()

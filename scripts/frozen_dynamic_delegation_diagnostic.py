"""Exercise dynamic specialist delegation through the frozen Windows API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import threading
import time
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
from xaa.storage.task_workspaces import TaskWorkspaces
from xaa.storage.tasks import TaskStore


class DelegationFixtureLMStudio(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, database: Path, run_id: str, nonce: str) -> None:
        super().__init__(("127.0.0.1", 0), DelegationFixtureHandler)
        self.database = database
        self.run_id = run_id
        self.nonce = nonce
        self.lock = threading.Lock()
        self.parent_read_calls = 0
        self.parent_write_calls = 0
        self.delegation_calls = 0
        self.child_read_calls = 0
        self.parent_resumed = False
        self.errors: list[str] = []


class DelegationFixtureHandler(BaseHTTPRequestHandler):
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
        self._json({"data": [{"id": "frozen-dynamic-delegation-model"}]})

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 1_048_576:
            self._json({"error": "invalid request size"}, 413)
            return
        fixture = cast(DelegationFixtureLMStudio, self.server)
        try:
            request = cast(dict[str, Any], json.loads(self.rfile.read(length)))
            messages = cast(list[dict[str, Any]], request.get("messages", []))
            tools = cast(list[dict[str, Any]], request.get("tools", []))
            tool_names = self._tool_names(tools)
            if self._last_tool_name(messages) == "xaa_echo":
                last = messages[-1]
                self._respond({"role": "assistant", "content": str(last.get("content", ""))})
                return
            if "xaa_echo" in tool_names:
                prompt = next(
                    (
                        str(item.get("content", ""))
                        for item in reversed(messages)
                        if item.get("role") == "user"
                    ),
                    "",
                )
                match = re.search(r"Call xaa_echo with value ([a-f0-9]+),", prompt)
                if match is None:
                    raise AssertionError("Tool-use diagnostic prompt was malformed.")
                self._respond(
                    self._tool_call(
                        "frozen-dynamic-delegation-echo",
                        "xaa_echo",
                        {"value": match.group(1)},
                    )
                )
                return
            assignment = self._assignment(messages)
            last_tool_name = self._last_tool_name(messages)
            if assignment["objective"] == "Aggregate the reviewed source finding":
                if assignment["subtask_results_untrusted"]:
                    self._verify_resumed_parent(fixture, assignment)
                    message = {"role": "assistant", "content": "Integrated reviewed finding."}
                elif last_tool_name == "workspace_read_file":
                    result = json.loads(messages[-1]["content"])
                    if result.get("text") != "before":
                        raise AssertionError("Parent did not read the original draft contents.")
                    message = self._tool_call(
                        "frozen-parent-write",
                        "workspace_write_file",
                        {
                            "path": "draft.txt",
                            "expected_sha256": result["sha256"],
                            "content": "retained private edit",
                        },
                    )
                elif last_tool_name == "workspace_write_file":
                    result = json.loads(messages[-1]["content"])
                    if result.get("bytes_written") != len("retained private edit"):
                        raise AssertionError("Parent private edit was not recorded.")
                    with fixture.lock:
                        fixture.parent_write_calls += 1
                        fixture.delegation_calls += 1
                    if "task_delegate_subtasks" not in self._tool_names(tools):
                        raise AssertionError("Frozen specialist did not expose dynamic delegation.")
                    message = self._tool_call(
                        "frozen-parent-delegate",
                        "task_delegate_subtasks",
                        {
                            "reason": "Source evidence needs independent review before aggregation.",
                            "tasks": [
                                {
                                    "id": "source-check",
                                    "objective": "Inspect and report the source evidence",
                                    "acceptance_criteria": ["Report the exact source contents."],
                                    "expected_outputs": ["A source evidence summary"],
                                    "dependencies": [],
                                    "permissions": ["workspace.read"],
                                    "role": "researcher",
                                    "max_iterations": 2,
                                    "max_seconds": 90,
                                }
                            ],
                        },
                    )
                else:
                    if "task_delegate_subtasks" not in self._tool_names(tools):
                        raise AssertionError("Frozen specialist did not expose dynamic delegation.")
                    with fixture.lock:
                        fixture.parent_read_calls += 1
                    message = self._tool_call(
                        "frozen-parent-read",
                        "workspace_read_file",
                        {"path": "draft.txt"},
                    )
            elif assignment["objective"] == "Inspect and report the source evidence":
                if last_tool_name == "workspace_read_file":
                    result = json.loads(messages[-1]["content"])
                    if result.get("text") != fixture.nonce:
                        raise AssertionError("Child read unexpected source contents.")
                    with fixture.lock:
                        fixture.child_read_calls += 1
                    message = {
                        "role": "assistant",
                        "content": f"The source contains {fixture.nonce}.",
                    }
                else:
                    message = self._tool_call(
                        "frozen-child-read",
                        "workspace_read_file",
                        {"path": "source.txt"},
                    )
            else:
                raise AssertionError(f"Unexpected specialist assignment: {assignment!r}")
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            with fixture.lock:
                fixture.errors.append(f"{type(exc).__name__}: {exc}")
            self._json({"error": "deterministic delegation fixture rejected the request"}, 409)
            return
        self._json(
            {
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
            }
        )

    def _respond(self, message: dict[str, Any]) -> None:
        self._json(
            {
                "choices": [
                    {
                        "index": 0,
                        "message": message,
                        "finish_reason": "tool_calls" if message.get("tool_calls") else "stop",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
            }
        )

    @staticmethod
    def _assignment(messages: list[dict[str, Any]]) -> dict[str, Any]:
        for item in reversed(messages):
            if item.get("role") != "user" or not isinstance(item.get("content"), str):
                continue
            try:
                payload = json.loads(item["content"])
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                assignment = cast(dict[str, Any], payload)
                if isinstance(assignment.get("objective"), str):
                    return assignment
        summary = [
            {
                "role": item.get("role"),
                "content": str(item.get("content", ""))[:1000],
            }
            for item in messages[-4:]
        ]
        raise AssertionError(f"Specialist request did not contain a task assignment: {summary!r}")

    @staticmethod
    def _tool_names(tools: list[dict[str, Any]]) -> set[str]:
        names: set[str] = set()
        for tool in tools:
            function_value = tool.get("function")
            if not isinstance(function_value, dict):
                continue
            function = cast(dict[str, Any], function_value)
            name = function.get("name")
            if isinstance(name, str):
                names.add(name)
        return names

    @staticmethod
    def _last_tool_name(messages: list[dict[str, Any]]) -> str | None:
        if not messages or messages[-1].get("role") != "tool":
            return None
        call_id = messages[-1].get("tool_call_id")
        for item in reversed(messages[:-1]):
            for call in item.get("tool_calls", []):
                if call.get("id") == call_id:
                    return call.get("function", {}).get("name")
        return None

    @staticmethod
    def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ],
        }

    @staticmethod
    def _verify_resumed_parent(
        fixture: DelegationFixtureLMStudio, assignment: dict[str, Any]
    ) -> None:
        child = assignment["subtask_results_untrusted"][0]
        if (
            assignment["subtask_result_task_ids"] != ["source-check"]
            or child.get("completion") != "completed_after_owner_review"
            or fixture.nonce not in child.get("candidate_untrusted", "")
        ):
            raise AssertionError("Parent did not receive the owner-reviewed child result.")
        rows = TaskStore(RunStore(fixture.database)).records(fixture.run_id)
        if not any(
            item["local_id"] == "source-check" and item["status"] == "completed" for item in rows
        ):
            raise AssertionError("Child was not completed by owner review before parent resume.")
        changes = TaskWorkspaces(RunStore(fixture.database)).changes(fixture.run_id, "lead")
        if [item["path"] for item in changes] != ["draft.txt"]:
            raise AssertionError("Parent private workspace edit did not survive delegation.")
        with fixture.lock:
            fixture.parent_resumed = True


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _seed_run(database: Path, workspace: Path) -> str:
    store = RunStore(database)
    run_id = store.create_run(
        objective="Delegate discovered independent source review.",
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="workspace",
        model="frozen-dynamic-delegation-model",
        max_iterations=16,
        max_seconds=600,
    )["id"]
    owner = store.acquire_lease(run_id, "frozen-dynamic-delegation-seed", seconds=60)
    if owner is None:
        raise RuntimeError("Could not acquire the disposable dynamic-delegation run.")
    try:
        store.transition(run_id, RunState.PLANNING, owner=owner)
        TaskStore(store).save_plan(
            run_id,
            owner,
            [
                TaskDefinition(
                    id="lead",
                    objective="Aggregate the reviewed source finding",
                    acceptance_criteria=("Use the reviewed child result.",),
                    role="developer",
                    permissions=("workspace.read", "workspace.write"),
                    max_iterations=8,
                    max_seconds=300,
                )
            ],
        )
        store.transition(run_id, RunState.RUNNING, owner=owner)
        store.transition(run_id, RunState.VERIFYING, owner=owner)
    finally:
        store.release_lease(run_id, owner)
    return run_id


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("Frozen dynamic-delegation acceptance requires Windows.")
    executable = Path(
        os.environ.get("XAA_FROZEN_API_EXECUTABLE", "build/native-dev/xaa-engine.exe")
    ).resolve()
    if not executable.is_file():
        raise FileNotFoundError("Build the frozen engine first or set XAA_FROZEN_API_EXECUTABLE.")
    credentials = CredentialStore()
    if credentials.get(API_LAUNCH_TOKEN_PROFILE) or credentials.get(API_LAUNCH_URL_PROFILE):
        raise RuntimeError("An API launch credential already exists; refusing to overwrite it.")

    root = (Path(".acceptance") / f"frozen-dynamic-delegation-{uuid4()}").resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    nonce = f"dynamic-delegation-evidence-{uuid4().hex}"
    (workspace / "source.txt").write_text(nonce, encoding="utf-8")
    draft = workspace / "draft.txt"
    draft.write_text("before", encoding="utf-8")
    original_draft_hash = hashlib.sha256(draft.read_bytes()).hexdigest()
    database = root / "data" / "XAA" / "xaa.sqlite3"
    run_id = _seed_run(database, workspace)
    token = secrets.token_urlsafe(48)
    api_port = _free_port()
    fixture = DelegationFixtureLMStudio(database, run_id, nonce)
    lm_port = fixture.server_address[1]
    fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fixture_thread.start()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{api_port}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=8,
    )
    process = None
    report: dict[str, Any] = {
        "passed": False,
        "scenario": "frozen specialist dynamically delegates, waits for owner review, and resumes",
        "provider": "local deterministic OpenAI-compatible fixture; no live model used",
        "frozen_engine_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "run_id": run_id,
        "workspace_draft_sha256_before": original_draft_hash,
        "source_nonce_sha256": hashlib.sha256(nonce.encode()).hexdigest(),
    }
    api_url = f"http://127.0.0.1:{api_port}"
    try:
        process = start_api(executable, root, api_port, lm_port, token, "dynamic-delegation")
        wait_for_api(client, time.monotonic() + 30)
        dispatch = client.post(f"/runs/{run_id}/plan/dispatch")
        dispatch.raise_for_status()
        store = RunStore(database)
        tasks = TaskStore(store)
        child_dispatched = False
        child_wait_deadline = time.monotonic() + 100
        while time.monotonic() < child_wait_deadline:
            rows = {item["local_id"]: item for item in tasks.records(run_id)}
            if "source-check" in rows and rows["source-check"]["status"] == "verifying":
                break
            if any(item["status"] == "failed" for item in rows.values()):
                raise RuntimeError(f"A specialist failed before child review: {rows!r}")
            current_run = store.get_run(run_id)
            if current_run is None or current_run["state"] in {"failed", "blocked"}:
                raise RuntimeError(
                    f"Coordinator stopped before child review: {None if current_run is None else current_run['state']}; "
                    f"fixture errors: {fixture.errors!r}"
                )
            if (
                not child_dispatched
                and current_run["state"] == "verifying"
                and rows.get("source-check", {}).get("status") == "queued"
            ):
                child_dispatch = client.post(f"/runs/{run_id}/plan/dispatch")
                child_dispatch.raise_for_status()
                child_dispatched = True
            if process.poll() is not None:
                raise RuntimeError("The frozen API exited before completing the child task.")
            time.sleep(0.15)
        else:
            raise TimeoutError("The dynamic child did not reach owner review in time.")
        child_response = client.get(f"/runs/{run_id}/tasks/source-check/candidate")
        child_response.raise_for_status()
        child = child_response.json()
        evidence = child.get("evidence", [])
        if (
            child.get("status") != "verifying"
            or nonce not in child.get("candidate", "")
            or not evidence
            or evidence[0].get("path") != "source.txt"
        ):
            raise RuntimeError("Child candidate is missing the expected source evidence.")
        review = client.post(
            f"/runs/{run_id}/tasks/source-check/review",
            json={
                "decision": "accept",
                "rationale": "The child candidate matches the inspected source file.",
                "confirmed_criteria": [0],
                "evidence_call_ids": [evidence[0]["call_id"]],
            },
        )
        review.raise_for_status()
        parent_deadline = time.monotonic() + 100
        parent_candidate: dict[str, Any] | None = None
        while time.monotonic() < parent_deadline:
            if process.poll() is not None:
                raise RuntimeError("The frozen API exited before the parent resumed.")
            response = client.get(f"/runs/{run_id}/tasks/lead/candidate")
            if response.status_code == 200:
                candidate = response.json()
                if candidate.get("status") == "verifying":
                    parent_candidate = cast(dict[str, Any], candidate)
                    break
            elif response.status_code not in {409, 422}:
                response.raise_for_status()
            time.sleep(0.15)
        if parent_candidate is None:
            raise TimeoutError("The parent did not resume with a reviewable candidate.")

        rows = {item["local_id"]: item for item in tasks.records(run_id)}
        events = store.list_events(run_id)
        delegation_results = [
            event
            for event in events
            if event["event_type"] == "task.tool.result"
            and event["payload"].get("task_id") == "lead"
            and event["payload"].get("call_id") == "frozen-parent-delegate"
        ]
        review_events = [
            event
            for event in events
            if event["event_type"] == "task.reviewed"
            and event["payload"].get("task_id") == "source-check"
            and event["payload"].get("decision") == "accept"
        ]
        private_changes = TaskWorkspaces(store).changes(run_id, "lead")
        report.update(
            passed=(
                rows["source-check"]["status"] == "completed"
                and rows["lead"]["status"] == "verifying"
                and len(delegation_results) == 1
                and len(review_events) == 1
                and fixture.parent_read_calls == 1
                and fixture.parent_write_calls == 1
                and fixture.delegation_calls == 1
                and fixture.child_read_calls == 1
                and fixture.parent_resumed
                and [item["path"] for item in private_changes] == ["draft.txt"]
                and hashlib.sha256(draft.read_bytes()).hexdigest() == original_draft_hash
                and not store.unresolved_tool_calls(run_id)
                and not fixture.errors
            ),
            tasks={name: item["status"] for name, item in rows.items()},
            delegation_results=len(delegation_results),
            owner_review_events=len(review_events),
            fixture_calls={
                "parent_read": fixture.parent_read_calls,
                "parent_private_write": fixture.parent_write_calls,
                "delegation": fixture.delegation_calls,
                "child_read": fixture.child_read_calls,
                "parent_resumed_with_reviewed_child": fixture.parent_resumed,
            },
            private_workspace_changes=private_changes,
            base_draft_unchanged=hashlib.sha256(draft.read_bytes()).hexdigest()
            == original_draft_hash,
            unresolved_tool_calls=len(store.unresolved_tool_calls(run_id)),
            fixture_errors=fixture.errors,
        )
        shutdown = client.post("/shutdown")
        shutdown.raise_for_status()
        process.wait(timeout=15)
        report["frozen_api_shutdown_clean"] = process.returncode == 0
        report["passed"] = report["passed"] and process.returncode == 0
        process = None
        report["launch_credentials_cleaned"] = clean_launch_credentials(credentials, token, api_url)
        report["passed"] = report["passed"] and report["launch_credentials_cleaned"]
        if not report["passed"]:
            raise RuntimeError("Frozen dynamic-delegation acceptance invariants failed.")
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {str(exc)[:1000]}"
        report["fixture_errors"] = fixture.errors
        raise
    finally:
        if process is not None and process.poll() is None:
            try:
                client.post("/shutdown", timeout=2)
                process.wait(timeout=8)
            except (httpx.HTTPError, TimeoutError):
                kill_api_tree(process)
        report["launch_credentials_cleaned"] = clean_launch_credentials(credentials, token, api_url)
        client.close()
        fixture.shutdown()
        fixture.server_close()
        fixture_thread.join(timeout=5)
        root.mkdir(parents=True, exist_ok=True)
        (root / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        if not report["passed"]:
            print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Frozen dynamic-delegation acceptance passed: {root}")


if __name__ == "__main__":
    main()

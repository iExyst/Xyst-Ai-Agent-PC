"""Kill and restart the authenticated API while a safe inference is in flight."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.runs import RunStore

CRASH_WRITE_CONTENT = "after-api-crash:" + "x" * (65_536 - len("after-api-crash:"))
CRASH_DIRECTORY_PARTS = tuple(f"dir{index:02d}" for index in range(24))
CRASH_CREATE_PATH = "/".join((*CRASH_DIRECTORY_PARTS, "created.txt"))


class ShortLeaseRunStore(RunStore):
    """Use short diagnostic leases so restart acceptance does not wait three minutes."""

    def acquire_lease(self, run_id: str, owner: str, *, seconds: int = 30) -> str | None:
        return super().acquire_lease(run_id, owner, seconds=min(seconds, 4))

    def renew_lease(self, run_id: str, token: str, *, seconds: int = 30) -> None:
        super().renew_lease(run_id, token, seconds=min(seconds, 4))


class RestartDiagnosticProvider(LMStudioProvider):
    def __init__(self, root: Path, phase: str, scenario: str) -> None:
        super().__init__(exact_context=False)
        self.base_url = "http://127.0.0.1:1234"
        self.root = root
        self.phase = phase
        self.scenario = scenario
        self.calls = 0

    @staticmethod
    def validate_endpoint(endpoint: str) -> str:
        return LMStudioProvider.validate_endpoint(endpoint)

    async def configure_endpoint(self, endpoint: str) -> str:
        self.base_url = self.validate_endpoint(endpoint)
        return self.base_url

    async def health(self) -> bool:
        return True

    async def diagnose_tools(self, model: str, *, request: Any = None) -> bool:
        return True

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        on_admission: Any = None,
        require_exact_context: bool = False,
    ) -> dict[str, Any]:
        self.calls += 1
        objective = os.environ["XAA_DIAGNOSTIC_OBJECTIVE"]
        occurrences = sum(
            message.get("role") == "user" and message.get("content") == objective
            for message in messages
        )
        digest = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        marker = self.root / f"{self.phase}-inference-{self.calls}.json"
        marker.write_text(
            json.dumps(
                {
                    "model": model,
                    "objective_occurrences": occurrences,
                    "assistant_history_messages": sum(
                        message.get("role") == "assistant" for message in messages
                    ),
                    "tool_history_messages": sum(
                        message.get("role") == "tool" for message in messages
                    ),
                    "assistant_tool_calls": sum(
                        len(message.get("tool_calls", []))
                        for message in messages
                        if message.get("role") == "assistant"
                    ),
                    "assistant_tool_names": [
                        call.get("function", {}).get("name")
                        for message in messages
                        if message.get("role") == "assistant"
                        for call in message.get("tool_calls", [])
                    ],
                    "tool_result_count": sum(message.get("role") == "tool" for message in messages),
                    "tool_result_contents": [
                        str(message.get("content", ""))
                        for message in messages
                        if message.get("role") == "tool"
                    ],
                    "message_count": len(messages),
                    "history_sha256": digest,
                    "tool_count": len(tools or []),
                }
            ),
            encoding="utf-8",
        )
        if self.scenario in {"write", "write_crash"} and self.phase == "effect" and self.calls == 1:
            before = (
                (self.root / "workspace" / "fixture.txt").read_bytes()
                if self.scenario == "write"
                else b""
            )
            tool_name = (
                "workspace_create_file"
                if self.scenario == "write_crash"
                else "workspace_write_file"
            )
            arguments = (
                {"path": CRASH_CREATE_PATH, "content": CRASH_WRITE_CONTENT}
                if self.scenario == "write_crash"
                else {
                    "path": "fixture.txt",
                    "content": "after-api-restart",
                    "expected_sha256": hashlib.sha256(before).hexdigest(),
                }
            )
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "restart-write-1",
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(arguments),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        if (self.phase == "crash" and self.scenario == "inference") or (
            self.phase == "effect" and self.scenario in {"write", "write_crash"}
        ):
            # In write mode, the first response's real filesystem effect and tool result
            # are already durable before the API is killed on the next inference.
            await asyncio.Event().wait()
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Recovered run candidate."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }


def run_server(root: Path, port: int, phase: str, scenario: str) -> None:
    import uvicorn
    from pydantic import SecretStr
    from xaa.api.app import create_app

    token = (root / "api-token.txt").read_text(encoding="utf-8")
    store = ShortLeaseRunStore(root / "runs.sqlite3")
    provider = RestartDiagnosticProvider(root, phase, scenario)
    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server is not None:
            server.should_exit = True

    app = create_app(
        provider,
        SecretStr(token),
        trusted_origins=(),
        store=store,
        shutdown_callback=request_shutdown,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            access_log=False,
            timeout_graceful_shutdown=3,
        )
    )
    server.run()


def wait_for_api(client: httpx.Client, deadline: float) -> None:
    while time.monotonic() < deadline:
        try:
            response = client.get("/health")
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError("Authenticated diagnostic API did not become ready.")


def start_server(root: Path, port: int, phase: str, scenario: str) -> subprocess.Popen[bytes]:
    log = (root / f"api-{phase}.log").open("wb")
    try:
        return subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--server",
                str(root),
                str(port),
                phase,
                scenario,
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    finally:
        log.close()


def stop_server(client: httpx.Client, process: subprocess.Popen[bytes]) -> None:
    try:
        client.post("/shutdown", timeout=2)
        process.wait(timeout=6)
    except (httpx.HTTPError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def kill_server_tree(process: subprocess.Popen[bytes]) -> None:
    """Kill the API and its owned filesystem worker at a controlled crash boundary."""
    if process.poll() is not None:
        return
    if os.name != "nt":
        raise RuntimeError("The write-crash API scenario requires Windows process-tree killing.")
    result = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0 and process.poll() is None:
        raise RuntimeError("Could not terminate the API and filesystem worker process tree.")
    process.wait(timeout=8)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        run_server(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5])
        return

    scenario = os.environ.get("XAA_API_RESTART_SCENARIO", "inference")
    if scenario not in {"inference", "write", "write_crash"}:
        raise ValueError("XAA_API_RESTART_SCENARIO must be inference, write, or write_crash.")
    if scenario == "write_crash" and os.name != "nt":
        raise ValueError("The write_crash scenario requires Windows process-tree semantics.")
    root = (Path(".acceptance") / f"api-restart-{uuid4()}").resolve()
    root.mkdir(parents=True)
    token = secrets.token_urlsafe(48)
    objective = f"Restart fixture {uuid4()}"
    workspace = root / "workspace"
    if scenario in {"write", "write_crash"}:
        workspace.mkdir()
    if scenario == "write":
        (workspace / "fixture.txt").write_bytes(b"before-api-restart")
    elif scenario == "write_crash":
        workspace.joinpath(*CRASH_DIRECTORY_PARTS).mkdir(parents=True)
    (root / "api-token.txt").write_text(token, encoding="utf-8")
    os.environ["XAA_DIAGNOSTIC_OBJECTIVE"] = objective
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})
    first: subprocess.Popen[bytes] | None = None
    second: subprocess.Popen[bytes] | None = None
    report: dict[str, Any] = {
        "passed": False,
        "scenario": scenario,
        "scope": "authenticated API process restart",
    }
    run_id: str | None = None
    target_mtime_after_write: int | None = None
    lock_connection: sqlite3.Connection | None = None
    monitor_connection: sqlite3.Connection | None = None
    try:
        first_phase = "crash" if scenario == "inference" else "effect"
        first = start_server(root, port, first_phase, scenario)
        wait_for_api(client, time.monotonic() + 20)
        response = client.post(
            "/runs",
            json={
                "objective": objective,
                "acceptance_criteria": ["The restarted coordinator returns its saved candidate."],
                "workspace": str(workspace) if scenario != "inference" else None,
                "mode": "observe" if scenario == "inference" else "workspace",
                "model": "restart-diagnostic-model",
                "max_iterations": 3,
                "max_seconds": 120,
            },
            timeout=5,
        )
        response.raise_for_status()
        run_id = response.json()["id"]
        assert isinstance(run_id, str)
        marker = root / f"{first_phase}-inference-1.json"
        deadline = time.monotonic() + 30
        journal = RunStore(root / "runs.sqlite3")
        monitor_connection = sqlite3.connect(root / "runs.sqlite3", timeout=5)
        while time.monotonic() < deadline:
            if first.poll() is not None:
                raise RuntimeError("First API process exited before reaching the crash boundary.")
            if marker.exists():
                if scenario == "inference":
                    break
                event_types = {
                    row[0]
                    for row in monitor_connection.execute(
                        "SELECT DISTINCT event_type FROM run_events WHERE run_id=? "
                        "AND event_type IN ('tool.intended','tool.executing','tool.completed')",
                        (run_id,),
                    )
                }
                target = workspace / "fixture.txt"
                if scenario == "write":
                    next_inference_started = (root / f"{first_phase}-inference-2.json").exists()
                    if (
                        "tool.completed" in event_types
                        and next_inference_started
                        and target.read_bytes() == b"after-api-restart"
                    ):
                        target_mtime_after_write = target.stat().st_mtime_ns
                        break
                elif "tool.intended" in event_types:
                    if lock_connection is None:
                        lock_connection = sqlite3.connect(
                            root / "runs.sqlite3", timeout=5, isolation_level=None
                        )
                        lock_connection.execute("BEGIN IMMEDIATE")
                    if "tool.completed" in event_types:
                        raise RuntimeError(
                            "The write result committed before the crash-boundary lock was acquired."
                        )
                    target = workspace.joinpath(*CRASH_DIRECTORY_PARTS, "created.txt")
                    target_size = target.stat().st_size if target.exists() else None
                    if target_size == 0:
                        # Exclusive creation precedes tool.executing and content. Holding the
                        # SQLite writer lock pauses the worker at that durable side-effect edge.
                        time.sleep(0.2)
                        event_types = {
                            row[0]
                            for row in monitor_connection.execute(
                                "SELECT DISTINCT event_type FROM run_events WHERE run_id=? "
                                "AND event_type IN ('tool.executing','tool.completed')",
                                (run_id,),
                            )
                        }
                        unresolved = journal.unresolved_tool_calls(run_id)
                        if (
                            "tool.executing" in event_types
                            or "tool.completed" in event_types
                            or len(unresolved) != 1
                            or target.stat().st_size != 0
                        ):
                            raise RuntimeError(
                                "The created file advanced past the intended crash boundary."
                            )
                        target_mtime_after_write = target.stat().st_mtime_ns
                        kill_server_tree(first)
                        first_exit = first.returncode
                        if first_exit in (0, None):
                            raise RuntimeError("The API did not terminate at the write boundary.")
                        if target.read_bytes() != b"":
                            raise RuntimeError("The incomplete create changed during process kill.")
                        lock_connection.rollback()
                        lock_connection.close()
                        lock_connection = None
                        time.sleep(0.2)
                        post_kill_events = journal.list_events(run_id)
                        if (
                            any(
                                event["event_type"] in {"tool.executing", "tool.completed"}
                                for event in post_kill_events
                            )
                            or len(journal.unresolved_tool_calls(run_id)) != 1
                            or target.read_bytes() != b""
                            or target.stat().st_mtime_ns != target_mtime_after_write
                        ):
                            raise RuntimeError(
                                "The killed worker advanced the side effect after lock release."
                            )
                        break
            time.sleep(0.001 if scenario == "write_crash" else 0.05)
        else:
            raise TimeoutError("First API never reached the controlled crash boundary.")
        if first.poll() is None:
            first.kill()
            first.wait(timeout=5)
        first_exit = first.returncode

        second = start_server(root, port, "resume", scenario)
        wait_for_api(client, time.monotonic() + 20)
        deadline = time.monotonic() + 40
        resumed: dict[str, Any] | None = None
        if scenario == "write_crash":
            while time.monotonic() < deadline:
                response = client.get(f"/runs/{run_id}", timeout=3)
                response.raise_for_status()
                current = response.json()
                if current["state"] == "blocked":
                    events_response = client.get(f"/runs/{run_id}/events", timeout=3)
                    events_response.raise_for_status()
                    events = events_response.json()
                    recovered_event = next(
                        (event for event in events if event["event_type"] == "run.recovered"),
                        None,
                    )
                    if recovered_event is not None:
                        if recovered_event["payload"]["reason"] != "tool_reconciliation_required":
                            raise RuntimeError(
                                "Crash recovery did not quarantine the unresolved write."
                            )
                        break
                if second.poll() is not None:
                    raise RuntimeError(
                        "Restarted API exited before quarantining the unresolved write."
                    )
                time.sleep(0.15)
            else:
                raise TimeoutError("Restarted API did not quarantine the unresolved write.")
            resume_response = client.post(f"/runs/{run_id}/resume", timeout=5)
            resume_response.raise_for_status()
        while time.monotonic() < deadline:
            response = client.get(f"/runs/{run_id}", timeout=3)
            response.raise_for_status()
            current = response.json()
            if current["state"] in {"verifying", "failed", "blocked"}:
                resumed = current
                break
            if second.poll() is not None:
                raise RuntimeError("Restarted API exited before recovery completed.")
            time.sleep(0.15)
        if resumed is None:
            raise TimeoutError("Restarted API did not recover the expired run lease.")
        events_response = client.get(f"/runs/{run_id}/events", timeout=3)
        events_response.raise_for_status()
        events = events_response.json()
        recovered = [event for event in events if event["event_type"] == "run.recovered"]
        state_changes = [event for event in events if event["event_type"] == "run.state_changed"]
        starts = [event for event in events if event["event_type"] == "model.inference.started"]
        answers = [event for event in events if event["event_type"] == "model.message"]
        probe_phase = "effect" if scenario == "write_crash" else "resume"
        resume_probe = json.loads(
            (root / f"{probe_phase}-inference-1.json").read_text(encoding="utf-8")
        )
        store = RunStore(root / "runs.sqlite3")
        unresolved = store.unresolved_tool_calls(run_id)
        saved_tool_names = [
            call.get("function", {}).get("name")
            for message in store.conversation_messages(run_id)
            if message.get("role") == "assistant"
            for call in message.get("tool_calls", [])
        ]
        reconciliation_retry_observed = scenario != "write_crash" or (
            any(
                event["payload"].get("from") == "blocked" and event["payload"].get("to") == "queued"
                for event in state_changes
            )
            and any(
                event["payload"].get("from") == "planning"
                and event["payload"].get("to") == "blocked"
                and event["payload"].get("reason") == "tool_reconciliation_required"
                for event in state_changes
            )
        )
        expected_history = 0 if scenario in {"inference", "write_crash"} else 1
        expected_starts = 1 if scenario == "write_crash" else 3 if scenario == "write" else 2
        expected_answers = 1 if scenario in {"inference", "write_crash"} else 2
        tool_intents = [event for event in events if event["event_type"] == "tool.intended"]
        tool_executions = [event for event in events if event["event_type"] == "tool.executing"]
        tool_completions = [event for event in events if event["event_type"] == "tool.completed"]
        target_unchanged_after_restart = True
        if scenario in {"write", "write_crash"}:
            target = (
                workspace.joinpath(*CRASH_DIRECTORY_PARTS, "created.txt")
                if scenario == "write_crash"
                else workspace / "fixture.txt"
            )
            target_unchanged_after_restart = (
                target_mtime_after_write is not None
                and target.read_bytes()
                == (b"" if scenario == "write_crash" else b"after-api-restart")
                and target.stat().st_mtime_ns == target_mtime_after_write
            )
        report.update(
            passed=(
                first_exit not in (0, None)
                and first.pid != second.pid
                and len(recovered) == 1
                and recovered[0]["payload"]["reason"]
                == (
                    "tool_reconciliation_required"
                    if scenario == "write_crash"
                    else "expired_worker"
                )
                and len(starts) == expected_starts
                and len(answers) == expected_answers
                and resumed["state"] == ("blocked" if scenario == "write_crash" else "verifying")
                and reconciliation_retry_observed
                and saved_tool_names
                == (
                    []
                    if scenario == "inference"
                    else [
                        "workspace_create_file"
                        if scenario == "write_crash"
                        else "workspace_write_file"
                    ]
                )
                and resume_probe["objective_occurrences"] == 1
                and resume_probe["assistant_history_messages"] == expected_history
                and resume_probe["tool_history_messages"] == expected_history
                and resume_probe["assistant_tool_calls"] == expected_history
                and resume_probe["assistant_tool_names"]
                == (["workspace_write_file"] if scenario == "write" else [])
                and resume_probe["tool_result_count"] == expected_history
                and (
                    scenario in {"inference", "write_crash"}
                    or (
                        len(resume_probe["tool_result_contents"]) == 1
                        and "fixture.txt" in resume_probe["tool_result_contents"][0]
                        and hashlib.sha256(
                            CRASH_WRITE_CONTENT.encode()
                            if scenario == "write_crash"
                            else b"after-api-restart"
                        ).hexdigest()
                        in resume_probe["tool_result_contents"][0]
                        and str(
                            len(CRASH_WRITE_CONTENT.encode()) if scenario == "write_crash" else 17
                        )
                        in resume_probe["tool_result_contents"][0]
                        and (
                            scenario != "write_crash"
                            or '"reconciled": true' in resume_probe["tool_result_contents"][0]
                        )
                    )
                )
                and len(unresolved) == (1 if scenario == "write_crash" else 0)
                and len(tool_intents) == (1 if scenario in {"write", "write_crash"} else 0)
                and len(tool_executions) == (0 if scenario == "write_crash" else expected_history)
                and len(tool_completions) == (0 if scenario == "write_crash" else expected_history)
                and target_unchanged_after_restart
            ),
            run_id=run_id,
            first_api_pid=first.pid,
            first_api_exit_code=first_exit,
            resumed_api_pid=second.pid,
            state=resumed["state"],
            recovered_events=len(recovered),
            inference_attempts=len(starts),
            persisted_model_messages=len(answers),
            resumed_objective_occurrences=resume_probe["objective_occurrences"],
            resumed_prior_assistant_messages=resume_probe["assistant_history_messages"],
            unresolved_tool_calls=len(unresolved),
            saved_assistant_tool_names=saved_tool_names,
            reconciliation_retry_observed=reconciliation_retry_observed,
            side_effect_intents=len(tool_intents),
            tool_executions=len(tool_executions),
            tool_completions=len(tool_completions),
            side_effect_unchanged_after_restart=target_unchanged_after_restart,
            final_decision=(
                "blocked; incomplete create remains unresolved for user inspection"
                if scenario == "write_crash"
                else "verifying; user review remains required"
            ),
            provider="deterministic fake; no LM Studio request",
        )
        stop_server(client, second)
        report["resumed_api_exit_code"] = second.returncode
        report["resumed_api_shutdown_clean"] = second.returncode == 0
        report["passed"] = report["passed"] and second.returncode == 0
        second = None
        if not report["passed"]:
            raise RuntimeError("API restart acceptance invariants failed.")
    finally:
        if second is not None and second.poll() is None:
            stop_server(client, second)
        if first is not None and first.poll() is None:
            if scenario == "write_crash":
                kill_server_tree(first)
            else:
                first.kill()
                first.wait(timeout=5)
        if lock_connection is not None:
            lock_connection.rollback()
            lock_connection.close()
        if monitor_connection is not None:
            monitor_connection.close()
        client.close()
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    main()

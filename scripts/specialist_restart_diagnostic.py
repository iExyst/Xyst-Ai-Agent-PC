"""Kill and restart the authenticated API after a specialist's durable read result."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore


class RestartProvider(LMStudioProvider):
    def __init__(self, root: Path, phase: str, nonce: str) -> None:
        super().__init__(exact_context=False)
        self.root = root
        self.phase = phase
        self.nonce = nonce

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
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not messages or not str(messages[0].get("content", "")).startswith("You are "):
            raise RuntimeError("The restart diagnostic expected a specialist inference.")
        results = [message for message in messages if message.get("role") == "tool"]
        marker = self.root / f"{self.phase}-specialist.json"
        if self.phase == "first" and not results:
            return {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "restart-read-once",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_read_file",
                                        "arguments": '{"path":"fixture.txt"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 3, "total_tokens": 6},
            }
        if self.phase == "first":
            marker.write_text(
                json.dumps({"phase": self.phase, "saved_tool_results": len(results)}),
                encoding="utf-8",
            )
            await asyncio.Event().wait()
        serialized_results = json.dumps(results, ensure_ascii=False)
        if self.nonce not in serialized_results:
            raise RuntimeError("Recovered history omitted the saved file-read evidence.")
        marker.write_text(
            json.dumps({"phase": self.phase, "saved_tool_results": len(results)}),
            encoding="utf-8",
        )
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"Recovered candidate using the saved read: {self.nonce}",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9},
        }


def run_server(root: Path, port: int, phase: str, nonce: str) -> None:
    import uvicorn
    from pydantic import SecretStr
    from xaa.api.app import create_app

    token = (root / "api-token.txt").read_text(encoding="utf-8")
    store = RunStore(root / "runs.sqlite3")
    provider = RestartProvider(root, phase, nonce)
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
            if client.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise TimeoutError("Authenticated diagnostic API did not become ready.")


def start_server(root: Path, port: int, phase: str, nonce: str) -> subprocess.Popen[bytes]:
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
                nonce,
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    finally:
        log.close()


def kill_server_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name != "nt":
        raise RuntimeError("The API process-tree restart diagnostic requires Windows.")
    result = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0 and process.poll() is None:
        raise RuntimeError("Could not terminate the API and its specialist worker tree.")
    process.wait(timeout=8)


def stop_server(client: httpx.Client, process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        client.post("/shutdown", timeout=2)
        process.wait(timeout=6)
    except (httpx.HTTPError, subprocess.TimeoutExpired):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def seed_run(database: Path, workspace: Path) -> str:
    store = RunStore(database)
    run_id = store.create_run(
        objective="Resume the specialist after its parent API process exits.",
        acceptance_criteria=["Reuse the saved file-read result without repeating the read."],
        workspace=str(workspace),
        mode="workspace",
        model="restart-diagnostic-model",
        max_iterations=8,
        max_seconds=120,
    )["id"]
    parent = store.acquire_lease(run_id, "restart-diagnostic-seed")
    if parent is None:
        raise RuntimeError("Could not reserve the diagnostic run.")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="reader",
                objective="Read fixture.txt and report its exact contents.",
                acceptance_criteria=("Report the exact fixture contents.",),
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


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        run_server(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4], sys.argv[5])
        return
    if os.name != "nt":
        raise RuntimeError("This acceptance diagnostic requires Windows process-tree killing.")

    root = (Path(".acceptance") / f"specialist-api-restart-{uuid4()}").resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    nonce = f"restart-evidence-{uuid4().hex}"
    fixture = workspace / "fixture.txt"
    fixture.write_text(nonce, encoding="utf-8")
    original_hash = hashlib.sha256(fixture.read_bytes()).hexdigest()
    token = secrets.token_urlsafe(48)
    (root / "api-token.txt").write_text(token, encoding="utf-8")
    run_id = seed_run(root / "runs.sqlite3", workspace)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})
    first: subprocess.Popen[bytes] | None = None
    second: subprocess.Popen[bytes] | None = None
    report: dict[str, Any] = {
        "passed": False,
        "scope": "authenticated API process kill and fresh-process specialist recovery",
        "run_id": run_id,
        "fixture_sha256": original_hash,
    }
    try:
        first = start_server(root, port, "first", nonce)
        wait_for_api(client, time.monotonic() + 20)
        response = client.post(f"/runs/{run_id}/plan/dispatch", timeout=5)
        response.raise_for_status()
        first_marker = root / "first-specialist.json"
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not first_marker.exists():
            if first.poll() is not None:
                raise RuntimeError("The first API exited before the specialist crash boundary.")
            time.sleep(0.1)
        if not first_marker.exists():
            raise TimeoutError("Specialist did not persist the read result before the deadline.")
        assert json.loads(first_marker.read_text(encoding="utf-8"))["saved_tool_results"] == 1
        kill_server_tree(first)
        with RunStore(root / "runs.sqlite3").connection() as connection:
            connection.execute(
                "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (run_id,),
            )

        second = start_server(root, port, "recovered", nonce)
        wait_for_api(client, time.monotonic() + 20)
        recovery_deadline = time.monotonic() + 20
        recovery: dict[str, Any] | None = None
        while time.monotonic() < recovery_deadline:
            response = client.get(f"/runs/{run_id}/tasks/reader/recovery", timeout=5)
            if response.status_code == 200:
                recovery = response.json()
                break
            if response.status_code not in {409, 500}:
                response.raise_for_status()
            time.sleep(0.2)
        if recovery is None or recovery.get("safe_to_resume") is not True:
            raise RuntimeError(f"Fresh API did not expose a safe recovery boundary: {recovery!r}")
        report["recovery_summary"] = recovery
        response = client.post(
            f"/runs/{run_id}/tasks/reader/recovery",
            json={"decision": "resume"},
            timeout=5,
        )
        response.raise_for_status()
        report["resume_status"] = response.status_code

        recovered_marker = root / "recovered-specialist.json"
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            if second.poll() is not None:
                raise RuntimeError("The recovery API exited before completing the specialist.")
            if recovered_marker.exists():
                run = client.get(f"/runs/{run_id}", timeout=5).json()
                if run.get("state") == "verifying":
                    break
            time.sleep(0.1)
        if not recovered_marker.exists():
            raise TimeoutError("The recovered specialist did not produce its saved-history result.")
        candidate = client.get(f"/runs/{run_id}/tasks/reader/candidate", timeout=5)
        candidate.raise_for_status()
        candidate_data = candidate.json()
        store = RunStore(root / "runs.sqlite3")
        task_results = [
            event
            for event in store.list_events(run_id)
            if event["event_type"] == "task.tool.result"
            and event["payload"].get("task_id") == "reader"
        ]
        final_content = candidate_data.get("candidate")
        if (
            len(task_results) != 1
            or not isinstance(final_content, str)
            or nonce not in final_content
            or hashlib.sha256(fixture.read_bytes()).hexdigest() != original_hash
        ):
            raise RuntimeError("Recovery repeated a read, lost evidence, or changed the fixture.")
        report.update(
            {
                "passed": True,
                "candidate_contains_fixture_nonce": True,
                "saved_task_read_results": len(task_results),
                "fixture_unchanged": True,
                "recovered_run_state": "verifying",
                "evidence_directory": str(root),
            }
        )
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {str(exc)[:500]}"
        raise
    finally:
        stop_server(client, second)
        stop_server(client, first)
        report_path = root / "report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        client.close()
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

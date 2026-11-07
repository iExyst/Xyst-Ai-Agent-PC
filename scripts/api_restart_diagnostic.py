"""Kill and restart the authenticated API while a safe inference is in flight."""

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
from xaa.storage.runs import RunStore


class ShortLeaseRunStore(RunStore):
    """Use short diagnostic leases so restart acceptance does not wait three minutes."""

    def acquire_lease(self, run_id: str, owner: str, *, seconds: int = 30) -> str | None:
        return super().acquire_lease(run_id, owner, seconds=min(seconds, 4))

    def renew_lease(self, run_id: str, token: str, *, seconds: int = 30) -> None:
        super().renew_lease(run_id, token, seconds=min(seconds, 4))


class RestartDiagnosticProvider(LMStudioProvider):
    def __init__(self, root: Path, phase: str) -> None:
        super().__init__(exact_context=False)
        self.base_url = "http://127.0.0.1:1234"
        self.root = root
        self.phase = phase

    @staticmethod
    def validate_endpoint(endpoint: str) -> str:
        return LMStudioProvider.validate_endpoint(endpoint)

    async def configure_endpoint(self, endpoint: str) -> str:
        self.base_url = self.validate_endpoint(endpoint)
        return self.base_url

    async def health(self) -> bool:
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
        objective = os.environ["XAA_DIAGNOSTIC_OBJECTIVE"]
        occurrences = sum(
            message.get("role") == "user" and message.get("content") == objective
            for message in messages
        )
        digest = hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()
        marker = self.root / f"{self.phase}-inference.json"
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
                    "message_count": len(messages),
                    "history_sha256": digest,
                    "tool_count": len(tools or []),
                }
            ),
            encoding="utf-8",
        )
        if self.phase == "crash":
            # The harness kills this API process while no model response or tool effect exists.
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


def run_server(root: Path, port: int, phase: str) -> None:
    import uvicorn
    from pydantic import SecretStr
    from xaa.api.app import create_app

    token = (root / "api-token.txt").read_text(encoding="utf-8")
    store = ShortLeaseRunStore(root / "runs.sqlite3")
    provider = RestartDiagnosticProvider(root, phase)
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


def start_server(root: Path, port: int, phase: str) -> subprocess.Popen[bytes]:
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


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--server":
        run_server(Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
        return

    root = (Path(".acceptance") / f"api-restart-{uuid4()}").resolve()
    root.mkdir(parents=True)
    token = secrets.token_urlsafe(48)
    objective = f"Restart fixture {uuid4()}"
    (root / "api-token.txt").write_text(token, encoding="utf-8")
    os.environ["XAA_DIAGNOSTIC_OBJECTIVE"] = objective
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    base_url = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})
    first: subprocess.Popen[bytes] | None = None
    second: subprocess.Popen[bytes] | None = None
    report: dict[str, Any] = {"passed": False, "scope": "authenticated API process restart"}
    run_id: str | None = None
    try:
        first = start_server(root, port, "crash")
        wait_for_api(client, time.monotonic() + 20)
        response = client.post(
            "/runs",
            json={
                "objective": objective,
                "acceptance_criteria": ["The restarted coordinator returns its saved candidate."],
                "mode": "observe",
                "model": "restart-diagnostic-model",
                "max_iterations": 3,
                "max_seconds": 120,
            },
            timeout=5,
        )
        response.raise_for_status()
        run_id = response.json()["id"]
        assert isinstance(run_id, str)
        marker = root / "crash-inference.json"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not marker.exists():
            if first.poll() is not None:
                raise RuntimeError("First API process exited before entering model inference.")
            time.sleep(0.05)
        if not marker.exists():
            raise TimeoutError("First API never reached the controlled inference boundary.")
        first.kill()
        first.wait(timeout=5)
        first_exit = first.returncode

        second = start_server(root, port, "resume")
        wait_for_api(client, time.monotonic() + 20)
        deadline = time.monotonic() + 35
        resumed: dict[str, Any] | None = None
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
        starts = [event for event in events if event["event_type"] == "model.inference.started"]
        answers = [event for event in events if event["event_type"] == "model.message"]
        resume_probe = json.loads((root / "resume-inference.json").read_text(encoding="utf-8"))
        store = RunStore(root / "runs.sqlite3")
        unresolved = store.unresolved_tool_calls(run_id)
        report.update(
            passed=(
                first_exit not in (0, None)
                and first.pid != second.pid
                and len(recovered) == 1
                and recovered[0]["payload"]["reason"] == "expired_worker"
                and len(starts) == 2
                and len(answers) == 1
                and resumed["state"] == "verifying"
                and resume_probe["objective_occurrences"] == 1
                and resume_probe["assistant_history_messages"] == 0
                and resume_probe["tool_history_messages"] == 0
                and len(unresolved) == 0
                and not any(event["event_type"] == "tool.intended" for event in events)
            ),
            run_id=run_id,
            first_api_pid=first.pid,
            first_api_exit_code=first_exit,
            resumed_api_pid=second.pid,
            state=resumed["state"],
            recovered_events=len(recovered),
            inference_attempts=len(starts),
            persisted_public_answers=len(answers),
            resumed_objective_occurrences=resume_probe["objective_occurrences"],
            resumed_prior_assistant_messages=resume_probe["assistant_history_messages"],
            unresolved_tool_calls=len(unresolved),
            side_effect_intents=sum(event["event_type"] == "tool.intended" for event in events),
            final_decision="verifying; user review remains required",
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
            first.kill()
            first.wait(timeout=5)
        client.close()
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    main()

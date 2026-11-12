"""Run one authorized scheduled file creation through a frozen X.A.A. engine."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from frozen_api_restart_diagnostic import (
    CREATE_CONTENT,
    CREATE_PATH,
    FixtureLMStudio,
    clean_launch_credentials,
    free_port,
    start_api,
    wait_for_api,
)
from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE, CredentialStore
from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore
from xaa.storage.schedules import ScheduleStore, parse_local_time


def _acceptance_root() -> Path:
    requested = os.environ.get("XAA_FROZEN_SCHEDULE_WRITE_ROOT")
    root = (
        Path(requested).resolve()
        if requested
        else (Path(".acceptance") / f"frozen-scheduled-write-{uuid4()}").resolve()
    )
    acceptance_root = Path(".acceptance").resolve() / ""
    if not root.is_relative_to(acceptance_root) or root.exists():
        raise RuntimeError("Scheduled-write acceptance needs a new directory inside .acceptance.")
    root.mkdir(parents=True)
    return root


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("Frozen scheduled-write acceptance requires Windows.")
    executable = Path(
        os.environ.get("XAA_FROZEN_API_EXECUTABLE", "build/native-release/xaa-engine.exe")
    ).resolve(strict=True)
    credential_store = CredentialStore()
    if credential_store.get(API_LAUNCH_TOKEN_PROFILE) or credential_store.get(
        API_LAUNCH_URL_PROFILE
    ):
        raise RuntimeError("An API launch credential already exists; refusing to overwrite it.")

    root = _acceptance_root()
    workspace = root / "workspace"
    workspace.mkdir()
    target = workspace.joinpath(*CREATE_PATH.split("/"))
    target.parent.mkdir(parents=True)
    data_root = root / "data"
    database = data_root / "XAA" / "xaa.sqlite3"
    store = RunStore(database)
    schedule = ScheduleStore(store).create(
        objective="Create the scheduled acceptance file in the selected workspace.",
        acceptance_criteria=["The fixture file is created exactly once."],
        workspace=str(workspace),
        model="restart-diagnostic-model",
        start_at=parse_local_time(
            (datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2))
            .replace(tzinfo=None)
            .isoformat(timespec="minutes")
        ),
        frequency="once",
        timezone="UTC",
        missed_run_policy="run_once",
        max_iterations=4,
        max_seconds=90,
        mode="authorized_automation",
    )

    token = secrets.token_urlsafe(48)
    api_port = free_port()
    fixture = FixtureLMStudio("create")
    fixture_port = fixture.server_address[1]
    fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fixture_thread.start()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{api_port}",
        headers={"Authorization": f"Bearer {token}"},
    )
    process = None
    report: dict[str, Any] = {
        "passed": False,
        "provider": "local deterministic OpenAI-compatible fixture; no LM Studio or live model",
        "live_model_inference_requested": False,
        "engine_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "schedule_id": schedule["id"],
        "schedule_mode": schedule["mode"],
        "workspace": str(workspace),
        "target": str(target),
        "evidence": str(root),
    }
    try:
        process = start_api(executable, root, api_port, fixture_port, token, "scheduled-write")
        wait_for_api(client, time.monotonic() + 30)
        deadline = time.monotonic() + 60
        execution: dict[str, Any] | None = None
        run: dict[str, Any] | None = None
        review_submitted = False
        while time.monotonic() < deadline:
            schedules_response = client.get("/schedules", timeout=5)
            schedules_response.raise_for_status()
            saved = next(
                (item for item in schedules_response.json() if item["id"] == schedule["id"]),
                None,
            )
            if saved is None:
                raise RuntimeError("Frozen engine did not retain the authorized schedule.")
            if saved["mode"] != "authorized_automation":
                raise RuntimeError("Frozen engine changed the saved schedule authorization mode.")
            executions = saved.get("executions", [])
            if executions:
                candidate_execution = executions[0]
                if not isinstance(candidate_execution, dict):
                    raise RuntimeError("Frozen engine returned an invalid scheduled execution.")
                execution = cast(dict[str, Any], candidate_execution)
                if execution.get("run_id"):
                    run = store.get_run(str(execution["run_id"]))
                if run is not None and run["state"] == RunState.VERIFYING.value:
                    if not target.is_file() or target.read_text(encoding="utf-8") != CREATE_CONTENT:
                        raise RuntimeError("Scheduled file evidence failed before owner review.")
                    events = store.list_events(str(run["id"]))
                    if not any(event["event_type"] == "tool.completed" for event in events):
                        raise RuntimeError("Scheduled file has no durable tool-completion event.")
                    if fixture.workspace_tool_requests != 1:
                        raise RuntimeError(
                            "The deterministic provider did not produce exactly one file-create call."
                        )
                    review = client.post(
                        f"/runs/{run['id']}/review",
                        json={
                            "decision": "accept",
                            "expected_revision": run["revision"],
                            "rationale": "The isolated acceptance fixture matches the saved criterion.",
                            "objective_confirmed": True,
                            "confirmed_criteria": [0],
                        },
                        timeout=10,
                    )
                    review.raise_for_status()
                    run = review.json()
                    review_submitted = True
                if execution.get("state") in {"failed", "missed", "overlap_skipped"}:
                    raise RuntimeError(f"Scheduled occurrence ended as {execution['state']}.")
                if execution.get("state") == "completed" and run is not None:
                    break
            if process.poll() is not None:
                raise RuntimeError("Frozen engine exited before scheduled write completed.")
            time.sleep(0.2)
        if execution is None:
            raise TimeoutError("Authorized scheduled file creation did not produce an occurrence.")
        if run is None:
            raise TimeoutError("Authorized scheduled file creation did not create a run.")
        if execution.get("state") != "completed":
            raise TimeoutError(
                "Authorized scheduled file creation did not complete within 60 seconds."
            )
        if run["mode"] != "authorized_automation" or run["workspace"] != str(workspace):
            raise RuntimeError("The scheduled run did not preserve its workspace-only grant.")
        if run.get("command_grant") is not None:
            raise RuntimeError("Authorized automation unexpectedly received a command grant.")
        if fixture.workspace_tool_requests != 1:
            raise RuntimeError(
                f"Expected one fixture file-create proposal, got {fixture.workspace_tool_requests}."
            )
        if not target.is_file() or target.read_text(encoding="utf-8") != CREATE_CONTENT:
            raise RuntimeError(
                "The scheduled workspace tool did not create the exact fixture bytes."
            )
        events = store.list_events(str(run["id"]))
        if not any(event["event_type"] == "tool.completed" for event in events):
            raise RuntimeError("Scheduled file creation has no durable tool-completion event.")
        report.update(
            {
                "run_id": run["id"],
                "run_state": run["state"],
                "execution_id": execution["id"],
                "execution_state": execution["state"],
                "workspace_create_proposals": fixture.workspace_tool_requests,
                "created_file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                "workspace_file_created": True,
                "command_grant": False,
                "durable_tool_completion": True,
                "owner_review_submitted": review_submitted,
            }
        )
        if run["state"] != RunState.COMPLETED.value:
            raise RuntimeError(f"Scheduled run ended as {run['state']} instead of completed.")
        shutdown = client.post("/shutdown", timeout=5)
        shutdown.raise_for_status()
        process.wait(timeout=15)
        if process.returncode != 0:
            raise RuntimeError(f"Frozen engine shutdown returned {process.returncode}.")
        report["graceful_shutdown"] = True
        report["passed"] = True
        print(json.dumps(report))
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if process is not None and process.poll() is None:
            try:
                client.post("/shutdown", timeout=2)
                process.wait(timeout=10)
            except (httpx.HTTPError, subprocess.TimeoutExpired):
                process.kill()
                process.wait(timeout=5)
        clean_launch_credentials(credential_store, token, f"http://127.0.0.1:{api_port}")
        client.close()
        fixture.shutdown()
        fixture.server_close()
        fixture_thread.join(timeout=2)
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

"""Run one coordinator command through a saved Windows AppContainer grant."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from frozen_api_restart_diagnostic import (
    FixtureLMStudio,
    clean_launch_credentials,
    free_port,
    start_api,
    wait_for_api,
)
from xaa.credentials import API_LAUNCH_TOKEN_PROFILE, API_LAUNCH_URL_PROFILE, CredentialStore
from xaa.runtime.execution_readiness import WINDOWS_LOCAL_CHECKS
from xaa.runtime.states import RunState
from xaa.runtime.windows_local_probe import command_argv as probe_command_argv
from xaa.storage.runs import RunStore


def _acceptance_root(report: Path | None) -> tuple[Path, Path]:
    acceptance_root = Path(".acceptance").resolve(strict=True)
    if report is None:
        root = acceptance_root / f"frozen-windows-appcontainer-command-{uuid4()}"
        report_path = root / "report.json"
    else:
        report_path = report.resolve()
        root = report_path.parent
    if not root.is_relative_to(acceptance_root) or root.exists():
        raise RuntimeError("Acceptance output needs a new directory inside .acceptance.")
    if report_path.exists() or not report_path.is_relative_to(root):
        raise RuntimeError("Acceptance report must be a new file inside its new output directory.")
    root.mkdir(parents=True, exist_ok=False)
    return root, report_path


def _download_artifact(client: httpx.Client, artifact_id: str) -> bytes:
    response = client.get(f"/artifacts/{artifact_id}", timeout=10)
    response.raise_for_status()
    return response.content


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise RuntimeError("Frozen Windows AppContainer command acceptance requires Windows.")
    engine = args.engine.resolve(strict=True)
    if not engine.is_file():
        raise SystemExit("--engine must point to a frozen engine executable.")
    default_credential_store = CredentialStore()
    default_credentials = {
        API_LAUNCH_TOKEN_PROFILE: default_credential_store.get(API_LAUNCH_TOKEN_PROFILE),
        API_LAUNCH_URL_PROFILE: default_credential_store.get(API_LAUNCH_URL_PROFILE),
    }
    credential_namespace = f"acceptance-{uuid4().hex}"
    os.environ["XAA_CREDENTIAL_NAMESPACE"] = credential_namespace
    credential_store = CredentialStore()
    if credential_store.get(API_LAUNCH_TOKEN_PROFILE) or credential_store.get(
        API_LAUNCH_URL_PROFILE
    ):
        raise RuntimeError("The isolated acceptance credential namespace is already occupied.")
    root, report_path = _acceptance_root(args.report)
    workspace = root / "workspace"
    workspace.mkdir()
    nonce = uuid4().hex
    (workspace / "probe.txt").write_text(nonce, encoding="utf-8")
    (workspace / "tests").mkdir()
    nested_fixture = b"packaged nested AppContainer fixture"
    (workspace / "tests" / "nested-fixture.txt").write_bytes(nested_fixture)
    outside = root / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_bytes(b"host file must remain outside the command snapshot")

    token = secrets.token_urlsafe(48)
    api_port = free_port()
    fixture = FixtureLMStudio(
        "appcontainer_command",
        command_argv=probe_command_argv(str(engine), api_port, frozen=True),
    )
    fixture_port = int(fixture.server_address[1])
    fixture_thread = threading.Thread(target=fixture.serve_forever, daemon=True)
    fixture_thread.start()
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{api_port}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    process: subprocess.Popen[bytes] | None = None
    database = root / "data" / "XAA" / "xaa.sqlite3"
    store = RunStore(database)
    report: dict[str, Any] = {
        "schema_version": 1,
        "acceptance": "frozen coordinator command through Windows AppContainer",
        "passed": False,
        "engine": str(engine),
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "provider": "local deterministic OpenAI-compatible fixture; no live model",
        "model_inference_requested": False,
        "credential_namespace": credential_namespace,
        "workspace": str(workspace),
        "outside_sentinel": str(sentinel),
        "evidence": str(root),
    }
    try:
        process = start_api(engine, root, api_port, fixture_port, token, "appcontainer-command")
        wait_for_api(client, time.monotonic() + 45)
        readiness_response = client.post(
            "/execution/readiness",
            json={"backend": "windows-appcontainer-v1", "distribution": "Windows-Local"},
            timeout=45,
        )
        readiness_response.raise_for_status()
        readiness = readiness_response.json()
        if (
            readiness.get("state") != "ready"
            or readiness.get("backend") != "windows-appcontainer-v1"
            or readiness.get("distribution") != "Windows-Local"
            or readiness.get("checks") != list(WINDOWS_LOCAL_CHECKS)
            or not isinstance(readiness.get("profile_sha256"), str)
        ):
            raise RuntimeError(
                "Frozen Windows AppContainer readiness failed: " + json.dumps(readiness)
            )
        grant = {
            "backend": "windows-appcontainer-v1",
            "distribution": "Windows-Local",
            "profile_sha256": readiness["profile_sha256"],
            "max_seconds": 30,
        }
        created = client.post(
            "/runs",
            json={
                "objective": "Run the packaged Windows AppContainer probe in a private workspace copy.",
                "acceptance_criteria": [
                    "The probe confirms AppContainer identity, denied network, private temp access, and an isolated writable snapshot."
                ],
                "workspace": str(workspace),
                "mode": "workspace",
                "model": "restart-diagnostic-model",
                "max_iterations": 3,
                "max_seconds": 120,
                "command_grant": grant,
            },
            timeout=10,
        )
        created.raise_for_status()
        run_id = str(created.json()["id"])
        if created.json().get("command_grant") != grant:
            raise RuntimeError("The engine did not persist the exact AppContainer grant.")
        deadline = time.monotonic() + 100
        run: dict[str, Any] | None = None
        reviewed = False
        verified_result: dict[str, Any] | None = None
        output: dict[str, bytes] = {}
        while time.monotonic() < deadline:
            response = client.get(f"/runs/{run_id}", timeout=5)
            response.raise_for_status()
            run = cast(dict[str, Any], response.json())
            if run.get("state") == RunState.VERIFYING.value:
                events = store.list_events(run_id)
                call_id = "frozen-appcontainer-command-1"
                expected = {
                    "tool.intended": 1,
                    "tool.executing": 1,
                    "tool.completed": 1,
                    "execution.completed": 1,
                }
                for event_type, count in expected.items():
                    matching = [
                        event
                        for event in events
                        if event["event_type"] == event_type
                        and event["payload"].get("call_id") == call_id
                    ]
                    if len(matching) != count:
                        raise RuntimeError(
                            f"Expected {count} {event_type} event(s), found {len(matching)}."
                        )
                intent = next(
                    event
                    for event in events
                    if event["event_type"] == "tool.intended"
                    and event["payload"].get("call_id") == call_id
                )
                if intent["payload"].get("tool") != "workspace.run_command":
                    raise RuntimeError("The durable intent does not identify a command tool call.")
                if fixture.workspace_tool_requests != 1:
                    raise RuntimeError("The local fixture did not propose exactly one command.")
                result_response = client.get(f"/runs/{run_id}/tools/{call_id}/result", timeout=5)
                result_response.raise_for_status()
                encoded_result = result_response.json().get("result")
                if not isinstance(encoded_result, str):
                    raise RuntimeError("The completed command result is unavailable.")
                decoded_result: object = json.loads(encoded_result)
                if not isinstance(decoded_result, dict):
                    raise TypeError("The completed command result is not a JSON object.")
                verified_result = cast(dict[str, Any], decoded_result)
                command_result_checks = {
                    "succeeded": verified_result.get("succeeded") is True,
                    "cleanup_confirmed": verified_result.get("cleanup_confirmed") is True,
                    "workspace_changes_persisted_is_false": verified_result.get(
                        "workspace_changes_persisted"
                    )
                    is False,
                    "trust_is_untrusted_command_output": verified_result.get("trust")
                    == "untrusted_command_output",
                }
                report["command_result_checks"] = command_result_checks
                if not all(command_result_checks.values()):
                    raise RuntimeError(
                        "The saved command result did not pass its sanitized success checks: "
                        + json.dumps(command_result_checks, sort_keys=True)
                    )
                for stream in ("stdout", "stderr"):
                    artifact_id = verified_result.get(f"{stream}_artifact_id")
                    if not isinstance(artifact_id, str):
                        raise TypeError(f"The command has no saved {stream} artifact.")
                    data = _download_artifact(client, artifact_id)
                    if len(data) != verified_result.get(f"{stream}_bytes") or hashlib.sha256(
                        data
                    ).hexdigest() != verified_result.get(f"{stream}_sha256"):
                        raise RuntimeError(f"The saved {stream} artifact failed integrity checks.")
                    output[stream] = data
                probe = json.loads(output["stdout"].decode("utf-8"))
                if probe != {
                    "nonce": nonce,
                    "appcontainer": True,
                    "network_denied": True,
                    "private_temp": True,
                    "nested_snapshot_access": True,
                    "checks": True,
                }:
                    raise RuntimeError("The frozen AppContainer child probe failed: " + repr(probe))
                if (workspace / "private-write.txt").exists() or (
                    workspace / "tests" / "private-write.txt"
                ).exists():
                    raise RuntimeError("The command wrote into the original workspace.")
                if sentinel.read_bytes() != b"host file must remain outside the command snapshot":
                    raise RuntimeError("The command changed a file outside its selected workspace.")
                review = client.post(
                    f"/runs/{run_id}/review",
                    json={
                        "decision": "accept",
                        "expected_revision": run["revision"],
                        "rationale": "The packaged child probe and isolated command artifacts passed their checks.",
                        "objective_confirmed": True,
                        "confirmed_criteria": [0],
                    },
                    timeout=10,
                )
                review.raise_for_status()
                run = cast(dict[str, Any], review.json())
                reviewed = True
                break
            if run.get("state") in {
                RunState.FAILED.value,
                RunState.BLOCKED.value,
                RunState.CANCELLED.value,
            }:
                raise RuntimeError(f"Frozen AppContainer command ended in {run['state']}.")
            if process.poll() is not None:
                raise RuntimeError("Frozen engine exited before the command was reviewed.")
            time.sleep(0.2)
        if run is None or run.get("state") != RunState.COMPLETED.value or not reviewed:
            raise TimeoutError("Frozen AppContainer command did not finish explicit review.")
        if fixture.workspace_tool_requests != 1:
            raise RuntimeError("The fixture requested an unexpected number of command calls.")
        if (workspace / "probe.txt").read_text(encoding="utf-8") != nonce:
            raise RuntimeError("The selected source workspace changed during command execution.")
        if (
            (workspace / "private-write.txt").exists()
            or (workspace / "tests" / "private-write.txt").exists()
            or (workspace / "tests" / "nested-fixture.txt").read_bytes() != nested_fixture
            or sentinel.read_bytes() != b"host file must remain outside the command snapshot"
        ):
            raise RuntimeError("The AppContainer command changed files outside its private copy.")
        if verified_result is None:
            raise RuntimeError("The accepted run has no saved command result.")
        result = verified_result
        report.update(
            {
                "run_id": run["id"],
                "run_state": run["state"],
                "readiness_checks": readiness["checks"],
                "profile_sha256": readiness["profile_sha256"],
                "saved_command_grant": run["command_grant"],
                "fixture_command_proposals": fixture.workspace_tool_requests,
                "command_succeeded": result["succeeded"],
                "command_cleanup_confirmed": result["cleanup_confirmed"],
                "appcontainer_identity_verified": True,
                "network_denial_verified": True,
                "private_runtime_temp_verified": True,
                "nested_snapshot_access_verified": True,
                "stdout_sha256": hashlib.sha256(output["stdout"]).hexdigest(),
                "stderr_sha256": hashlib.sha256(output["stderr"]).hexdigest(),
                "tool_intended_executing_completed": True,
                "owner_review_submitted": reviewed,
                "original_workspace_unchanged": True,
                "outside_sentinel_unchanged": True,
            }
        )
        shutdown = client.post("/shutdown", timeout=5)
        shutdown.raise_for_status()
        process.wait(timeout=15)
        if process.returncode != 0:
            raise RuntimeError(f"Frozen engine shutdown returned {process.returncode}.")
        report["graceful_shutdown"] = True
        report["launch_credentials_cleaned"] = clean_launch_credentials(
            credential_store, token, f"http://127.0.0.1:{api_port}"
        )
        if not report["launch_credentials_cleaned"]:
            raise RuntimeError("Frozen engine launch credentials were not cleaned up.")
        report["user_launch_credentials_unchanged"] = all(
            default_credential_store.get(profile) == value
            for profile, value in default_credentials.items()
        )
        if not report["user_launch_credentials_unchanged"]:
            raise RuntimeError("Acceptance changed the user's active desktop credentials.")
        report["passed"] = True
        print(json.dumps(report, indent=2))
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
        report["engine_exit_code"] = None if process is None else process.returncode
        clean_launch_credentials(credential_store, token, f"http://127.0.0.1:{api_port}")
        client.close()
        fixture.shutdown()
        fixture.server_close()
        fixture_thread.join(timeout=2)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

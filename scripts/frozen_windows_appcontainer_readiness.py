"""Probe Windows AppContainer readiness through a freshly packaged engine."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast
from uuid import uuid4

EXPECTED_CHECKS = [
    "appcontainer_identity",
    "network_capabilities_absent",
    "private_snapshot",
    "nested_snapshot_access",
    "workspace_write_isolated",
    "private_runtime_temp",
    "credential_environment_absent",
    "bounded_child_process_tree",
    "cleanup_confirmed",
]


def _request(
    url: str,
    token: str,
    *,
    body: dict[str, object] | None = None,
    timeout: float = 3,
) -> dict[str, object]:
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="GET" if body is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        decoded: object = json.loads(response.read())
    if not isinstance(decoded, dict):
        raise TypeError("Frozen engine returned an invalid API response.")
    return cast(dict[str, object], decoded)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Frozen Windows AppContainer acceptance requires Windows.")
    engine = args.engine.resolve(strict=True)
    if not engine.is_file():
        raise SystemExit("--engine must point to a frozen engine executable.")
    run_id = uuid4().hex
    acceptance_root = Path(".acceptance").resolve()
    root = (
        args.report.resolve().parent
        if args.report is not None
        else acceptance_root / f"frozen-windows-appcontainer-{run_id}"
    )
    if not root.is_relative_to(acceptance_root):
        raise SystemExit("Acceptance output must remain inside .acceptance.")
    root.mkdir(parents=True, exist_ok=False)
    report_path = args.report.resolve() if args.report is not None else root / "report.json"
    if report_path.exists() or not report_path.is_relative_to(root):
        raise SystemExit("Acceptance report must be a new file inside its new output directory.")
    data_dir = root / "data"
    local_app_data = root / "local-app-data"
    app_data = root / "app-data"
    data_dir.mkdir()
    local_app_data.mkdir()
    app_data.mkdir()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(48)
    base_url = f"http://127.0.0.1:{port}"
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        not in {
            "XAA_API_TOKEN",
            "XAA_API_URL",
            "XAA_API_PORT",
            "XAA_DATA_DIR",
            "LM_STUDIO_API_TOKEN",
            "LM_API_TOKEN",
        }
    }
    env.update(
        {
            "XAA_API_TOKEN": token,
            "XAA_API_PORT": str(port),
            "XAA_DATA_DIR": str(data_dir),
            "LOCALAPPDATA": str(local_app_data),
            "APPDATA": str(app_data),
            "XAA_STREAM_RESPONSES": "0",
        }
    )
    log_path = root / "engine.log"
    started = time.monotonic()
    process: subprocess.Popen[bytes] | None = None
    result: dict[str, object] = {
        "schema_version": 1,
        "acceptance": "frozen Windows AppContainer readiness",
        "passed": False,
        "engine": str(engine),
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "model_inference_requested": False,
    }
    try:
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [str(engine)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ready = False
            startup_deadline = time.monotonic() + 60
            last_error = "API has not started"
            while time.monotonic() < startup_deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        f"Frozen engine exited during startup ({process.returncode})."
                    )
                try:
                    _request(f"{base_url}/runs", token)
                    ready = True
                    break
                except (OSError, urllib.error.URLError, TimeoutError) as exc:
                    last_error = str(exc)
                    time.sleep(0.2)
            if not ready:
                raise RuntimeError(f"Frozen engine API did not start: {last_error}")
            readiness = _request(
                f"{base_url}/execution/readiness",
                token,
                body={
                    "backend": "windows-appcontainer-v1",
                    "distribution": "Windows-Local",
                },
                timeout=45,
            )
            elapsed = time.monotonic() - started
            checks = readiness.get("checks")
            if (
                readiness.get("state") != "ready"
                or readiness.get("backend") != "windows-appcontainer-v1"
                or readiness.get("distribution") != "Windows-Local"
                or checks != EXPECTED_CHECKS
                or not isinstance(checks, list)
                or not isinstance(readiness.get("profile_sha256"), str)
            ):
                raise RuntimeError(
                    "Frozen engine AppContainer readiness did not satisfy the expected checks: "
                    + json.dumps(readiness, sort_keys=True)
                )
            verified_checks = cast(list[str], checks)
            result.update(
                {
                    "passed": True,
                    "state": readiness["state"],
                    "checks": verified_checks,
                    "profile_sha256": readiness["profile_sha256"],
                    "elapsed_seconds": round(elapsed, 3),
                    "cleanup_confirmed": "cleanup_confirmed" in verified_checks,
                }
            )
            _request(f"{base_url}/shutdown", token, body={})
            process.wait(timeout=15)
            if process.returncode != 0:
                raise RuntimeError(
                    f"Frozen engine did not shut down cleanly ({process.returncode})."
                )
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        result["engine_exit_code"] = None if process is None else process.returncode
        result["log_path"] = str(log_path)
        report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

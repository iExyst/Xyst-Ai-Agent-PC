"""Trusted Linux manager for one explicitly selected, bounded sandbox execution.

Run as a standalone file in the configured WSL distribution. Only packaged codec
and profile paths are command-line parameters; request data is bounded JSON stdin.
"""

import base64
import json
import os
import re
import runpy
import selectors
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

BASE = Path("/tmp/xaa-executions")
MAX_REQUEST_BYTES = 18 * 1024 * 1024
MAX_OUTPUT_BYTES = 1024 * 1024


def execution_id(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Execution identity must be an application-generated UUID hex value.")
    return value


def prepare_base() -> None:
    BASE.mkdir(mode=0o700, exist_ok=True)
    info = BASE.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError("Sandbox manager staging must be a private root-owned directory.")


def stop_unit(identity: str) -> None:
    identity = execution_id(identity)
    unit = f"xaa-exec-{identity}.service"
    subprocess.run(
        ["/usr/bin/systemctl", "stop", unit], check=False, capture_output=True, timeout=5
    )
    subprocess.run(
        ["/usr/bin/systemctl", "reset-failed", unit], check=False, capture_output=True, timeout=5
    )
    state = (
        subprocess.run(
            ["/usr/bin/systemctl", "show", unit, "--property=ActiveState", "--value"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        .stdout.decode("utf-8")
        .strip()
    )
    if state != "inactive":
        raise RuntimeError("Cannot confirm the exact owned execution unit is inactive.")


def cleanup(identity: str) -> None:
    identity = execution_id(identity)
    stop_unit(identity)
    stage = BASE / identity
    if stage.exists() or stage.is_symlink():
        info = stage.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
            raise PermissionError("Refusing non-directory or unowned execution cleanup target.")
        if stage.resolve().parent != BASE.resolve() or not shutil.rmtree.avoids_symlink_attacks:
            raise PermissionError("Safe execution staging cleanup is unavailable.")
        shutil.rmtree(stage)
    (BASE / (identity + ".cancelled")).unlink(missing_ok=True)


def run(request: dict[str, Any], codec: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    if set(request) != {"action", "execution_id", "argv", "max_seconds", "snapshot_b64"}:
        raise ValueError("Invalid execution request fields.")
    identity = execution_id(request["execution_id"])
    raw_arguments = request["argv"]
    if not isinstance(raw_arguments, list):
        raise TypeError("Command arguments must be an array.")
    values = cast(list[Any], raw_arguments)
    if not 1 <= len(values) <= 64:
        raise ValueError("Supply 1..64 structured command arguments.")
    if any(not isinstance(value, str) or "\0" in value for value in values):
        raise ValueError("Command arguments must be strings without NUL characters.")
    arguments = cast(list[str], values)
    if not arguments[0] or sum(len(value.encode("utf-8")) for value in arguments) > 16384:
        raise ValueError("Command arguments exceed the configured size limit.")
    seconds = request["max_seconds"]
    if type(seconds) is not int or not 1 <= seconds <= 30:
        raise ValueError("Command runtime must be 1..30 seconds.")
    encoded_snapshot = request["snapshot_b64"]
    if not isinstance(encoded_snapshot, str):
        raise TypeError("Missing snapshot wire data.")
    snapshot = codec["WorkspaceSnapshot"].from_bytes(
        base64.b64decode(encoded_snapshot, validate=True)
    )
    bpf = profile["syscall_filter"]()
    stage = BASE / identity
    stage.mkdir(mode=0o700, exist_ok=False)
    process: subprocess.Popen[bytes] | None = None
    result: dict[str, Any] = {}
    try:
        source = stage / "input"
        codec["materialize_snapshot"](snapshot, source)
        unit = f"xaa-exec-{identity}.service"
        command = [
            "/usr/bin/systemd-run",
            "--unit",
            unit,
            "--wait",
            "--pipe",
            "--property=MemoryMax=128M",
            "--property=MemorySwapMax=0",
            "--property=CPUQuota=50%",
            "--property=TasksMax=32",
            f"--property=RuntimeMaxSec={seconds}s",
            "--property=TimeoutStopSec=1s",
            "--property=KillMode=control-group",
        ] + profile["bubblewrap_arguments"](source, arguments)
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        assert (
            process.stdin is not None and process.stdout is not None and process.stderr is not None
        )
        process.stdin.write(bpf)
        process.stdin.close()
        output = {"stdout": bytearray(), "stderr": bytearray()}
        reason = "finished"
        total = 0
        with selectors.DefaultSelector() as selector:
            for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, name)
            while selector.get_map():
                if time.monotonic() - started > seconds + 5 and reason == "finished":
                    reason = "manager_deadline"
                    cleanup(identity)
                for key, _ in selector.select(timeout=0.1):
                    chunk = os.read(key.fd, 16384)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    remaining = MAX_OUTPUT_BYTES - total
                    output[key.data].extend(chunk[:remaining])
                    total += min(len(chunk), remaining)
                    if len(chunk) > remaining and reason == "finished":
                        reason = "output_limit"
                        cleanup(identity)
        exit_code = process.wait(timeout=5)
        unit_state = subprocess.run(
            ["/usr/bin/systemctl", "show", unit, "--property=Result", "--property=ExecMainStatus"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
        metadata = dict(line.split("=", 1) for line in unit_state if "=" in line)
        unit_result = metadata.get("Result", "success" if exit_code == 0 else "unknown")
        if reason == "finished" and unit_result in {"timeout", "oom-kill", "signal", "resources"}:
            reason = unit_result
        if (BASE / (identity + ".cancelled")).exists():
            reason = "cancelled"
        result = {
            "execution_id": identity,
            "backend": "wsl-bubblewrap-v1",
            "snapshot_sha256": snapshot.sha256,
            "exit_code": exit_code if reason == "finished" else None,
            "succeeded": reason == "finished" and exit_code == 0,
            "supervisor_exit_code": exit_code,
            "stdout_b64": base64.b64encode(output["stdout"]).decode("ascii"),
            "stderr_b64": base64.b64encode(output["stderr"]).decode("ascii"),
            "termination_reason": reason,
            "unit_result": unit_result,
            "output_complete": reason not in {"output_limit", "manager_deadline"},
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "workspace_changes_persisted": False,
            "limits": {
                "memory_bytes": 134217728,
                "cpu_percent": 50,
                "tasks": 32,
                "scratch_bytes": 33554432,
                "workspace_bytes": 33554432,
                "output_bytes": MAX_OUTPUT_BYTES,
                "seconds": seconds,
                "network": "none",
            },
            "trust": "untrusted_command_output",
        }
    finally:
        cleanup(identity)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        result["cleanup_confirmed"] = True
    return result


def decode_request(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Execution request exceeds its transport limit.")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate execution request key.")
            result[key] = value
        return result

    request = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(request, dict):
        raise TypeError("Execution request must be a JSON object.")
    return cast(dict[str, Any], request)


def main() -> None:
    if os.name != "posix" or os.getuid() != 0:
        raise PermissionError("The selected WSL manager must have root service-management access.")
    os.environ.clear()
    os.environ.update({"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    request = decode_request(raw)
    prepare_base()
    if request.get("action") == "stop" and set(request) == {"action", "execution_id"}:
        identity = execution_id(request["execution_id"])
        receipt = BASE / (identity + ".cancelled")
        try:
            with receipt.open("xb") as target:
                target.write(b"cancelled")
                target.flush()
                os.fsync(target.fileno())
        except FileExistsError:
            if receipt.is_symlink() or receipt.read_bytes() != b"cancelled":
                raise PermissionError("Invalid cancellation receipt.")
        stop_unit(identity)
        print(
            json.dumps({"execution_id": identity, "unit_stopped": True, "cleanup_confirmed": False})
        )
    elif request.get("action") == "cleanup" and set(request) == {"action", "execution_id"}:
        cleanup(execution_id(request["execution_id"]))
        print(json.dumps({"execution_id": request["execution_id"], "cleanup_confirmed": True}))
    elif request.get("action") == "run":
        result = run(request, runpy.run_path(sys.argv[1]), runpy.run_path(sys.argv[2]))
        print(json.dumps(result))
    else:
        raise ValueError("Unsupported execution manager operation.")


if __name__ == "__main__":
    main()

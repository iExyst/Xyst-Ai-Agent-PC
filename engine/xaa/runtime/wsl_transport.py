"""Application-selected WSL resources and structured, bounded manager IPC."""

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import TransportError, request_process
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot

RESOURCE_NAMES = (
    "wsl_manager.py",
    "workspace_snapshot.py",
    "sandbox_profile.py",
    "execution_receipts.py",
    "cgroup_gate.py",
)


def profile_fingerprint() -> tuple[str, tuple[Path, ...]]:
    resources = tuple(Path(__file__).resolve().with_name(name) for name in RESOURCE_NAMES)
    fingerprints = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in resources}
    digest = hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()
    return digest, resources


def response_object(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise TransportError("Duplicate manager response field.")
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise TransportError("Invalid manager response; execution must be reconciled.") from exc
    if not isinstance(result, dict):
        raise TransportError("Manager response must be an object.")
    return cast(dict[str, Any], result)


@dataclass(frozen=True)
class WSLTransport:
    distribution: str
    profile_sha256: str
    invocation: tuple[str, ...]

    @classmethod
    async def discover(cls, distribution: str, *, deadline: float) -> "WSLTransport":
        if os.name != "nt" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", distribution):
            raise ValueError("Select an existing WSL distribution on Windows.")
        digest, resources = profile_fingerprint()
        executable = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe")
        prefix = (executable, "--distribution", distribution, "--user", "root", "--exec")
        mapper = (
            "import json,subprocess,sys\n"
            "paths=json.load(sys.stdin)\n"
            "mapped=[]\n"
            "for path in paths:\n"
            " result=subprocess.run(['/usr/bin/wslpath','-a','-u',path],check=True,"
            "stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)\n"
            " mapped.append(result.stdout.strip())\n"
            "sys.stdout.write(json.dumps(mapped))\n"
        )
        raw = await request_process(
            (*prefix, "/usr/bin/python3", "-I", "-c", mapper),
            json.dumps([str(path) for path in resources]).encode("utf-8"),
            timeout=min(30, deadline - time.monotonic()),
            output_limit=8192,
        )
        try:
            decoded: object = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise TransportError("WSL returned invalid resource path data.") from exc
        if not isinstance(decoded, list) or len(cast(list[object], decoded)) != len(resources):
            raise TransportError("WSL returned an incomplete resource path list.")
        paths: list[str] = []
        for value in cast(list[object], decoded):
            if (
                not isinstance(value, str)
                or not value.startswith("/")
                or any(char in value for char in "\r\n\0")
            ):
                raise TransportError("WSL returned an invalid resource path.")
            paths.append(value)
        if profile_fingerprint()[0] != digest:
            raise TransportError("Execution resources changed during discovery.")
        return cls(distribution, digest, (*prefix, "/usr/bin/python3", "-I", *paths[:4]))

    def require_intent(self, intent: CommandIntent) -> None:
        if intent.distribution != self.distribution or intent.profile_sha256 != self.profile_sha256:
            raise PermissionError("Execution grant does not match the selected WSL profile.")
        if profile_fingerprint()[0] != self.profile_sha256:
            raise PermissionError("Execution profile changed; obtain a fresh explicit grant.")

    async def run(
        self, intent: CommandIntent, snapshot: WorkspaceSnapshot, *, deadline: float
    ) -> dict[str, Any]:
        self.require_intent(intent)
        remaining = deadline - time.monotonic()
        if remaining < intent.max_seconds:
            raise TimeoutError("Command duration exceeds the remaining parent time budget.")
        payload = json.dumps(
            {
                "action": "run",
                "execution_id": intent.execution_id,
                "argv": intent.argv,
                "max_seconds": intent.max_seconds,
                "snapshot_b64": base64.b64encode(snapshot.to_bytes()).decode("ascii"),
            }
        ).encode("utf-8")
        if len(payload) > 18 * 1024 * 1024:
            raise ValueError("Command request exceeds the manager transport limit.")
        raw = await request_process(
            self.invocation,
            payload,
            timeout=deadline - time.monotonic(),
            output_limit=2 * 1024 * 1024,
        )
        result = response_object(raw)
        if result.get("execution_id") != intent.execution_id:
            raise TransportError("Manager replied for a different execution identity.")
        return result

    async def control(
        self,
        action: Literal["stop", "cleanup", "status"],
        intent: CommandIntent,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        self.require_intent(intent)
        if action not in {"stop", "cleanup", "status"}:
            raise ValueError("Unsupported execution control action.")
        raw = await request_process(
            self.invocation,
            json.dumps({"action": action, "execution_id": intent.execution_id}).encode(),
            timeout=min(15, deadline - time.monotonic()),
            output_limit=16384,
        )
        result = response_object(raw)
        if result.get("execution_id") != intent.execution_id:
            raise TransportError("Manager replied for a different execution identity.")
        return result

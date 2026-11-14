"""Application-selected WSL resources and structured, bounded manager IPC."""

import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import EnvironmentInUseError, TransportError, request_process
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot

RESOURCE_NAMES = (
    "wsl_manager.py",
    "workspace_snapshot.py",
    "sandbox_profile.py",
    "execution_receipts.py",
    "cgroup_gate.py",
    "procedure_environment_manager.py",
    "node_environment_manager.py",
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
        return cls(distribution, digest, (*prefix, "/usr/bin/python3", "-I", *paths))

    def require_intent(self, intent: CommandIntent) -> None:
        if intent.distribution != self.distribution or intent.profile_sha256 != self.profile_sha256:
            raise PermissionError("Execution grant does not match the selected WSL profile.")
        if profile_fingerprint()[0] != self.profile_sha256:
            raise PermissionError("Execution profile changed; obtain a fresh explicit grant.")

    async def run(
        self,
        intent: CommandIntent,
        snapshot: WorkspaceSnapshot,
        *,
        deadline: float,
        on_output: Callable[[str, bytes], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        self.require_intent(intent)
        remaining = deadline - time.monotonic()
        if remaining < intent.max_seconds:
            raise TimeoutError("Command duration exceeds the remaining parent time budget.")
        request: dict[str, Any] = {
            "action": "run",
            "execution_id": intent.execution_id,
            "argv": intent.argv,
            "max_seconds": intent.max_seconds,
            "snapshot_b64": base64.b64encode(snapshot.to_bytes()).decode("ascii"),
        }
        if intent.procedure_environment_digest is not None:
            request["procedure_environment_digest"] = intent.procedure_environment_digest
            request["procedure_environment_tree_sha256"] = intent.procedure_environment_tree_sha256
        if intent.procedure_node_environment_digest is not None:
            request["procedure_node_environment_digest"] = (
                intent.procedure_node_environment_digest
            )
            request["procedure_node_environment_tree_sha256"] = (
                intent.procedure_node_environment_tree_sha256
            )
            request["procedure_skill_id"] = intent.procedure_skill_id
            request["procedure_version"] = intent.procedure_version
        payload = json.dumps(request).encode("utf-8")
        if len(payload) > 18 * 1024 * 1024:
            raise ValueError("Command request exceeds the manager transport limit.")

        async def frame(raw_frame: bytes) -> None:
            message = response_object(raw_frame)
            if message.get("type") == "output" and set(message) == {"type", "stream", "chunk_b64"}:
                stream, encoded = message["stream"], message["chunk_b64"]
                if (
                    stream not in {"stdout", "stderr"}
                    or not isinstance(encoded, str)
                    or len(encoded) > 21848
                ):
                    raise TransportError("Manager sent an invalid output frame.")
                try:
                    chunk = base64.b64decode(encoded, validate=True)
                except ValueError as exc:
                    raise TransportError("Manager sent malformed output bytes.") from exc
                if not chunk or len(chunk) > 16384:
                    raise TransportError("Manager output frame exceeded its bound.")
                streamed_bytes[0] += len(chunk)
                if streamed_bytes[0] > 1024 * 1024:
                    raise TransportError("Manager exceeded the combined output limit.")
                if on_output is not None:
                    await on_output(stream, chunk)
                return
            if message.get("type") == "result" and set(message) == {"type", "result"}:
                frames.append(message["result"])
                if len(frames) != 1:
                    raise TransportError("Manager sent duplicate result frames.")
                return
            raise TransportError("Manager sent an unknown output frame.")

        frames: list[Any] = []
        streamed_bytes = [0]
        raw = await request_process(
            self.invocation,
            payload,
            timeout=deadline - time.monotonic(),
            output_limit=2 * 1024 * 1024,
            on_stdout_line=frame,
        )
        if raw:
            raise TransportError("Manager returned unframed output.")
        if len(frames) != 1 or not isinstance(frames[0], dict):
            raise TransportError("Manager omitted its final execution receipt.")
        result = cast(dict[str, Any], frames[0])
        if result.get("execution_id") != intent.execution_id:
            raise TransportError("Manager replied for a different execution identity.")
        if (
            result.get("procedure_node_environment_digest")
            != intent.procedure_node_environment_digest
            or result.get("procedure_node_environment_tree_sha256")
            != intent.procedure_node_environment_tree_sha256
            or result.get("procedure_skill_id") != intent.procedure_skill_id
            or result.get("procedure_version") != intent.procedure_version
        ):
            raise TransportError("Manager result does not match the selected Node environment.")
        if (
            result.get("procedure_environment_digest") != intent.procedure_environment_digest
            or result.get("procedure_environment_tree_sha256")
            != intent.procedure_environment_tree_sha256
        ):
            raise TransportError("Manager result does not match the selected Python environment.")
        return result

    async def control(
        self,
        action: Literal["stop", "cleanup", "status"],
        intent: CommandIntent,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        if action not in {"stop", "cleanup", "status"}:
            raise ValueError("Unsupported execution control action.")
        # A saved execution may outlive the packaged profile that launched it.
        # Control addresses only its immutable identity in the original WSL
        # distribution; profile equality remains mandatory for every launch.
        if intent.distribution != self.distribution:
            raise PermissionError("Execution control must use the saved WSL distribution.")
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

    async def install_procedure_environment(
        self,
        lock: Any,
        wheelhouse_path: Path,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        """Install an already owner-approved immutable lock using the root WSL manager."""
        from xaa.extensions.procedure_dependencies import ProcedurePythonDependencyLock

        if not isinstance(lock, ProcedurePythonDependencyLock):
            raise TypeError("Install request must contain a validated dependency lock.")
        if lock.target.distribution != self.distribution:
            raise PermissionError("Procedure lock targets a different WSL distribution.")
        if profile_fingerprint()[0] != self.profile_sha256:
            raise PermissionError("WSL manager profile changed; rediscover before installation.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Procedure environment installation deadline expired.")
        windows_path = wheelhouse_path.resolve(strict=True)
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
        prefix = (
            str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe"),
            "--distribution",
            self.distribution,
            "--user",
            "root",
            "--exec",
        )
        mapped_raw = await request_process(
            (*prefix, "/usr/bin/python3", "-I", "-c", mapper),
            json.dumps([str(windows_path)]).encode("utf-8"),
            timeout=min(20, deadline - time.monotonic()),
            output_limit=8192,
        )
        try:
            mapped_value: object = json.loads(mapped_raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise TransportError("WSL returned invalid wheelhouse path data.") from exc
        if not isinstance(mapped_value, list):
            raise TransportError("WSL returned an invalid wheelhouse path.")
        mapped_paths = cast(list[object], mapped_value)
        if (
            len(mapped_paths) != 1
            or not isinstance(mapped_paths[0], str)
            or not mapped_paths[0].startswith("/mnt/")
        ):
            raise TransportError("WSL returned an invalid wheelhouse path.")
        payload = json.dumps(
            {
                "action": "install",
                "environment_digest": lock.environment_digest(),
                "lock_json": lock.canonical_json(),
                "wheelhouse_path": mapped_paths[0],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > 512 * 1024:
            raise ValueError("Procedure dependency install request exceeds its transport limit.")
        raw = await request_process(
            self.invocation,
            payload,
            timeout=deadline - time.monotonic(),
            output_limit=16384,
        )
        result = response_object(raw)
        if (
            result.get("environment_digest") != lock.environment_digest()
            or result.get("lock_sha256")
            != hashlib.sha256(lock.canonical_json().encode("ascii")).hexdigest()
            or result.get("installed") is not True
            or not isinstance(result.get("tree_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", cast(str, result.get("tree_sha256")))
        ):
            error_code = result.get("error_code")
            if isinstance(error_code, str):
                detail = result.get("error_detail")
                if isinstance(detail, str) and len(detail) <= 240:
                    raise TransportError(
                        "WSL procedure environment installation failed ("
                        + error_code
                        + "): "
                        + detail
                    )
                raise TransportError("WSL procedure environment installation failed: " + error_code)
            raise TransportError("WSL did not confirm the exact immutable procedure environment.")
        return result

    async def install_node_environment(
        self,
        lock: Any,
        package_manifest: bytes,
        package_lock: bytes,
        tarball_directory: Path,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        """Install exact owner-approved npm tarballs offline in managed WSL storage."""
        from xaa.extensions.npm_dependencies import ProcedureNpmDependencyLock

        if not isinstance(lock, ProcedureNpmDependencyLock):
            raise TypeError("Install request must contain a validated npm dependency lock.")
        if lock.distribution != self.distribution:
            raise PermissionError("npm dependency lock targets a different WSL distribution.")
        if (
            hashlib.sha256(package_manifest).hexdigest() != lock.package_manifest_sha256
            or hashlib.sha256(package_lock).hexdigest() != lock.lockfile_sha256
        ):
            raise ValueError("Linked npm resources do not match the validated dependency lock.")
        if profile_fingerprint()[0] != self.profile_sha256:
            raise PermissionError("WSL manager profile changed; rediscover before installation.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Node environment installation deadline expired.")
        directory = tarball_directory.resolve(strict=True)
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
        prefix = (
            str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe"),
            "--distribution",
            self.distribution,
            "--user",
            "root",
            "--exec",
        )
        mapped_raw = await request_process(
            (*prefix, "/usr/bin/python3", "-I", "-c", mapper),
            json.dumps([str(directory)]).encode("utf-8"),
            timeout=min(20, deadline - time.monotonic()),
            output_limit=8192,
        )
        try:
            mapped: object = json.loads(mapped_raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise TransportError("WSL returned invalid npm tarball path data.") from exc
        if (
            not isinstance(mapped, list)
            or len(cast(list[object], mapped)) != 1
            or not isinstance(cast(list[object], mapped)[0], str)
            or not cast(list[str], mapped)[0].startswith("/mnt/")
        ):
            raise TransportError("WSL returned an invalid npm tarball path.")
        payload = json.dumps(
            {
                "action": "install_node",
                "environment_digest": lock.environment_digest(),
                "lock_json": lock.canonical_json(),
                "package_manifest_b64": base64.b64encode(package_manifest).decode("ascii"),
                "package_lock_b64": base64.b64encode(package_lock).decode("ascii"),
                "tarball_path": cast(list[str], mapped)[0],
            },
            separators=(",", ":"),
        ).encode("utf-8")
        if len(payload) > 18 * 1024 * 1024:
            raise ValueError("npm environment install request exceeds its transport limit.")
        raw = await request_process(
            self.invocation, payload, timeout=deadline - time.monotonic(), output_limit=16384
        )
        result = response_object(raw)
        if (
            result.get("environment_digest") != lock.environment_digest()
            or result.get("lock_sha256") != lock.environment_digest()
            or result.get("installed") is not True
            or not isinstance(result.get("tree_sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", cast(str, result.get("tree_sha256"))) is None
        ):
            code, detail = result.get("error_code"), result.get("error_detail")
            if isinstance(code, str):
                suffix = f": {detail}" if isinstance(detail, str) and len(detail) <= 240 else ""
                raise TransportError(f"WSL npm environment installation failed ({code}){suffix}")
            raise TransportError("WSL did not confirm the exact immutable npm environment.")
        return result

    async def node_environment_status(
        self, environment_digest: str, *, deadline: float
    ) -> dict[str, Any]:
        return await self._node_environment_control(
            "status_node", environment_digest, deadline=deadline
        )

    async def remove_node_environment(
        self, environment_digest: str, *, deadline: float
    ) -> dict[str, Any]:
        result = await self._node_environment_control(
            "remove_node", environment_digest, deadline=deadline
        )
        if result.get("removed") is not True:
            raise TransportError("WSL did not confirm exact npm environment removal.")
        return result

    async def _node_environment_control(
        self, action: str, environment_digest: str, *, deadline: float
    ) -> dict[str, Any]:
        if (
            action not in {"status_node", "remove_node"}
            or re.fullmatch(r"[a-f0-9]{64}", environment_digest) is None
        ):
            raise ValueError("Node environment identity or action is invalid.")
        if profile_fingerprint()[0] != self.profile_sha256:
            raise PermissionError(
                "WSL manager profile changed; rediscover before environment control."
            )
        raw = await request_process(
            self.invocation,
            json.dumps(
                {"action": action, "environment_digest": environment_digest}, separators=(",", ":")
            ).encode("ascii"),
            timeout=deadline - time.monotonic(),
            output_limit=8192,
        )
        result = response_object(raw)
        if isinstance(result.get("error_code"), str):
            if result["error_code"] == "environment_in_use":
                raise EnvironmentInUseError("A running procedure is using this Node environment.")
            raise TransportError("WSL Node environment operation failed.")
        if result.get("environment_digest") != environment_digest:
            raise TransportError("WSL returned a different npm environment identity.")
        if action == "status_node" and type(result.get("installed")) is not bool:
            raise TransportError("WSL returned an invalid npm environment status.")
        return result

    async def procedure_environment_status(
        self, environment_digest: str, *, deadline: float
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{64}", environment_digest):
            raise ValueError("Procedure environment identity is invalid.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Procedure environment status deadline expired.")
        raw = await request_process(
            self.invocation,
            json.dumps(
                {"action": "status", "environment_digest": environment_digest},
                separators=(",", ":"),
            ).encode("ascii"),
            timeout=deadline - time.monotonic(),
            output_limit=8192,
        )
        result = response_object(raw)
        if isinstance(result.get("error_code"), str):
            raise TransportError("WSL procedure environment status failed.")
        if (
            result.get("environment_digest") != environment_digest
            or type(result.get("installed")) is not bool
        ):
            raise TransportError("WSL returned an invalid procedure environment status.")
        return result

    async def remove_procedure_environment(
        self, environment_digest: str, *, deadline: float
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{64}", environment_digest):
            raise ValueError("Procedure environment identity is invalid.")
        if time.monotonic() >= deadline:
            raise TimeoutError("Procedure environment removal deadline expired.")
        raw = await request_process(
            self.invocation,
            json.dumps(
                {"action": "remove", "environment_digest": environment_digest},
                separators=(",", ":"),
            ).encode("ascii"),
            timeout=deadline - time.monotonic(),
            output_limit=8192,
        )
        result = response_object(raw)
        if isinstance(result.get("error_code"), str):
            if result["error_code"] == "environment_in_use":
                raise EnvironmentInUseError("A running procedure is using this environment.")
            raise TransportError("WSL procedure environment removal failed.")
        if (
            result.get("environment_digest") != environment_digest
            or result.get("removed") is not True
        ):
            raise TransportError("WSL did not confirm exact procedure environment removal.")
        return result

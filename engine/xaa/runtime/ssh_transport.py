"""OpenSSH transport to a preinstalled X.A.A. restricted execution manager."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import request_process
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport, profile_fingerprint

SSH_BACKEND = "ssh-wsl-bubblewrap-v1"
REMOTE_MANAGER_COMMAND = "xaa-ssh-execution-manager"
_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_USER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")
_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,252}$|^[0-9A-Fa-f:]+$")


def _effective_options(raw: bytes) -> dict[str, list[str]]:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ValueError("OpenSSH returned invalid profile data.") from exc
    options: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, separator, value = line.partition(" ")
        if not separator or not key or not value:
            raise ValueError("OpenSSH returned malformed profile data.")
        options.setdefault(key.casefold(), []).append(value.strip())
    return options


def _one(options: dict[str, list[str]], key: str) -> str:
    values = options.get(key, [])
    if len(values) != 1:
        raise ValueError(f"SSH profile must resolve exactly one {key} value.")
    return values[0]


def _optional(options: dict[str, list[str]], key: str, default: str = "none") -> str:
    values = options.get(key, [])
    if not values:
        return default
    if len(values) != 1:
        raise ValueError(f"SSH profile must resolve at most one {key} value.")
    return values[0]


def _profile_digest(
    *,
    alias: str,
    manager_sha256: str,
    effective: bytes,
    known_hosts: bytes,
    host_pin: bytes,
    identity_path: str,
    identity_fingerprint: str,
) -> str:
    record = {
        "backend": SSH_BACKEND,
        "alias": alias,
        "manager_sha256": manager_sha256,
        "effective_ssh_options_sha256": hashlib.sha256(effective).hexdigest(),
        "known_hosts_sha256": hashlib.sha256(known_hosts).hexdigest(),
        "host_pin_sha256": hashlib.sha256(host_pin).hexdigest(),
        "identity_path": identity_path,
        "identity_fingerprint": identity_fingerprint,
        "remote_command": REMOTE_MANAGER_COMMAND,
        "strict_host_key_checking": True,
        "forwarding": False,
    }
    return hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class SSHTransport:
    """Run the same bounded manager protocol through a pinned OpenSSH identity."""

    distribution: str
    profile_sha256: str
    manager_profile_sha256: str
    identity_file: Path
    ssh_alias: str
    invocation: tuple[str, ...]
    effective_options: bytes
    known_hosts_path: Path
    known_hosts_bytes: bytes
    host_pin: bytes
    identity_fingerprint: str

    @classmethod
    async def discover(
        cls,
        alias: str,
        identity_file: str,
        *,
        deadline: float,
    ) -> SSHTransport:
        if os.name != "nt":
            raise ValueError("SSH execution profiles are available on Windows only.")
        if _ALIAS.fullmatch(alias) is None:
            raise ValueError("Enter a simple OpenSSH host alias.")
        manager_sha256, _ = profile_fingerprint()
        profile_value = os.environ.get("USERPROFILE")
        if not profile_value:
            raise ValueError("The Windows user profile is unavailable.")
        profile_root = Path(profile_value).resolve(strict=True)
        ssh_directory = profile_root / ".ssh"
        identity = Path(identity_file).resolve(strict=True)
        if not identity.is_file() or not identity.is_relative_to(
            ssh_directory.resolve(strict=True)
        ):
            raise PermissionError("Choose a private key file under the current user's .ssh folder.")
        if identity.stat().st_size > 128 * 1024:
            raise ValueError("SSH private key file exceeds 128 KiB.")
        public_key = identity.with_name(identity.name + ".pub")
        if not public_key.is_file():
            raise ValueError("The selected SSH key needs its matching .pub file.")
        known_hosts_path = ssh_directory / "known_hosts"
        known_hosts_bytes = known_hosts_path.read_bytes()
        config_path = ssh_directory / "config"
        config_bytes = config_path.read_bytes()
        if len(config_bytes) > 64 * 1024:
            raise ValueError("The OpenSSH config exceeds 64 KiB.")
        try:
            config_text = config_bytes.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError("The OpenSSH config must use UTF-8 text.") from exc
        for line in config_text.splitlines():
            directive = line.split("#", maxsplit=1)[0].strip()
            if re.match(r"(?i)^include(?:\s|$)", directive) or re.match(
                r"(?i)^match\b.*\bexec\b", directive
            ):
                raise ValueError(
                    "SSH config Include and Match exec directives are unsupported for sandbox setup."
                )
        ssh = shutil.which("ssh.exe") or shutil.which("ssh")
        ssh_keygen = shutil.which("ssh-keygen.exe") or shutil.which("ssh-keygen")
        if ssh is None or ssh_keygen is None:
            raise FileNotFoundError("Install the Windows OpenSSH client and try again.")

        effective = await request_process(
            (ssh, "-G", "-F", str(config_path), alias),
            b"",
            timeout=min(5, deadline - time.monotonic()),
            output_limit=64 * 1024,
        )
        options = _effective_options(effective)
        hostname = _one(options, "hostname")
        username = _one(options, "user")
        port_text = _one(options, "port")
        host_key_alias = _optional(options, "hostkeyalias")
        proxy_command = _optional(options, "proxycommand")
        proxy_jump = _optional(options, "proxyjump")
        remote_command = _optional(options, "remotecommand")
        if (
            _HOST.fullmatch(hostname) is None
            or _USER.fullmatch(username) is None
            or not port_text.isdecimal()
            or not 1 <= int(port_text) <= 65535
            or host_key_alias not in {"none", ""}
            or proxy_command not in {"none", ""}
            or proxy_jump not in {"none", ""}
            or remote_command not in {"none", ""}
        ):
            raise ValueError(
                "SSH aliases with proxy commands, host-key aliases, or remote commands are unsupported."
            )
        port = int(port_text)
        known_name = hostname if port == 22 else f"[{hostname}]:{port}"
        pin = await request_process(
            (ssh_keygen, "-F", known_name, "-f", str(known_hosts_path)),
            b"",
            timeout=min(5, deadline - time.monotonic()),
            output_limit=64 * 1024,
        )
        if not pin.strip():
            raise PermissionError("The SSH server host key is not pinned in .ssh\\known_hosts.")
        fingerprint_result = await request_process(
            (ssh_keygen, "-lf", str(public_key)),
            b"",
            timeout=min(5, deadline - time.monotonic()),
            output_limit=4096,
        )
        fingerprint_parts = fingerprint_result.decode("utf-8", errors="strict").split()
        if len(fingerprint_parts) < 2 or not fingerprint_parts[1].startswith("SHA256:"):
            raise ValueError("OpenSSH could not verify the selected public-key fingerprint.")
        identity_fingerprint = fingerprint_parts[1]
        invocation = (
            ssh,
            "-F",
            "none",
            "-T",
            "-p",
            str(port),
            "-i",
            str(identity),
            "-oBatchMode=yes",
            "-oIdentitiesOnly=yes",
            "-oStrictHostKeyChecking=yes",
            f"-oUserKnownHostsFile={known_hosts_path}",
            "-oGlobalKnownHostsFile=none",
            "-oClearAllForwardings=yes",
            "-oForwardAgent=no",
            "-oRequestTTY=no",
            "-oControlMaster=no",
            "-oConnectTimeout=10",
            f"{username}@{hostname}",
            REMOTE_MANAGER_COMMAND,
        )
        digest = _profile_digest(
            alias=alias,
            manager_sha256=manager_sha256,
            effective=effective,
            known_hosts=known_hosts_bytes,
            host_pin=pin,
            identity_path=str(identity),
            identity_fingerprint=identity_fingerprint,
        )
        return cls(
            distribution=alias,
            profile_sha256=digest,
            manager_profile_sha256=manager_sha256,
            identity_file=identity,
            ssh_alias=alias,
            invocation=invocation,
            effective_options=effective,
            known_hosts_path=known_hosts_path,
            known_hosts_bytes=known_hosts_bytes,
            host_pin=pin,
            identity_fingerprint=identity_fingerprint,
        )

    def require_intent(self, intent: CommandIntent) -> None:
        if (
            intent.backend != SSH_BACKEND
            or intent.distribution != self.distribution
            or intent.profile_sha256 != self.profile_sha256
            or intent.ssh_identity_file != str(self.identity_file)
        ):
            raise PermissionError("Saved command grant does not match the pinned SSH profile.")
        if profile_fingerprint()[0] != self.manager_profile_sha256:
            raise PermissionError("The local SSH sandbox-manager profile changed; recheck it.")

    async def _verify_current_profile(self, intent: CommandIntent, deadline: float) -> None:
        fresh = await SSHTransport.discover(
            self.ssh_alias, str(self.identity_file), deadline=deadline
        )
        if fresh.profile_sha256 != intent.profile_sha256:
            raise PermissionError(
                "SSH identity or pinned host profile changed; recheck the sandbox."
            )

    def _manager_transport(self) -> WSLTransport:
        return WSLTransport(
            distribution=self.distribution,
            profile_sha256=self.manager_profile_sha256,
            invocation=self.invocation,
        )

    def _manager_intent(self, intent: CommandIntent) -> CommandIntent:
        return intent.model_copy(
            update={
                "backend": "wsl-bubblewrap-v1",
                "profile_sha256": self.manager_profile_sha256,
            }
        )

    async def run(
        self,
        intent: CommandIntent,
        snapshot: WorkspaceSnapshot,
        *,
        deadline: float,
        on_output: Any = None,
        procedure_secrets: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.require_intent(intent)
        await self._verify_current_profile(intent, deadline)
        result = await self._manager_transport().run(
            self._manager_intent(intent),
            snapshot,
            deadline=deadline,
            on_output=on_output,
            procedure_secrets=procedure_secrets,
        )
        result["backend"] = SSH_BACKEND
        return result

    async def control(
        self,
        action: Literal["stop", "cleanup", "status"],
        intent: CommandIntent,
        *,
        deadline: float,
    ) -> dict[str, Any]:
        self.require_intent(intent)
        await self._verify_current_profile(intent, deadline)
        return await self._manager_transport().control(
            action, self._manager_intent(intent), deadline=deadline
        )

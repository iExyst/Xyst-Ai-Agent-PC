from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from xaa.runtime import execution_readiness as readiness_module
from xaa.runtime import ssh_transport
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.execution_readiness import ExecutionReadiness, ReadinessRequest


@pytest.mark.skipif(os.name != "nt", reason="SSH execution profile is Windows-first")
@pytest.mark.parametrize("manager_matches", [True, False])
async def test_ssh_discovery_pins_identity_and_builds_fixed_restricted_invocation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, manager_matches: bool
) -> None:
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir()
    identity = ssh_directory / "id_ed25519"
    identity.write_bytes(b"fixture private key placeholder")
    identity.with_name(identity.name + ".pub").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAfixture xaa-test\n", encoding="utf-8"
    )
    known_hosts = ssh_directory / "known_hosts"
    pinned_key = b"[127.0.0.1]:22222 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAhost\n"
    known_hosts.write_bytes(pinned_key)
    (ssh_directory / "config").write_text(
        "Host xaa-test\n  HostName 127.0.0.1\n  User xaa-test\n  Port 22222\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(ssh_transport.shutil, "which", lambda _: "C:/OpenSSH/ssh.exe")

    options = b"""hostname 127.0.0.1
user xaa-test
port 22222
hostkeyalias none
proxycommand none
proxyjump none
remotecommand none
"""

    async def fake_request(command, payload, *, timeout, output_limit, on_stdout_line=None):
        del timeout, output_limit, on_stdout_line
        if "-G" in command:
            assert command[command.index("-F") + 1] == str(ssh_directory / "config")
            return options
        if "-F" in command and command[-1] == str(known_hosts):
            assert command[-1] == str(known_hosts)
            return b"# Host [127.0.0.1]:22222 found: line 1\n" + pinned_key
        if "-lf" in command:
            return b"256 SHA256:identity-fingerprint fixture (ED25519)\n"
        if ssh_transport.REMOTE_MANAGER_COMMAND in command:
            challenge = json.loads(payload)
            digest, resources = ssh_transport.profile_fingerprint()
            hashes = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in resources
            }
            if not manager_matches:
                digest = "0" * 64
            return json.dumps(
                {
                    "protocol_version": 1,
                    "nonce": challenge["nonce"],
                    "manager_sha256": digest,
                    "resource_sha256": hashes,
                }
            ).encode()
        raise AssertionError(f"Unexpected subprocess: {command!r}")

    monkeypatch.setattr(ssh_transport, "request_process", fake_request)
    if not manager_matches:
        with pytest.raises(PermissionError, match="remote X.A.A. manager does not match"):
            await ssh_transport.SSHTransport.discover("xaa-test", str(identity), deadline=10**12)
        return
    transport = await ssh_transport.SSHTransport.discover(
        "xaa-test", str(identity), deadline=10**12
    )

    assert transport.profile_sha256 != transport.manager_profile_sha256
    assert transport.invocation[-2:] == (
        "xaa-test@127.0.0.1",
        ssh_transport.REMOTE_MANAGER_COMMAND,
    )
    assert "-oStrictHostKeyChecking=yes" in transport.invocation
    assert "-oClearAllForwardings=yes" in transport.invocation
    assert "-oForwardAgent=no" in transport.invocation
    assert "-oRequestTTY=no" in transport.invocation
    assert "-oControlMaster=no" in transport.invocation
    assert transport.identity_fingerprint == "SHA256:identity-fingerprint"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proxycommand", "ssh attacker@host"),
        ("proxyjump", "attacker"),
        ("remotecommand", "id"),
        ("hostkeyalias", "unrelated-host"),
    ],
)
@pytest.mark.skipif(os.name != "nt", reason="SSH execution profile is Windows-first")
async def test_ssh_profile_refuses_ambiguous_or_relayed_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir()
    identity = ssh_directory / "id_ed25519"
    identity.write_bytes(b"fixture private key placeholder")
    identity.with_name(identity.name + ".pub").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAfixture xaa-test\n", encoding="utf-8"
    )
    (ssh_directory / "known_hosts").write_text(
        "[127.0.0.1]:22222 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAhost\n", encoding="utf-8"
    )
    (ssh_directory / "config").write_text(
        "Host xaa-test\n  HostName 127.0.0.1\n  User xaa-test\n  Port 22222\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(ssh_transport.shutil, "which", lambda _: "C:/OpenSSH/ssh.exe")
    options = {
        "hostname": "127.0.0.1",
        "user": "xaa-test",
        "port": "22222",
        "hostkeyalias": "none",
        "proxycommand": "none",
        "proxyjump": "none",
        "remotecommand": "none",
    }
    options[field] = value

    async def fake_request(command, payload, *, timeout, output_limit, on_stdout_line=None):
        del payload, timeout, output_limit, on_stdout_line
        if "-G" in command:
            return "".join(f"{key} {item}\n" for key, item in options.items()).encode()
        raise AssertionError("Unsafe SSH profile was not rejected before key lookup")

    monkeypatch.setattr(ssh_transport, "request_process", fake_request)
    with pytest.raises(ValueError, match="SSH aliases"):
        await ssh_transport.SSHTransport.discover("xaa-test", str(identity), deadline=10**12)


@pytest.mark.skipif(os.name != "nt", reason="SSH execution profile is Windows-first")
@pytest.mark.parametrize("directive", ["Include other.conf", 'Match exec "whoami"'])
async def test_ssh_profile_rejects_config_directives_that_execute_or_extend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, directive: str
) -> None:
    ssh_directory = tmp_path / ".ssh"
    ssh_directory.mkdir()
    identity = ssh_directory / "id_ed25519"
    identity.write_bytes(b"fixture private key placeholder")
    identity.with_name(identity.name + ".pub").write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAfixture xaa-test\n", encoding="utf-8"
    )
    (ssh_directory / "known_hosts").write_text(
        "[127.0.0.1]:22222 ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAhost\n", encoding="utf-8"
    )
    (ssh_directory / "config").write_text(directive + "\n", encoding="utf-8")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(ssh_transport.shutil, "which", lambda _: "C:/OpenSSH/ssh.exe")

    async def unexpected_request(*args, **kwargs):
        raise AssertionError("Unsafe SSH config reached OpenSSH discovery")

    monkeypatch.setattr(ssh_transport, "request_process", unexpected_request)
    with pytest.raises(ValueError, match="Include and Match exec"):
        await ssh_transport.SSHTransport.discover("xaa-test", str(identity), deadline=10**12)


def test_ssh_command_authority_requires_exact_identity_file() -> None:
    with pytest.raises(ValueError, match="require"):
        CommandGrant(
            backend=ssh_transport.SSH_BACKEND,
            distribution="xaa-host",
            profile_sha256="a" * 64,
        )
    grant = CommandGrant(
        backend=ssh_transport.SSH_BACKEND,
        distribution="xaa-host",
        profile_sha256="a" * 64,
        ssh_identity_file=r"C:\Users\Test\.ssh\id_ed25519",
    )
    intent = CommandIntent(
        argv=["/usr/bin/id"],
        execution_id="a" * 32,
        backend=grant.backend,
        distribution=grant.distribution,
        profile_sha256=grant.profile_sha256,
        ssh_identity_file=grant.ssh_identity_file,
        workspace="C:\\fixture",
    )
    assert intent.ssh_identity_file == grant.ssh_identity_file
    with pytest.raises(ValueError, match="identity file can be selected only for SSH readiness"):
        ReadinessRequest(
            backend="wsl-bubblewrap-v1",
            distribution="Ubuntu-24.04",
            ssh_identity_file=grant.ssh_identity_file,
        )
    assert (
        ReadinessRequest(
            backend=ssh_transport.SSH_BACKEND,
            distribution="xaa-host",
            ssh_identity_file=grant.ssh_identity_file,
        ).ssh_identity_file
        == grant.ssh_identity_file
    )


async def test_ssh_readiness_requires_remote_probe_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Backend:
        profile_sha256 = "a" * 64

        def __init__(self) -> None:
            self.controls: list[str] = []

        async def run(self, intent, snapshot, *, deadline):
            del intent, deadline
            import base64
            import json

            nonce = snapshot.files[0].content.decode()
            return {
                "backend": ssh_transport.SSH_BACKEND,
                "succeeded": True,
                "cleanup_confirmed": True,
                "exit_code": 0,
                "snapshot_sha256": snapshot.sha256,
                "output_complete": True,
                "workspace_changes_persisted": False,
                "limits": {
                    "max_active_executions": 2,
                    "memory_bytes": 134217728,
                    "cpu_percent": 50,
                    "tasks": 32,
                    "scratch_bytes": 33554432,
                    "workspace_bytes": 33554432,
                    "output_bytes": 1048576,
                    "seconds": 5,
                    "network": "none",
                },
                "stdout_b64": base64.b64encode(
                    json.dumps({"nonce": nonce, "checks": True}).encode()
                ).decode(),
            }

        async def control(self, action, intent, *, deadline):
            del intent, deadline
            self.controls.append(action)
            return {
                "cleanup_confirmed": True,
                "closed": True,
                "manager_alive": False,
            }

    backend = Backend()
    monkeypatch.setattr(readiness_module, "SUPPORTED", True)

    async def discover(alias, identity_file, *, deadline):
        del deadline
        assert alias == "xaa-host"
        assert identity_file == r"C:\Users\Test\.ssh\id_ed25519"
        return backend

    monkeypatch.setattr(readiness_module.SSHTransport, "discover", discover)
    service = ExecutionReadiness()
    result = await service.check(
        ReadinessRequest(
            backend=ssh_transport.SSH_BACKEND,
            distribution="xaa-host",
            ssh_identity_file=r"C:\Users\Test\.ssh\id_ed25519",
        )
    )

    assert result.state == "ready"
    assert result.checks == list(readiness_module.SSH_CHECKS)
    assert backend.controls == ["cleanup", "status"]
    service.require_grant(
        CommandGrant(
            backend=ssh_transport.SSH_BACKEND,
            distribution="xaa-host",
            profile_sha256=backend.profile_sha256,
            ssh_identity_file=r"C:\Users\Test\.ssh\id_ed25519",
        )
    )

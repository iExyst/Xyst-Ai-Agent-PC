import time

import pytest
from xaa.runtime import wsl_transport
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import TransportError
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot


@pytest.mark.parametrize("raw", [b"[]", b'{"x":1,"x":2}', b"not-json", b'"value"'])
def test_invalid_manager_protocol_is_not_authoritative(raw):
    with pytest.raises(TransportError):
        wsl_transport.response_object(raw)


@pytest.mark.parametrize("problem", ["grant", "resources", "deadline"])
async def test_prelaunch_checks_never_invoke_manager(problem, monkeypatch):
    digest, _ = wsl_transport.profile_fingerprint()
    transport = wsl_transport.WSLTransport("Ubuntu", digest, ("must-not-launch",))
    intent = CommandIntent(
        argv=["/usr/bin/true"],
        max_seconds=10,
        execution_id="a" * 32,
        backend="wsl-bubblewrap-v1",
        distribution="Other" if problem == "grant" else "Ubuntu",
        profile_sha256=digest,
        workspace="fixture",
    )
    if problem == "resources":
        monkeypatch.setattr(wsl_transport, "profile_fingerprint", lambda: ("f" * 64, ()))

    async def forbidden(*args, **kwargs):
        pytest.fail("Prelaunch denial must not start the remote manager")

    monkeypatch.setattr(wsl_transport, "request_process", forbidden)
    with pytest.raises(TimeoutError if problem == "deadline" else PermissionError):
        await transport.run(
            intent,
            WorkspaceSnapshot((), (), ()),
            deadline=time.monotonic() + (0.1 if problem == "deadline" else 30),
        )

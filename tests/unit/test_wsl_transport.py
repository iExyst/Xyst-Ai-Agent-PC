import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from xaa.runtime import wsl_transport
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import TransportError
from xaa.runtime.workspace_snapshot import WorkspaceSnapshot


async def test_discovery_maps_all_worker_resources_with_one_bounded_wsl_launch(monkeypatch):
    resources = tuple(Path(f"C:/engine/resource-{index}.py") for index in range(5))
    digest = "a" * 64
    monkeypatch.setattr(wsl_transport, "profile_fingerprint", lambda: (digest, resources))
    monkeypatch.setattr(
        wsl_transport,
        "os",
        SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"}),
    )
    calls = []

    async def request_process(command, payload, *, timeout, output_limit):
        calls.append((command, json.loads(payload), timeout, output_limit))
        return json.dumps([f"/mnt/c/engine/resource-{index}.py" for index in range(5)]).encode()

    monkeypatch.setattr(wsl_transport, "request_process", request_process)
    transport = await wsl_transport.WSLTransport.discover(
        "Ubuntu-24.04", deadline=time.monotonic() + 60
    )

    assert len(calls) == 1
    command, payload, timeout, output_limit = calls[0]
    assert command[:7] == (
        r"C:\Windows\System32\wsl.exe",
        "--distribution",
        "Ubuntu-24.04",
        "--user",
        "root",
        "--exec",
        "/usr/bin/python3",
    )
    assert payload == [str(path) for path in resources]
    assert 0 < timeout <= 30 and output_limit == 8192
    assert transport.invocation[-4:] == tuple(
        f"/mnt/c/engine/resource-{index}.py" for index in range(4)
    )
    assert transport.profile_sha256 == digest


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'["/valid"]', b'["relative"]'])
async def test_discovery_rejects_invalid_or_incomplete_resource_maps(raw, monkeypatch):
    resources = tuple(Path(f"C:/engine/resource-{index}.py") for index in range(5))
    digest = "a" * 64
    monkeypatch.setattr(wsl_transport, "profile_fingerprint", lambda: (digest, resources))
    monkeypatch.setattr(
        wsl_transport,
        "os",
        SimpleNamespace(name="nt", environ={"SystemRoot": r"C:\Windows"}),
    )

    async def request_process(*args, **kwargs):
        return raw

    monkeypatch.setattr(wsl_transport, "request_process", request_process)
    with pytest.raises(TransportError):
        await wsl_transport.WSLTransport.discover("Ubuntu-24.04", deadline=time.monotonic() + 60)


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


async def test_run_streams_bounded_output_frames_and_requires_one_receipt(monkeypatch):
    digest, _ = wsl_transport.profile_fingerprint()
    intent = CommandIntent(
        argv=["/usr/bin/printf", "ok"],
        max_seconds=10,
        execution_id="a" * 32,
        backend="wsl-bubblewrap-v1",
        distribution="Ubuntu",
        profile_sha256=digest,
        workspace="fixture",
    )
    transport = wsl_transport.WSLTransport("Ubuntu", digest, ("fixture-manager",))
    snapshot = WorkspaceSnapshot((), (), ())
    seen = []

    async def request_process(command, payload, *, timeout, output_limit, on_stdout_line=None):
        assert command == transport.invocation
        assert output_limit == 2 * 1024 * 1024
        assert on_stdout_line is not None
        await on_stdout_line(
            json.dumps({"type": "output", "stream": "stdout", "chunk_b64": "aMOp"}).encode()
        )
        await on_stdout_line(
            json.dumps({"type": "output", "stream": "stderr", "chunk_b64": "eA=="}).encode()
        )
        await on_stdout_line(
            json.dumps({"type": "result", "result": {"execution_id": intent.execution_id}}).encode()
        )
        return b""

    async def output(stream, chunk):
        seen.append((stream, chunk))

    monkeypatch.setattr(wsl_transport, "request_process", request_process)
    result = await transport.run(intent, snapshot, deadline=time.monotonic() + 30, on_output=output)
    assert result["execution_id"] == intent.execution_id
    assert seen == [("stdout", b"h\xc3\xa9"), ("stderr", b"x")]


@pytest.mark.parametrize(
    "frame",
    [
        b'{"type":"output","stream":"stdout","chunk_b64":""}',
        b'{"type":"output","stream":"other","chunk_b64":"eA=="}',
        b'{"type":"result","result":{"execution_id":"x"}}',
    ],
)
async def test_run_rejects_invalid_or_duplicate_frames(frame, monkeypatch):
    digest, _ = wsl_transport.profile_fingerprint()
    intent = CommandIntent(
        argv=["/usr/bin/true"],
        max_seconds=10,
        execution_id="a" * 32,
        backend="wsl-bubblewrap-v1",
        distribution="Ubuntu",
        profile_sha256=digest,
        workspace="fixture",
    )
    transport = wsl_transport.WSLTransport("Ubuntu", digest, ("fixture-manager",))

    async def request_process(*args, on_stdout_line=None, **kwargs):
        await on_stdout_line(frame)
        if b'"type":"result"' in frame:
            await on_stdout_line(frame)
        return b""

    monkeypatch.setattr(wsl_transport, "request_process", request_process)
    with pytest.raises(TransportError):
        await transport.run(intent, WorkspaceSnapshot((), (), ()), deadline=time.monotonic() + 30)

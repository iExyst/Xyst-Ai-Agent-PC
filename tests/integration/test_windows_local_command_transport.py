"""Real Windows AppContainer acceptance for the local command transport."""

import asyncio
import base64
import json
import os
import socket
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from xaa.runtime.command_contracts import CommandGrant, CommandIntent
from xaa.runtime.execution_readiness import (
    WINDOWS_LOCAL_CHECKS,
    ExecutionReadiness,
    ReadinessRequest,
)
from xaa.runtime.mcp_appcontainer import process_identity
from xaa.runtime.windows_local_transport import (
    WINDOWS_LOCAL_DISTRIBUTION,
    WindowsLocalTransport,
    profile_fingerprint,
)
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows AppContainer acceptance")


@pytest.mark.asyncio
async def test_local_command_runs_in_private_no_network_appcontainer(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = workspace_tmp_path
    host_sentinel = tmp_path / "host-secret.txt"
    host_sentinel.write_text("must stay unreadable", encoding="utf-8")
    monkeypatch.setenv("LM_STUDIO_API_TOKEN", "must-not-enter-child-environment")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        program = (
            "import ctypes,json,os,pathlib,socket,sys\n"
            "p=ctypes.c_int(0);n=ctypes.c_ulong(0);t=ctypes.c_void_p();k=ctypes.WinDLL('kernel32',use_last_error=True);a=ctypes.WinDLL('advapi32',use_last_error=True);"
            "k.GetCurrentProcess.restype=ctypes.c_void_p;"
            "a.OpenProcessToken.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_void_p)];a.OpenProcessToken.restype=ctypes.c_int;"
            "a.GetTokenInformation.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong)];a.GetTokenInformation.restype=ctypes.c_int;"
            "app=a.OpenProcessToken(k.GetCurrentProcess(),8,ctypes.byref(t)) and a.GetTokenInformation(t,29,ctypes.byref(p),ctypes.sizeof(p),ctypes.byref(n)) and p.value==1\n"
            "try:pathlib.Path(sys.argv[1]).read_text();host_denied=False\n"
            "except OSError:host_denied=True\n"
            "try:\n s=socket.create_connection(('127.0.0.1',int(sys.argv[2])),timeout=1);s.close();network_denied=False\n"
            "except OSError:network_denied=True\n"
            "private=pathlib.Path('private-write.txt');private.write_text(pathlib.Path('fixture.txt').read_text())\n"
            "assert set(('XAA_API_TOKEN','LM_STUDIO_API_TOKEN','LM_API_TOKEN')).isdisjoint(os.environ)\n"
            "print(json.dumps({'appcontainer':app,'host_denied':host_denied,'network_denied':network_denied,'private_write':private.read_text()}))\n"
        )
        digest = profile_fingerprint()
        transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
        snapshot = WorkspaceSnapshot((SnapshotFile("fixture.txt", b"private fixture"),), (), ())
        intent = CommandIntent(
            argv=[sys.executable, "-I", "-c", program, str(host_sentinel), str(port)],
            max_seconds=10,
            execution_id=uuid4().hex,
            backend="windows-appcontainer-v1",
            distribution=WINDOWS_LOCAL_DISTRIBUTION,
            profile_sha256=digest,
            workspace=str(tmp_path),
        )
        receipt = await transport.run(intent, snapshot, deadline=time.monotonic() + 30)

    assert receipt["succeeded"] is True
    assert receipt["cleanup_confirmed"] is True
    assert receipt["workspace_changes_persisted"] is False
    output = json.loads(base64.b64decode(receipt["stdout_b64"], validate=True))
    assert output == {
        "appcontainer": True,
        "host_denied": True,
        "network_denied": True,
        "private_write": "private fixture",
    }
    assert host_sentinel.read_text(encoding="utf-8") == "must stay unreadable"
    readiness = ExecutionReadiness()
    checked = await readiness.check(
        ReadinessRequest(
            backend="windows-appcontainer-v1",
            distribution=WINDOWS_LOCAL_DISTRIBUTION,
        )
    )
    assert checked.state == "ready"
    assert tuple(checked.checks) == WINDOWS_LOCAL_CHECKS
    assert checked.profile_sha256 == digest
    readiness.require_grant(
        CommandGrant(
            backend="windows-appcontainer-v1",
            distribution=WINDOWS_LOCAL_DISTRIBUTION,
            profile_sha256=digest,
        )
    )


def test_local_command_rejects_shell_scripts() -> None:
    from xaa.runtime.windows_local_transport import _resolve_executable

    with pytest.raises(PermissionError, match="directly executable"):
        _resolve_executable(r"C:\Windows\System32\cmd.bat")


@pytest.mark.asyncio
async def test_cancelling_local_command_closes_descendant_processes(
    workspace_tmp_path: Path,
) -> None:
    digest = profile_fingerprint()
    transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
    snapshot = WorkspaceSnapshot((SnapshotFile("fixture.txt", b"cancel fixture"),), (), ())
    command = (
        "import subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'])\n"
        "print(child.pid,flush=True);time.sleep(60)\n"
    )
    intent = CommandIntent(
        argv=[sys.executable, "-I", "-c", command],
        max_seconds=30,
        execution_id=uuid4().hex,
        backend="windows-appcontainer-v1",
        distribution=WINDOWS_LOCAL_DISTRIBUTION,
        profile_sha256=digest,
        workspace=str(workspace_tmp_path),
    )
    output = bytearray()
    child_started = asyncio.Event()

    async def capture(_stream: str, chunk: bytes) -> None:
        output.extend(chunk)
        if b"\n" in output:
            child_started.set()

    task = asyncio.create_task(
        transport.run(
            intent,
            snapshot,
            deadline=time.monotonic() + 40,
            on_output=capture,
        )
    )
    await asyncio.wait_for(child_started.wait(), timeout=20)
    child_pid = int(output.decode("ascii").strip())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    deadline = time.monotonic() + 5
    while process_identity(child_pid)[0] is not False and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert process_identity(child_pid)[0] is False

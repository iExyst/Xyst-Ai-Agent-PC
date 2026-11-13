"""Real Windows AppContainer acceptance for the local command transport."""

import asyncio
import base64
import hashlib
import json
import os
import re
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
            "private=pathlib.Path('tests/nested-write.txt');private.write_text(pathlib.Path('tests/nested-fixture.txt').read_text())\n"
            "assert set(('XAA_API_TOKEN','LM_STUDIO_API_TOKEN','LM_API_TOKEN')).isdisjoint(os.environ)\n"
            "print(json.dumps({'appcontainer':app,'host_denied':host_denied,'network_denied':network_denied,'private_write':private.read_text()}))\n"
        )
        digest = profile_fingerprint()
        transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
        snapshot = WorkspaceSnapshot(
            (
                SnapshotFile("fixture.txt", b"private fixture"),
                SnapshotFile("tests/nested-fixture.txt", b"nested private fixture"),
            ),
            ("tests",),
            (),
        )
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

    assert receipt["succeeded"] is True, receipt
    assert receipt["cleanup_confirmed"] is True
    assert receipt["workspace_changes_persisted"] is False
    output = json.loads(base64.b64decode(receipt["stdout_b64"], validate=True))
    assert output == {
        "appcontainer": True,
        "host_denied": True,
        "network_denied": True,
        "private_write": "nested private fixture",
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


@pytest.mark.asyncio
async def test_local_command_preserves_fragmented_utf8_stdout_stderr_and_exit_status(
    workspace_tmp_path: Path,
) -> None:
    transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
    snapshot = WorkspaceSnapshot((SnapshotFile("fixture.txt", b"utf8 fixture"),), (), ())
    stdout_text = "café ☃ — 你好 — 🧪\n"
    stderr_text = "ошибка — naïve — 🚧\n"
    program = (
        "import os,sys\n"
        "for fd,text in ((1,sys.argv[1]),(2,sys.argv[2])):\n"
        " data=text.encode('utf-8')\n"
        " for byte in data: os.write(fd,bytes((byte,)))\n"
        "raise SystemExit(7)\n"
    )
    intent = CommandIntent(
        argv=[sys.executable, "-I", "-c", program, stdout_text, stderr_text],
        max_seconds=10,
        execution_id=uuid4().hex,
        backend="windows-appcontainer-v1",
        distribution=WINDOWS_LOCAL_DISTRIBUTION,
        profile_sha256=profile_fingerprint(),
        workspace=str(workspace_tmp_path),
    )
    streamed: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}

    async def capture(stream: str, chunk: bytes) -> None:
        streamed[stream].extend(chunk)

    receipt = await transport.run(
        intent,
        snapshot,
        deadline=time.monotonic() + 30,
        on_output=capture,
    )
    expected_stdout = stdout_text.encode("utf-8")
    expected_stderr = stderr_text.encode("utf-8")
    receipt_stdout = base64.b64decode(receipt["stdout_b64"], validate=True)
    receipt_stderr = base64.b64decode(receipt["stderr_b64"], validate=True)

    assert receipt["succeeded"] is False
    assert receipt["output_complete"] is True
    assert receipt["exit_code"] == 7
    assert receipt["cleanup_confirmed"] is True
    assert receipt_stdout.endswith(expected_stdout)
    assert receipt_stderr.endswith(expected_stderr)
    assert receipt_stdout.count(expected_stdout) == 1
    assert receipt_stderr.count(expected_stderr) == 1
    assert bytes(streamed["stdout"]) == receipt_stdout
    assert bytes(streamed["stderr"]) == receipt_stderr
    assert receipt["stdout_bytes"] == len(receipt_stdout)
    assert receipt["stderr_bytes"] == len(receipt_stderr)
    assert receipt["stdout_sha256"] == hashlib.sha256(receipt_stdout).hexdigest()
    assert receipt["stderr_sha256"] == hashlib.sha256(receipt_stderr).hexdigest()


@pytest.mark.asyncio
async def test_local_command_output_limit_stops_owned_process_tree(
    workspace_tmp_path: Path,
) -> None:
    transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
    snapshot = WorkspaceSnapshot((SnapshotFile("fixture.txt", b"output limit fixture"),), (), ())
    program = (
        "import os,subprocess,sys,time\n"
        "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],"
        "stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
        "os.write(2,f'child-pid={child.pid}\\n'.encode())\n"
        "data=b'x'*(1024*1024+65536)\n"
        "while data:\n"
        " written=os.write(1,data)\n"
        " if written<=0: raise RuntimeError('short write made no progress')\n"
        " data=data[written:]\n"
        "time.sleep(60)\n"
    )
    intent = CommandIntent(
        argv=[sys.executable, "-I", "-c", program],
        max_seconds=20,
        execution_id=uuid4().hex,
        backend="windows-appcontainer-v1",
        distribution=WINDOWS_LOCAL_DISTRIBUTION,
        profile_sha256=profile_fingerprint(),
        workspace=str(workspace_tmp_path),
    )
    stdout = bytearray()
    stderr = bytearray()

    async def capture(stream: str, chunk: bytes) -> None:
        (stdout if stream == "stdout" else stderr).extend(chunk)

    try:
        receipt = await transport.run(
            intent,
            snapshot,
            deadline=time.monotonic() + 30,
            on_output=capture,
        )
    except ValueError as exc:
        assert "output exceeded the 1 MiB limit" in str(exc)
    else:
        child_match = re.search(rb"(?m)^child-pid=(\d+)\r?$", stderr)
        stderr_summary = stderr.decode("utf-8", errors="replace")[-600:]
        stderr_summary = re.sub(r"(?i)[A-Z]:\\[^\r\n]*", "<windows-path>", stderr_summary)
        stderr_summary = " ".join(stderr_summary.split())
        pytest.fail(
            "AppContainer transport returned without enforcing the output ceiling: "
            f"stdout_bytes={receipt.get('stdout_bytes')}, "
            f"stderr_bytes={receipt.get('stderr_bytes')}, "
            f"streamed_stdout_bytes={len(stdout)}, streamed_stderr_bytes={len(stderr)}, "
            f"termination_reason={receipt.get('termination_reason')}, "
            f"supervisor_exit_code={receipt.get('supervisor_exit_code')}, "
            f"output_complete={receipt.get('output_complete')}, "
            f"child_marker_seen={child_match is not None}, stderr_summary={stderr_summary!r}"
        )

    match = re.search(rb"(?m)^child-pid=(\d+)\r?$", stderr)
    assert match is not None, stderr.decode("utf-8", errors="replace")
    child_pid = int(match.group(1))
    deadline = time.monotonic() + 5
    while process_identity(child_pid)[0] is not False and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert process_identity(child_pid)[0] is False
    assert intent.execution_id not in transport._active


@pytest.mark.asyncio
async def test_local_command_enforces_process_tree_memory_limit(
    workspace_tmp_path: Path,
) -> None:
    transport = await WindowsLocalTransport.discover(deadline=time.monotonic() + 20)
    snapshot = WorkspaceSnapshot((SnapshotFile("fixture.txt", b"memory limit fixture"),), (), ())
    program = (
        "import sys\n"
        "print('memory-fixture-started',flush=True)\n"
        "try:\n"
        " data=bytearray(192*1024*1024)\n"
        " for offset in range(0,len(data),4096): data[offset]=1\n"
        "except MemoryError:\n"
        " print('memory-limit-enforced',flush=True)\n"
        " raise SystemExit(73)\n"
        "print('memory-limit-not-enforced',flush=True)\n"
    )
    intent = CommandIntent(
        argv=[sys.executable, "-I", "-c", program],
        max_seconds=20,
        execution_id=uuid4().hex,
        backend="windows-appcontainer-v1",
        distribution=WINDOWS_LOCAL_DISTRIBUTION,
        profile_sha256=profile_fingerprint(),
        workspace=str(workspace_tmp_path),
    )
    streamed = bytearray()

    async def capture(stream: str, chunk: bytes) -> None:
        if stream == "stdout":
            streamed.extend(chunk)

    receipt = await transport.run(
        intent,
        snapshot,
        deadline=time.monotonic() + 30,
        on_output=capture,
    )
    stdout = base64.b64decode(receipt["stdout_b64"], validate=True)
    assert streamed == stdout
    assert b"memory-fixture-started" in stdout
    assert b"memory-limit-enforced" in stdout
    assert b"memory-limit-not-enforced" not in stdout
    assert receipt["output_complete"] is True
    assert receipt["succeeded"] is False
    assert receipt["exit_code"] not in {None, 0}
    assert receipt["cleanup_confirmed"] is True
    assert intent.execution_id not in transport._active


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
        "import os,subprocess,time\n"
        "child=subprocess.Popen([os.path.join(os.environ['SYSTEMROOT'],'System32','timeout.exe'),'/t','60','/nobreak'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)\n"
        "print('child-pid='+str(child.pid),flush=True);time.sleep(60)\n"
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

    async def capture(stream: str, chunk: bytes) -> None:
        if stream != "stdout":
            return
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
    match = re.search(rb"(?m)^child-pid=(\d+)\r?$", output)
    assert match is not None, output.decode("ascii", errors="replace")
    child_pid = int(match.group(1))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    deadline = time.monotonic() + 5
    while process_identity(child_pid)[0] is not False and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert process_identity(child_pid)[0] is False

"""Exercise application-owned WSL IPC against the actual restricted backend."""

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path
from uuid import uuid4

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.owned_transport import TransportError
from xaa.runtime.workspace_snapshot import capture_workspace
from xaa.runtime.wsl_transport import WSLTransport


async def diagnose(distribution: str) -> None:
    root = Path(".acceptance") / str(uuid4())
    project = root / "project"
    project.mkdir(parents=True)
    nonce = uuid4().hex
    (project / "fixture.txt").write_text(nonce, encoding="utf-8")
    snapshot = capture_workspace(project)
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 60)

    def intent(argv: list[str], seconds: int = 10) -> CommandIntent:
        return CommandIntent(
            argv=argv,
            max_seconds=seconds,
            execution_id=uuid4().hex,
            backend="wsl-bubblewrap-v1",
            distribution=distribution,
            profile_sha256=transport.profile_sha256,
            workspace=str(project.resolve()),
        )

    normal = intent(
        [
            "/usr/bin/python3",
            "-I",
            "-c",
            "import pathlib;print(pathlib.Path('fixture.txt').read_text())",
        ]
    )
    result = await transport.run(normal, snapshot, deadline=time.monotonic() + 20)
    assert result["succeeded"] is True and result["cleanup_confirmed"] is True
    assert base64.b64decode(result["stdout_b64"]).decode().strip() == nonce

    deltas: list[tuple[str, bytes, float]] = []

    async def receive_delta(stream: str, chunk: bytes) -> None:
        deltas.append((stream, chunk, time.monotonic()))

    streamed = intent(
        [
            "/usr/bin/python3",
            "-I",
            "-c",
            (
                "import sys,time;sys.stdout.write('early-');sys.stdout.flush();time.sleep(0.8);"
                "sys.stdout.write('late\\n');sys.stdout.flush()"
            ),
        ]
    )
    streamed_result = await transport.run(
        streamed, snapshot, deadline=time.monotonic() + 20, on_output=receive_delta
    )
    received_at = time.monotonic()
    streamed_bytes = b"".join(chunk for stream, chunk, _at in deltas if stream == "stdout")
    receipt_bytes = base64.b64decode(streamed_result["stdout_b64"], validate=True)
    assert streamed_result["succeeded"] and streamed_result["cleanup_confirmed"]
    assert len(deltas) >= 2 and streamed_bytes == receipt_bytes == b"early-late\n"
    assert received_at - deltas[0][2] >= 0.5
    status = await transport.control("status", normal, deadline=time.monotonic() + 10)
    assert status["closed"] and not status["manager_alive"] and status["outcome"] is not None
    try:
        await transport.run(normal, snapshot, deadline=time.monotonic() + 20)
    except TransportError:
        pass
    else:
        raise AssertionError("Completed execution was replayed")

    expired = intent(["/usr/bin/true"])
    try:
        await transport.run(expired, snapshot, deadline=time.monotonic() + 0.5)
    except TimeoutError:
        pass
    else:
        raise AssertionError("Command exceeded remaining parent allowance")
    before = await transport.control("status", expired, deadline=time.monotonic() + 10)
    assert before["claim"] is None
    await transport.control("cleanup", expired, deadline=time.monotonic() + 10)

    cancelled = intent(["/usr/bin/python3", "-I", "-c", "import time;time.sleep(25)"], 30)
    task = asyncio.create_task(transport.run(cancelled, snapshot, deadline=time.monotonic() + 40))
    try:
        deadline = time.monotonic() + 15
        while True:
            observed = await transport.control("status", cancelled, deadline=deadline)
            if observed["manager_alive"]:
                break
            await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        acknowledgement = await transport.control(
            "cleanup", cancelled, deadline=time.monotonic() + 15
        )
    assert acknowledgement["cleanup_confirmed"] is True
    deadline = time.monotonic() + 10
    while True:
        final = await transport.control("status", cancelled, deadline=deadline)
        if not final["manager_alive"]:
            break
        await asyncio.sleep(0.05)
    assert final["closed"] is True
    assert capture_workspace(project).sha256 == snapshot.sha256
    report = {
        "passed": True,
        "distribution": distribution,
        "profile_sha256": transport.profile_sha256,
        "normal_result_verified": True,
        "incremental_output_verified": True,
        "incremental_delta_count": len(deltas),
        "first_delta_preceded_receipt_seconds": round(received_at - deltas[0][2], 3),
        "incremental_output_matches_receipt": streamed_bytes == receipt_bytes,
        "replay_refused": True,
        "remaining_time_rejected_before_claim": True,
        "launcher_cancellation_awaited": True,
        "separate_remote_cleanup_confirmed": True,
        "cancelled_manager_alive": final["manager_alive"],
        "cancelled_result_available": final["outcome"] is not None,
        "original_project_unchanged": True,
        "model_or_ui_tool_enabled": False,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Owned WSL transport acceptance passed: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    asyncio.run(diagnose(parser.parse_args().distribution))

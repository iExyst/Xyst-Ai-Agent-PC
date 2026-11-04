"""Cancel a real frozen worker blocked on SQLite; verify its process tree exits."""

import asyncio
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from xaa.runtime import tool_worker
from xaa.storage.runs import RunStore


def process_tree() -> dict[int, int]:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId | ConvertTo-Json -Compress",
        ],
        check=True,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return {row["ProcessId"]: row["ParentProcessId"] for row in json.loads(result.stdout)}


async def main() -> None:
    if os.name != "nt":
        raise RuntimeError("This diagnostic requires Windows.")
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    store = RunStore(root / "runs.sqlite3")
    run = store.create_run(
        objective="Frozen worker cancellation",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model=None,
    )
    token = store.acquire_lease(run["id"], "diagnostic")
    assert token is not None
    (root / "fixture.txt").write_bytes(b"unchanged")
    # Force the worker to wait on its journal transaction, not on a fake operation.
    lock = sqlite3.connect(store.path)
    lock.execute("BEGIN IMMEDIATE")
    original = asyncio.create_subprocess_exec
    launched = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []

    async def spawn(*command: str, **kwargs: Any) -> asyncio.subprocess.Process:
        process = await original(*command, **kwargs)
        if "--tool-worker" in command:
            processes.append(process)
            launched.set()
        return process

    task = None
    descendants: set[int] = set()
    try:
        with (
            patch.object(tool_worker.sys, "frozen", True, create=True),
            patch.object(
                tool_worker.sys,
                "executable",
                str(Path("build/native-dev/xaa-engine.exe").resolve()),
            ),
            patch.object(asyncio, "create_subprocess_exec", spawn),
        ):
            task = asyncio.create_task(
                tool_worker.execute_filesystem(
                    store,
                    run["id"],
                    token,
                    "read",
                    "read",
                    {"path": "fixture.txt"},
                    idempotency_key="read",
                    timeout=10,
                )
            )
            await asyncio.wait_for(launched.wait(), 3)
            pid = processes[0].pid
            for _ in range(15):
                snapshot = await asyncio.to_thread(process_tree)
                descendants = set()
                while True:
                    expanded = {
                        child
                        for child, parent in snapshot.items()
                        if parent in ({pid} | descendants)
                    }
                    if expanded <= descendants:
                        break
                    descendants |= expanded
                if descendants:
                    break
                await asyncio.sleep(0.1)
            if not descendants:
                raise RuntimeError("Frozen application child was not observed.")
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            snapshot = await asyncio.to_thread(process_tree)
            survivors = ({pid} | descendants) & snapshot.keys()
            if survivors:
                raise RuntimeError("Frozen worker processes survived cancellation.")
    finally:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        lock.rollback()
        lock.close()
        store.release_lease(run["id"], token)
    report = {
        "passed": True,
        "build_sha256": json.loads(
            Path("build/native-dev/manifest.json").read_text(encoding="utf-8")
        ),
        "observed_application_children": len(descendants),
        "bootloader_and_children_stopped": True,
        "fixture_unchanged": (root / "fixture.txt").read_bytes() == b"unchanged",
        "unresolved_intents": len(store.unresolved_tool_calls(run["id"])),
    }
    assert report["fixture_unchanged"]
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

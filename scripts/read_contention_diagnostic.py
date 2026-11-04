"""Release a real Windows exclusive lock only after observing a journaled read retry."""

import argparse
import asyncio
import ctypes
import json
import os
from contextlib import ExitStack, suppress
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from xaa.runtime import tool_worker
from xaa.runtime.file_handles import open_existing_for_edit
from xaa.storage.runs import RunStore


async def main() -> None:
    if os.name != "nt":
        raise RuntimeError("Windows sharing violation diagnostic.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", action="store_true")
    args = parser.parse_args()
    root = (Path(".acceptance") / str(uuid4())).resolve()
    workspace = root / "workspace"
    workspace.mkdir(parents=True)
    directory = workspace / "locked"
    directory.mkdir()
    target = directory / "locked.txt"
    original = b"unchanged contention evidence\r\n"
    target.write_bytes(original)
    stamp = target.stat().st_mtime_ns
    store = RunStore(root / "runs.sqlite3")
    run = store.create_run(
        objective="Read temporarily locked file",
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="observe",
        model=None,
    )
    token = store.acquire_lease(run["id"], "diagnostic")
    assert token is not None
    frozen = os.environ.get("XAA_FROZEN_WORKER") == "1"
    task = None
    report: dict[str, object] = {
        "passed": False,
        "frozen_worker": frozen,
        "directory_listing": args.directory,
    }
    try:
        with ExitStack() as stack:
            if frozen:
                stack.enter_context(patch.object(tool_worker.sys, "frozen", True, create=True))
                stack.enter_context(
                    patch.object(
                        tool_worker.sys,
                        "executable",
                        str(Path("build/native-dev/xaa-engine.exe").resolve()),
                    )
                )
            if args.directory:
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                create = kernel.CreateFileW
                create.argtypes = [
                    ctypes.c_wchar_p,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                    ctypes.c_uint32,
                    ctypes.c_void_p,
                ]
                create.restype = ctypes.c_void_p
                close = kernel.CloseHandle
                close.argtypes = [ctypes.c_void_p]
                handle = create(str(directory), 1, 0, None, 3, 0x02000000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise ctypes.WinError(ctypes.get_last_error())
                lock = ExitStack()
                lock.callback(close, handle)
            else:
                lock = open_existing_for_edit(target)
            with lock:
                task = asyncio.create_task(
                    tool_worker.execute_filesystem(
                        store,
                        run["id"],
                        token,
                        "read",
                        "list" if args.directory else "read",
                        {"path": "locked" if args.directory else "locked/locked.txt"},
                        idempotency_key="read",
                    )
                )
                for _ in range(400):
                    if any(
                        e["event_type"] == "tool.read_retry" for e in store.list_events(run["id"])
                    ):
                        break
                    if task.done():
                        await task
                        raise RuntimeError("Read finished without encountering the held lock.")
                    await asyncio.sleep(0.005)
                else:
                    raise TimeoutError("No read retry observed.")
            result = await task
        events = store.list_events(run["id"])
        retries = sum(e["event_type"] == "tool.read_retry" for e in events)
        passed = (
            (
                {"name": "locked.txt", "kind": "file"} in result["entries"]
                if args.directory
                else result["text"] == original.decode()
            )
            and 1 <= retries <= 2
            and target.read_bytes() == original
            and target.stat().st_mtime_ns == stamp
            and not store.unresolved_tool_calls(run["id"])
        )
        report.update(
            passed=passed,
            retries=retries,
            file_unchanged=target.read_bytes() == original,
            mtime_unchanged=target.stat().st_mtime_ns == stamp,
            intended_actions=sum(e["event_type"] == "tool.intended" for e in events),
        )
        if frozen:
            report["build_sha256"] = json.loads(
                Path("build/native-dev/manifest.json").read_text(encoding="utf-8")
            )
        if not passed:
            raise RuntimeError("Contention retry acceptance failed.")
    finally:
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        store.release_lease(run["id"], token)
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

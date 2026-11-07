"""Bounded directory discovery through a verified directory handle."""

import ctypes
import errno
import json
import os
import stat
import struct
import time
from functools import partial
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.executor import ReconciliationRequired
from xaa.runtime.file_handles import resolve_read_path, stat_read_path
from xaa.storage.runs import RunStore


class ListDirectoryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(default=".", min_length=1, max_length=2048)


LIST_DIRECTORY_TOOL = {
    "name": "workspace_list_directory",
    "version": "1",
    "permissions": ["workspace.read"],
    "behavior": "read",
    "input_schema": ListDirectoryArguments.model_json_schema(),
    "entry_limit": 256,
    "entry_bytes_limit": 32768,
    "cancellation": "between coordinator tool calls; bounded local enumeration",
    "retry": "up to three transient read attempts, 50/100 ms backoff; fresh verified handle",
    "audit": ["run_id", "call_id", "path", "entries", "truncated"],
}


def _windows_entries(target: Path, root: Path) -> list[dict[str, str]]:
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
    # Read directory data, allow sharing, OPEN_EXISTING + BACKUP_SEMANTICS.
    handle = create(str(target), 1, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
    try:
        resolve = kernel.GetFinalPathNameByHandleW
        resolve.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        resolve.restype = ctypes.c_uint32
        name = ctypes.create_unicode_buffer(32768)
        length = resolve(handle, name, len(name), 0)
        if not length or length >= len(name):
            raise OSError("Cannot verify directory handle.")
        actual = name.value
        if actual.startswith("\\\\?\\UNC\\"):
            actual = "\\\\" + actual[8:]
        elif actual.startswith("\\\\?\\"):
            actual = actual[4:]
        if not Path(actual).is_relative_to(root):
            raise PermissionError("Opened directory escaped workspace.")
        query = kernel.GetFileInformationByHandleEx
        query.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        query.restype = ctypes.c_int
        entries: list[dict[str, str]] = []
        while len(entries) < 257:
            buffer = ctypes.create_string_buffer(65536)
            # FileFullDirectoryInfo enumerates the already opened directory.
            if not query(handle, 14, buffer, len(buffer)):
                error = ctypes.get_last_error()
                if error == 18:  # ERROR_NO_MORE_FILES
                    break
                raise ctypes.WinError(error)
            offset = 0
            while True:
                next_offset = struct.unpack_from("<I", buffer.raw, offset)[0]
                attrs, size = struct.unpack_from("<II", buffer.raw, offset + 56)
                if size > len(buffer) - offset - 68 or size % 2:
                    raise OSError("Invalid directory entry.")
                filename = buffer.raw[offset + 68 : offset + 68 + size].decode("utf-16-le")
                if filename not in {".", ".."}:
                    kind = "link" if attrs & 1024 else "directory" if attrs & 16 else "file"
                    entries.append({"name": filename, "kind": kind})
                if len(entries) >= 257 or not next_offset:
                    break
                if next_offset < 68 or offset + next_offset + 68 > len(buffer):
                    raise OSError("Invalid directory entry offset.")
                offset += next_offset
        return entries
    finally:
        close(handle)


def _entries(target: Path, root: Path) -> list[dict[str, str]]:
    if os.name == "nt":
        entries = _windows_entries(target, root)
    else:
        descriptor = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
        try:
            if not Path(f"/proc/self/fd/{descriptor}").resolve(strict=True).is_relative_to(root):
                raise PermissionError("Opened directory escaped workspace.")
            entries = []
            with os.scandir(descriptor) as iterator:
                for entry in iterator:
                    kind = (
                        "link"
                        if entry.is_symlink()
                        else "directory"
                        if entry.is_dir(follow_symlinks=False)
                        else "file"
                    )
                    entries.append({"name": entry.name, "kind": kind})
                    if len(entries) >= 257:
                        break
        finally:
            os.close(descriptor)
    return entries


def list_directory(
    store: RunStore,
    run_id: str,
    token: str,
    call_id: str,
    arguments: dict[str, Any],
    *,
    internal: bool = False,
) -> dict[str, Any]:
    parsed = ListDirectoryArguments.model_validate(arguments)
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if not run["workspace"] or run["mode"] not in {"observe", "workspace", "authorized_automation"}:
        raise PermissionError("Select a workspace with read permission.")

    def path_retry(stage: str, attempt: int, delay_ms: int) -> None:
        store.append_worker_event(
            run_id,
            token,
            "tool.read_retry",
            {
                "call_id": call_id,
                "attempt": attempt,
                "delay_ms": delay_ms,
                "stage": stage,
            },
        )

    def require_owner() -> None:
        with store.connection() as connection:
            store.require_lease(connection, run_id, token)

    root = resolve_read_path(
        Path(run["workspace"]), on_retry=partial(path_retry, "path_resolution")
    )
    requested = Path(parsed.path)
    if requested.is_absolute():
        raise PermissionError("Use a relative directory path.")
    target = resolve_read_path(root / requested, on_retry=partial(path_retry, "path_resolution"))
    if not target.is_relative_to(root) or not stat.S_ISDIR(
        stat_read_path(
            target, before_attempt=require_owner, on_retry=partial(path_retry, "metadata")
        ).st_mode
    ):
        raise PermissionError("Directory is outside the workspace.")
    if not store.record_tool_intent(
        run_id,
        token,
        call_id=call_id,
        tool="workspace.list_directory",
        version="1",
        arguments=parsed.model_dump(),
        idempotency_key=call_id,
        internal=internal,
    ):
        raise ReconciliationRequired("Directory listing already journaled.")
    for attempt in range(3):
        with store.connection() as connection:
            store.require_lease(connection, run_id, token)
        try:
            entries = _entries(target, root)
            break
        except OSError as exc:
            transient = getattr(exc, "winerror", None) in {32, 33, 121, 1237} or exc.errno in {
                errno.EAGAIN,
                errno.EINTR,
                errno.ETIMEDOUT,
                errno.ESTALE,
            }
            if not transient or attempt == 2:
                raise
            store.append_worker_event(
                run_id,
                token,
                "tool.read_retry",
                {"call_id": call_id, "attempt": attempt + 1, "delay_ms": 50 * 2**attempt},
            )
            time.sleep(0.05 * 2**attempt)
    else:
        raise RuntimeError("Directory retry loop exhausted without a result.")
    bounded: list[dict[str, str]] = []
    used = 0
    for entry in entries[:256]:
        size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        if used + size > 32768:
            break
        bounded.append(entry)
        used += size
    result: dict[str, Any] = {
        "path": parsed.path,
        "entries": bounded,
        "truncated": len(bounded) < len(entries),
        "trust": "untrusted_workspace_content",
    }
    store.record_tool_result(run_id, token, call_id, result, internal=internal)
    return result

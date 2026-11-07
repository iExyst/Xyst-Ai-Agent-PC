"""Resolve actual opened file destinations before consuming workspace data."""

import ctypes
import errno
import os
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from xaa.runtime.mutation_policy import require_mutable_destination


def is_transient_read_error(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in {32, 33, 121, 1237} or exc.errno in {
        errno.EAGAIN,
        errno.EINTR,
        errno.ETIMEDOUT,
        errno.ESTALE,
    }


def resolve_read_path(
    path: Path,
    *,
    on_retry: Callable[[int, int], None] | None = None,
) -> Path:
    """Resolve a read target, retrying only known transient filesystem errors."""
    for attempt in range(3):
        try:
            return path.resolve(strict=True)
        except OSError as exc:
            if not is_transient_read_error(exc) or attempt == 2:
                raise
            delay_ms = 50 * 2**attempt
            if on_retry is not None:
                on_retry(attempt + 1, delay_ms)
            time.sleep(delay_ms / 1000)
    raise RuntimeError("Path resolution retry loop exhausted without a result.")


def stat_read_path(
    path: Path,
    *,
    before_attempt: Callable[[], None],
    on_retry: Callable[[int, int], None],
) -> os.stat_result:
    """Inspect the resolved read target while retaining lease and retry limits."""
    for attempt in range(3):
        before_attempt()
        try:
            return path.stat()
        except OSError as exc:
            if not is_transient_read_error(exc) or attempt == 2:
                raise
            delay_ms = 50 * 2**attempt
            on_retry(attempt + 1, delay_ms)
            time.sleep(delay_ms / 1000)
    raise RuntimeError("Metadata retry loop exhausted without a result.")


@contextmanager
def open_for_read(path: Path) -> Generator[BinaryIO]:
    """Preserve Windows sharing-error codes instead of CRT's ambiguous EACCES."""
    if os.name != "nt":
        with path.open("rb") as source:
            yield source
        return
    import msvcrt

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
    handle = create(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close(handle)
        raise
    with os.fdopen(descriptor, "rb") as source:
        yield source


@contextmanager
def open_existing_for_edit(path: Path, *, database: Path | None = None) -> Generator[BinaryIO]:
    """Hold exclusive access for the entire compare/write/flush interval."""
    if os.name == "nt":
        import msvcrt

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
        # OPEN_EXISTING, read/write, no sharing: deny writes, reads and rename/delete.
        handle = create(str(path), 0xC0000000, 0, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "Cannot acquire exclusive file access.")
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
        except BaseException:
            close = kernel.CloseHandle
            close.argtypes = [ctypes.c_void_p]
            close(handle)
            raise
        with os.fdopen(descriptor, "r+b") as destination:
            if database is not None:
                require_mutable_destination(opened_file_path(destination), database)
            if os.fstat(destination.fileno()).st_nlink != 1:
                raise PermissionError("Workspace edits cannot modify files with hard-link aliases.")
            yield destination
    else:
        import fcntl

        with path.open("r+b") as destination:
            fcntl.flock(destination.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                if database is not None:
                    require_mutable_destination(opened_file_path(destination), database)
                if os.fstat(destination.fileno()).st_nlink != 1:
                    raise PermissionError(
                        "Workspace edits cannot modify files with hard-link aliases."
                    )
                yield destination
            finally:
                fcntl.flock(destination.fileno(), fcntl.LOCK_UN)


def opened_file_path(source: BinaryIO) -> Path:
    if os.name == "nt":
        import msvcrt

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        resolve = kernel.GetFinalPathNameByHandleW
        resolve.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        resolve.restype = ctypes.c_uint32
        handle = msvcrt.get_osfhandle(source.fileno())
        buffer = ctypes.create_unicode_buffer(32768)
        length = resolve(handle, buffer, len(buffer), 0)
        if length == 0 or length >= len(buffer):
            raise OSError(ctypes.get_last_error(), "Cannot resolve the opened file destination.")
        path = buffer.value
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path[8:]
        elif path.startswith("\\\\?\\"):
            path = path[4:]
        return Path(path)
    descriptor = Path("/proc/self/fd") / str(source.fileno())
    if descriptor.exists():
        return descriptor.resolve(strict=True)
    raise OSError("This platform does not yet support verified file-handle containment.")


def delete_opened_file_if_hash(
    path: Path,
    root: Path,
    expected_sha256: str,
    *,
    database: Path,
    before_delete: Callable[[], None],
) -> int:
    """Exclusively hash and unlink one regular Windows file through its open handle."""
    if os.name != "nt":
        raise PermissionError("Reviewed file deletion currently requires Windows.")
    import hashlib
    import msvcrt

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
    # DELETE plus read access, no sharing, and open the final reparse point itself.
    handle = create(str(path), 0x00010000 | 0x80000000, 0, None, 3, 0x80 | 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value or handle is None:
        raise OSError(ctypes.get_last_error(), "Cannot acquire exclusive file deletion access.")
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close(handle)
        raise
    with os.fdopen(descriptor, "rb") as source:
        actual_path = opened_file_path(source)
        if not actual_path.is_relative_to(root):
            raise PermissionError("Opened deletion target escaped the selected workspace.")
        require_mutable_destination(actual_path, database)
        attributes = (ctypes.c_uint32 * 2)()
        inspect = kernel.GetFileInformationByHandleEx
        inspect.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        inspect.restype = ctypes.c_int
        native_handle = msvcrt.get_osfhandle(source.fileno())
        if not inspect(native_handle, 9, attributes, ctypes.sizeof(attributes)):
            raise OSError(ctypes.get_last_error(), "Cannot inspect deletion target attributes.")
        if attributes[0] & 0x400:
            raise PermissionError("Reviewed deletion does not follow links or junctions.")
        if os.fstat(source.fileno()).st_nlink != 1:
            raise PermissionError("Reviewed deletion refuses files with hard-link aliases.")
        content = source.read(65_537)
        if len(content) > 65_536:
            raise ValueError("Reviewed deletion requires a file no larger than 64 KiB.")
        actual_sha256 = hashlib.sha256(content).hexdigest()
        if actual_sha256 != expected_sha256:
            raise PermissionError("File changed after integration review.")
        before_delete()
        disposition = ctypes.c_ubyte(1)
        set_info = kernel.SetFileInformationByHandle
        set_info.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        set_info.restype = ctypes.c_int
        if not set_info(native_handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise OSError(ctypes.get_last_error(), "Cannot delete the reviewed workspace file.")
        return len(content)

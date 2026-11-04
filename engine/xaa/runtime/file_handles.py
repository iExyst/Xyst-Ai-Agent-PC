"""Resolve actual opened file destinations before consuming workspace data."""

import ctypes
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


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
def open_existing_for_edit(path: Path) -> Generator[BinaryIO]:
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
            if os.fstat(destination.fileno()).st_nlink != 1:
                raise PermissionError("Workspace edits cannot modify files with hard-link aliases.")
            yield destination
    else:
        import fcntl

        with path.open("r+b") as destination:
            fcntl.flock(destination.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
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

"""Exclusive creation relative to verified directory handles; never overwrite."""

import ctypes
import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path, PureWindowsPath
from typing import BinaryIO

from xaa.runtime.mutation_policy import require_mutable_destination


def creation_parts(path: str) -> tuple[str, ...]:
    requested = PureWindowsPath(path)
    parts = path.replace("\\", "/").split("/")
    if (
        requested.drive
        or requested.root
        or len(parts) > 32
        or any(
            not part
            or part in {".", ".."}
            or part[-1:] in {" ", "."}
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or part.split(".")[0].upper()
            in {
                "CON",
                "PRN",
                "AUX",
                "NUL",
                *(f"COM{i}" for i in range(10)),
                *(f"LPT{i}" for i in range(10)),
            }
            for part in parts
        )
    ):
        raise PermissionError(
            "Creation requires a plain workspace-relative path without aliases or traversal."
        )
    return tuple(parts)


class CreationUncertainError(RuntimeError):
    """A new file exists, but opening its Python stream failed; reconcile it."""


class _UnicodeString(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint16),
        ("maximum", ctypes.c_uint16),
        ("buffer", ctypes.c_void_p),
    ]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint32),
        ("root", ctypes.c_void_p),
        ("name", ctypes.POINTER(_UnicodeString)),
        ("attributes", ctypes.c_uint32),
        ("security", ctypes.c_void_p),
        ("quality", ctypes.c_void_p),
    ]


class _IoStatus(ctypes.Structure):
    _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]


@contextmanager
def open_new_file(
    root: Path, parts: tuple[str, ...], *, database: Path | None = None
) -> Generator[BinaryIO]:
    if os.name != "nt":
        raise PermissionError("Exclusive verified file creation currently requires Windows.")
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    close = kernel.CloseHandle
    close.argtypes = [ctypes.c_void_p]
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
    nt_create = native.NtCreateFile
    nt_create.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_uint32,
        ctypes.POINTER(_ObjectAttributes),
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    nt_create.restype = ctypes.c_int32
    translate = native.RtlNtStatusToDosError
    translate.argtypes = [ctypes.c_int32]
    translate.restype = ctypes.c_uint32
    query = kernel.GetFileInformationByHandleEx
    query.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    query.restype = ctypes.c_int
    resolve = kernel.GetFinalPathNameByHandleW
    resolve.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    resolve.restype = ctypes.c_uint32
    handles: list[int] = []

    def relative(parent: int, name: str, *, directory: bool) -> int:
        buffer = ctypes.create_unicode_buffer(name)
        size = len(name.encode("utf-16-le"))
        unicode = _UnicodeString(size, size + 2, ctypes.cast(buffer, ctypes.c_void_p))
        attributes = _ObjectAttributes(
            ctypes.sizeof(_ObjectAttributes), parent, ctypes.pointer(unicode), 0x40, None, None
        )
        result = ctypes.c_void_p()
        status = _IoStatus()
        code = nt_create(
            ctypes.byref(result),
            0x100081 if directory else 0xC0100000,
            ctypes.byref(attributes),
            ctypes.byref(status),
            None,
            0x80,
            3 if directory else 0,
            1 if directory else 2,
            0x200021 if directory else 0x60,
            None,
            0,
        )
        if code < 0:
            error = translate(code)
            raise OSError(error, "Cannot open creation directory or exclusively create file.")
        assert result.value is not None
        return result.value

    try:
        # No delete sharing pins root and every descendant against rename/removal.
        handle = create(str(root), 0x100081, 3, None, 3, 0x02000000, None)
        if handle == ctypes.c_void_p(-1).value or handle is None:
            raise OSError(ctypes.get_last_error(), "Cannot pin workspace directory.")
        handles.append(handle)
        for index in range(len(parts)):
            parent = handles[-1]
            name = ctypes.create_unicode_buffer(32768)
            length = resolve(parent, name, len(name), 0)
            if not length or length >= len(name):
                raise OSError("Cannot resolve creation directory handle.")
            actual = name.value
            if actual.startswith("\\\\?\\UNC\\"):
                actual = "\\\\" + actual[8:]
            elif actual.startswith("\\\\?\\"):
                actual = actual[4:]
            if not Path(actual).is_relative_to(root):
                raise PermissionError("Opened creation directory escaped workspace.")
            attributes = (ctypes.c_uint32 * 2)()
            if not query(parent, 9, attributes, 8):
                raise OSError(ctypes.get_last_error(), "Cannot inspect creation directory.")
            if attributes[0] & 1024:
                raise PermissionError("Creation does not traverse directory links or junctions.")
            if index == len(parts) - 1:
                if database is not None:
                    require_mutable_destination(Path(actual) / parts[index], database)
                new_handle = relative(parent, parts[index], directory=False)
                try:
                    new_descriptor = msvcrt.open_osfhandle(new_handle, os.O_RDWR | os.O_BINARY)
                except BaseException as exc:
                    close(new_handle)
                    raise CreationUncertainError("File created but stream setup failed.") from exc
                try:
                    destination = os.fdopen(new_descriptor, "r+b")
                except BaseException as exc:
                    os.close(new_descriptor)
                    raise CreationUncertainError("File created but stream setup failed.") from exc
                with destination:
                    yield destination
                return
            handles.append(relative(parent, parts[index], directory=True))
    finally:
        for handle in reversed(handles):
            close(handle)

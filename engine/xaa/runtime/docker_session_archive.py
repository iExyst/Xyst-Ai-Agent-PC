"""Validation for bounded, untrusted Docker session workspace archives."""

from __future__ import annotations

import hashlib
import io
import tarfile
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

MAX_SESSION_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_SESSION_ARCHIVE_FILES = 4096
MAX_SESSION_ARCHIVE_CONTENT_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class ValidatedSessionArchive:
    size_bytes: int
    sha256: str
    file_count: int


def validate_session_archive(data: bytes) -> ValidatedSessionArchive:
    """Reject unsafe tar entries and unbounded archives without extracting them."""
    if not data or len(data) > MAX_SESSION_ARCHIVE_BYTES:
        raise ValueError("Docker session archive exceeds its 8 MiB size limit.")
    if len(data) % tarfile.BLOCKSIZE or data[-1024:] != bytes(1024):
        raise ValueError("Docker session archive is malformed.")
    seen: set[str] = set()
    total_content = 0
    file_count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for member in archive:
                raw_name = member.name
                if "\\" in raw_name or "\0" in raw_name:
                    raise ValueError("Docker session archive contains an invalid path.")
                normalized = raw_name.removeprefix("./")
                if normalized == "." and member.isdir():
                    continue
                posix = PurePosixPath(normalized)
                windows = PureWindowsPath(normalized)
                if (
                    not normalized
                    or len(normalized) > 2048
                    or posix.is_absolute()
                    or windows.drive
                    or windows.root
                    or any(part in {"", ".", ".."} for part in posix.parts)
                    or posix.as_posix() != normalized
                ):
                    raise ValueError("Docker session archive contains a path traversal.")
                if normalized in seen:
                    raise ValueError("Docker session archive contains duplicate paths.")
                seen.add(normalized)
                if not member.isdir() and not member.isfile():
                    raise ValueError("Docker session archive contains a non-regular entry.")
                if member.mode & 0o7000:
                    raise ValueError("Docker session archive contains unsafe permission bits.")
                if member.isdir():
                    continue
                file_count += 1
                total_content += member.size
                if (
                    file_count > MAX_SESSION_ARCHIVE_FILES
                    or member.size < 0
                    or total_content > MAX_SESSION_ARCHIVE_CONTENT_BYTES
                ):
                    raise ValueError("Docker session archive exceeds its file or content limit.")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Docker session archive contains unreadable file data.")
                remaining = member.size
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        raise ValueError("Docker session archive contains truncated file data.")
                    remaining -= len(chunk)
                if source.read(1):
                    raise ValueError("Docker session archive file size does not match its header.")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ValueError("Docker session archive is malformed.") from exc
    return ValidatedSessionArchive(
        size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), file_count=file_count
    )

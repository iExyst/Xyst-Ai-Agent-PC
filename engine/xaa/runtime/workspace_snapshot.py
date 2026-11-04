"""Bounded regular-file snapshots; transport never carries host filesystem mounts.

The wire codec/materializer uses only the Python standard library so the same
validation can run in an explicitly configured Linux execution manager.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

MAX_FILES = 256
MAX_DIRECTORIES = 256
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_WIRE_BYTES = 12 * 1024 * 1024
EXCLUDED_DIRECTORIES = frozenset({".git", ".venv", "node_modules", "__pycache__"})


def checked_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > 1024:
        raise ValueError("Snapshot path must be a bounded UTF-8 relative path.")
    parts = value.split("/")
    if len(parts) > 16 or any(
        not part or part in {".", ".."} or part.endswith((" ", ".")) for part in parts
    ):
        raise ValueError("Snapshot path has invalid or excessive components.")
    if any(ord(character) < 32 or character in '\\:*?"<>|' for character in value):
        raise ValueError("Snapshot path is not portable between Windows and Linux.")
    reserved = {"con", "prn", "aux", "nul"} | {
        f"{prefix}{index}" for prefix in ("com", "lpt") for index in range(1, 10)
    }
    if any(part.split(".", 1)[0].casefold() in reserved for part in parts):
        raise ValueError("Snapshot path contains a reserved device name.")
    return PurePosixPath(value)


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    content: bytes

    def __post_init__(self) -> None:
        checked_path(self.path)
        if not isinstance(cast(object, self.content), bytes) or len(self.content) > MAX_FILE_BYTES:
            raise ValueError("Snapshot file exceeds the regular-file byte limit.")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class WorkspaceSnapshot:
    files: tuple[SnapshotFile, ...]
    directories: tuple[str, ...]
    excluded: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            isinstance(cast(object, value), tuple)
            for value in (self.files, self.directories, self.excluded)
        ):
            raise ValueError("Snapshot collections must be immutable tuples.")
        if len(self.files) > MAX_FILES or len(self.directories) > MAX_DIRECTORIES:
            raise ValueError("Snapshot contains too many files or directories.")
        if len(self.excluded) > MAX_DIRECTORIES:
            raise ValueError("Snapshot contains too many omitted directories.")
        if not all(isinstance(cast(object, entry), SnapshotFile) for entry in self.files):
            raise ValueError("Invalid snapshot file record.")
        if sum(len(entry.content) for entry in self.files) > MAX_TOTAL_BYTES:
            raise ValueError("Snapshot exceeds the aggregate content limit.")
        names: dict[str, str] = {}
        for name in (*self.directories, *(entry.path for entry in self.files), *self.excluded):
            checked_path(name)
            folded = name.casefold()
            if folded in names:
                raise ValueError("Snapshot has duplicate or case-colliding paths.")
            names[folded] = name
        directory_names = set(self.directories)
        for name in (*self.directories, *(entry.path for entry in self.files), *self.excluded):
            for parent in checked_path(name).parents:
                if str(parent) != "." and str(parent) not in directory_names:
                    raise ValueError("Snapshot is missing an explicit parent directory.")

    def _metadata(self) -> dict[str, Any]:
        return {
            "format": 1,
            "directories": sorted(self.directories),
            "excluded": sorted(self.excluded),
            "files": [
                {"path": entry.path, "bytes": len(entry.content), "sha256": entry.sha256}
                for entry in sorted(self.files, key=lambda entry: entry.path)
            ],
        }

    @property
    def sha256(self) -> str:
        canonical = json.dumps(self._metadata(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_bytes(self) -> bytes:
        data = self._metadata()
        contents = {entry.path: entry.content for entry in self.files}
        for entry in data["files"]:
            entry["content_b64"] = base64.b64encode(contents[entry["path"]]).decode("ascii")
        data["sha256"] = self.sha256
        encoded = json.dumps(data, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_WIRE_BYTES:
            raise ValueError("Snapshot exceeds the transport byte limit.")
        return encoded

    @classmethod
    def from_bytes(cls, data: bytes) -> WorkspaceSnapshot:
        if len(data) > MAX_WIRE_BYTES:
            raise ValueError("Snapshot exceeds the transport byte limit.")

        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate snapshot JSON key.")
                result[key] = value
            return result

        def mapping(value: object) -> dict[str, Any]:
            if not isinstance(value, dict):
                raise TypeError("Invalid snapshot object.")
            return cast(dict[str, Any], value)

        def collection(value: object) -> list[Any]:
            if not isinstance(value, list):
                raise TypeError("Invalid snapshot collection.")
            return cast(list[Any], value)

        payload = mapping(json.loads(data, object_pairs_hook=unique_object))
        if set(payload) != {"format", "directories", "excluded", "files", "sha256"}:
            raise ValueError("Invalid snapshot envelope.")
        if type(payload["format"]) is not int or payload["format"] != 1:
            raise ValueError("Unsupported snapshot format.")
        entries = collection(payload["files"])
        directories = collection(payload["directories"])
        excluded = collection(payload["excluded"])
        if len(entries) > MAX_FILES:
            raise ValueError("Too many snapshot files.")
        files: list[SnapshotFile] = []
        total = 0
        for item in entries:
            entry = mapping(item)
            if set(entry) != {"path", "bytes", "sha256", "content_b64"}:
                raise ValueError("Invalid snapshot file envelope.")
            if not isinstance(entry["content_b64"], str) or len(entry["content_b64"]) > (
                (MAX_FILE_BYTES + 2) // 3 * 4
            ):
                raise ValueError("Snapshot file exceeds the encoded byte limit.")
            content = base64.b64decode(entry["content_b64"], validate=True)
            total += len(content)
            if total > MAX_TOTAL_BYTES:
                raise ValueError("Snapshot exceeds the aggregate content limit.")
            checked_path(entry["path"])
            file = SnapshotFile(entry["path"], content)
            if type(entry["bytes"]) is not int or entry["bytes"] != len(content):
                raise ValueError("Snapshot file byte count mismatch.")
            if file.sha256 != entry["sha256"]:
                raise ValueError("Snapshot file content digest mismatch.")
            files.append(file)
        snapshot = cls(tuple(files), tuple(directories), tuple(excluded))
        if snapshot.sha256 != payload["sha256"]:
            raise ValueError("Snapshot manifest digest mismatch.")
        return snapshot


def capture_workspace(root: Path) -> WorkspaceSnapshot:
    """Copy bytes through verified handles; never truncate or mount the source tree."""
    from xaa.runtime.file_handles import open_for_read, opened_file_path

    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Snapshot source must be a directory.")
    files: list[SnapshotFile] = []
    directories: list[str] = []
    excluded: list[str] = []
    pending = [root]
    total = 0
    deadline = time.monotonic() + 5
    visited = 0
    while pending:
        directory = pending.pop()
        if not directory.resolve(strict=True).is_relative_to(root):
            raise PermissionError("Snapshot directory escaped the selected workspace.")
        with os.scandir(directory) as entries:
            for entry in entries:
                visited += 1
                if visited > 2048 or time.monotonic() > deadline:
                    raise ValueError(
                        "Snapshot enumeration limit reached; select a smaller project."
                    )
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                checked_path(relative)
                info = entry.stat(follow_symlinks=False)
                if entry.name in EXCLUDED_DIRECTORIES and stat.S_ISDIR(info.st_mode):
                    excluded.append(relative)
                    if len(excluded) > MAX_DIRECTORIES:
                        raise ValueError("Too many excluded snapshot directories.")
                    continue
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
                    raise PermissionError(
                        "Snapshots refuse symlinks, junctions and reparse points."
                    )
                if stat.S_ISDIR(info.st_mode):
                    directories.append(relative)
                    if len(directories) > MAX_DIRECTORIES:
                        raise ValueError("Snapshot directory count exceeded.")
                    pending.append(path)
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE_BYTES:
                    raise ValueError("Snapshot requires bounded regular files.")
                if len(files) >= MAX_FILES:
                    raise ValueError("Snapshot file count exceeded.")
                with open_for_read(path) as source:
                    if not opened_file_path(source).is_relative_to(root):
                        raise PermissionError("Opened snapshot file escaped the workspace.")
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ValueError("Opened snapshot source is not a regular file.")
                    content = source.read(min(MAX_FILE_BYTES, MAX_TOTAL_BYTES - total) + 1)
                    after = os.fstat(source.fileno())
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ) or len(content) != after.st_size:
                    raise ValueError("Snapshot file changed or exceeded the byte limits.")
                total += len(content)
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("Snapshot aggregate content limit exceeded.")
                files.append(SnapshotFile(relative, content))
    return WorkspaceSnapshot(tuple(files), tuple(directories), tuple(excluded))


def materialize_snapshot(snapshot: WorkspaceSnapshot, destination: Path) -> None:
    """Create a new private staging tree only; never merge into an existing project."""
    destination.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name in sorted(
        snapshot.directories, key=lambda value: (len(checked_path(value).parts), value)
    ):
        destination.joinpath(*checked_path(name).parts).mkdir(mode=0o700)
    for entry in snapshot.files:
        with destination.joinpath(*checked_path(entry.path).parts).open("xb") as target:
            target.write(entry.content)

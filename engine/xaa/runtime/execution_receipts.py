"""Private Linux-side receipts that fence duplicate and delayed sandbox launches."""

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

MAX_IDENTITIES = 4096
MAX_ACTIVE_EXECUTIONS = 2


def identity_value(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("Invalid execution receipt identity.")
    return value


class ExecutionReceipts:
    @property
    def active_limit(self) -> int:
        return MAX_ACTIVE_EXECUTIONS

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise PermissionError("Execution receipts need a private root-owned directory.")
        self._sync_directory(directory.parent)
        with self._file_lock(directory / "_admission.lock"):
            schema = directory / "_schema.json"
            if schema.exists():
                if (
                    schema.is_symlink()
                    or schema.stat().st_size > 64
                    or schema.read_bytes() != b'{"format":1}'
                ):
                    raise ValueError("Unsupported execution receipt schema.")
            else:
                self._exclusive(schema, b'{"format":1}')

    def path(self, identity: str, suffix: str) -> Path:
        if suffix not in {"lock", "claim.json", "outcome.json", "closed", "stop", "active"}:
            raise ValueError("Invalid receipt kind.")
        return self.directory / f"{identity_value(identity)}.{suffix}"

    def _sync_directory(self, directory: Path | None = None) -> None:
        descriptor = os.open(directory or self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def _file_lock(self, path: Path) -> Generator[None]:
        if os.name == "nt":
            raise RuntimeError("Execution receipt locks require the Linux manager.")
        import fcntl

        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
                raise PermissionError("Invalid execution lock file.")
            deadline = time.monotonic() + 6
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "Execution control is busy; reconcile before retrying."
                        ) from None
                    time.sleep(0.02)
            yield
        finally:
            os.close(descriptor)

    @contextmanager
    def locked(self, identity: str) -> Generator[None]:
        identity = identity_value(identity)
        with self._file_lock(self.directory / "_admission.lock"):
            identities: set[str] = set()
            with os.scandir(self.directory) as entries:
                for count, entry in enumerate(entries):
                    if count > MAX_IDENTITIES * 6:
                        raise RuntimeError(
                            "Execution receipt directory needs operator maintenance."
                        )
                    prefix = entry.name.split(".", 1)[0]
                    if re.fullmatch(r"[a-f0-9]{32}", prefix):
                        identities.add(prefix)
            if identity not in identities and len(identities) >= MAX_IDENTITIES:
                raise RuntimeError("Execution identity retention is full; no launch was permitted.")
            # Reserve an identity's quota slot before releasing the admission lock.
            descriptor = os.open(
                self.path(identity, "lock"),
                os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            os.close(descriptor)
            self._sync_directory()
        with self._file_lock(self.path(identity, "lock")):
            yield

    def _exclusive(self, path: Path, data: bytes) -> None:
        temporary = self.directory / f"_{path.name}.{uuid4().hex}"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as target:
                target.write(data)
                target.flush()
                os.fsync(target.fileno())
            # Linking publishes a complete record atomically and refuses replacement.
            os.link(temporary, path, follow_symlinks=False)
            self._sync_directory()
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _check_active(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size != 8
                or os.read(descriptor, 9) != b"reserved"
            ):
                raise PermissionError(
                    "Invalid execution admission marker; operator maintenance required."
                )
        finally:
            os.close(descriptor)

    def release_active(self, identity: str) -> None:
        """Caller holds the identity lock and has stopped its unit and removed staging."""
        with self._file_lock(self.directory / "_admission.lock"):
            path = self.path(identity, "active")
            if path.exists() or path.is_symlink():
                self._check_active(path)
                path.unlink()
                self._sync_directory()

    def marker(self, identity: str, *, stopped: bool = False) -> None:
        for suffix in ("closed", "stop") if stopped else ("closed",):
            target = self.path(identity, suffix)
            try:
                self._exclusive(target, b"sealed")
            except FileExistsError:
                if target.is_symlink() or target.read_bytes() != b"sealed":
                    raise PermissionError("Invalid execution closure marker.") from None

    def claim(self, identity: str, request: dict[str, Any]) -> None:
        if self.path(identity, "closed").exists() or self.path(identity, "closed").is_symlink():
            raise RuntimeError("Execution identity is closed; a delayed launch was refused.")
        record = {
            "format": 1,
            "execution_id": identity,
            "manager_pid": os.getpid(),
            "manager_boot_id": Path("/proc/sys/kernel/random/boot_id")
            .read_text(encoding="utf-8")
            .strip(),
            "manager_start": Path("/proc/self/stat")
            .read_text(encoding="utf-8")
            .rsplit(")", 1)[1]
            .split()[19],
            "request_sha256": hashlib.sha256(
                json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        with self._file_lock(self.directory / "_admission.lock"):
            active = list(self.directory.glob("*.active"))
            for path in active:
                identity_value(path.name.removesuffix(".active"))
                self._check_active(path)
            if len(active) >= MAX_ACTIVE_EXECUTIONS:
                raise RuntimeError(
                    "Private execution capacity is full; reconcile before another launch."
                )
            try:
                self._exclusive(
                    self.path(identity, "claim.json"), json.dumps(record).encode("utf-8")
                )
            except FileExistsError:
                raise RuntimeError(
                    "Execution identity was already used; reconcile instead of replaying."
                ) from None
            # A dead manager retains this slot until observed unit/staging cleanup.
            self._exclusive(self.path(identity, "active"), b"reserved")

    def finished(self, identity: str, result: dict[str, Any]) -> None:
        metadata = {
            key: value for key, value in result.items() if key not in {"stdout_b64", "stderr_b64"}
        }
        record = {"state": "finished" if result else "interrupted", "result": metadata or None}
        data = json.dumps(record).encode("utf-8")
        if len(data) > 8192:
            raise ValueError("Execution outcome metadata exceeds its limit.")
        self._exclusive(self.path(identity, "outcome.json"), data)

    def status(self, identity: str) -> dict[str, Any]:
        identity = identity_value(identity)
        result: dict[str, Any] = {
            "execution_id": identity,
            "closed": self.path(identity, "closed").exists()
            or self.path(identity, "closed").is_symlink(),
            "stop_requested": self.path(identity, "stop").exists(),
            "claim": None,
            "outcome": None,
            "admission_reserved": self.path(identity, "active").exists()
            or self.path(identity, "active").is_symlink(),
        }
        for kind, name in (("claim.json", "claim"), ("outcome.json", "outcome")):
            path = self.path(identity, kind)
            if path.exists():
                if path.is_symlink() or path.stat().st_size > 8192:
                    raise PermissionError("Invalid execution receipt file.")
                result[name] = json.loads(path.read_bytes())
        record = result["claim"]
        result["manager_alive"] = False
        if (
            record is not None
            and record.get("manager_boot_id")
            == Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        ):
            try:
                actual = (
                    Path("/proc", str(record["manager_pid"]), "stat")
                    .read_text(encoding="utf-8")
                    .rsplit(")", 1)[1]
                    .split()[19]
                )
                result["manager_alive"] = actual == record["manager_start"]
            except (FileNotFoundError, ProcessLookupError):
                pass
        return result

"""Safely install or upgrade the root-owned X.A.A. SSH execution manager on Linux."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Protocol, cast

RESOURCE_NAMES = (
    "wsl_manager.py",
    "workspace_snapshot.py",
    "sandbox_profile.py",
    "execution_receipts.py",
    "cgroup_gate.py",
    "procedure_environment_manager.py",
    "node_environment_manager.py",
)
INSTALLER_NAME = "install_ssh_manager.py"
ROOT = Path("/opt/xaa-ssh-manager")
RELEASES = ROOT / "releases"
CURRENT = ROOT / "current"
WRAPPER = Path("/usr/local/sbin/xaa-ssh-execution-manager")
MANAGER_LOCK = Path("/run/lock/xaa-ssh-manager.lock")
RECEIPTS = Path("/var/lib/xaa-execution-manager")
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 6 * 1024 * 1024
MANAGER_SHA = re.compile(r"^[a-f0-9]{64}$")


class _FcntlModule(Protocol):
    LOCK_EX: int
    LOCK_NB: int

    def flock(self, fd: int, operation: int) -> None: ...


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("The manager bundle manifest contains duplicate fields.")
        result[key] = value
    return result


def manager_digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("utf-8")).hexdigest()


def read_bundle(path: Path) -> tuple[str, dict[str, bytes]]:
    bundle = path.resolve(strict=True)
    info = bundle.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("The SSH manager bundle must be a regular file under 8 MiB.")
    expected_names = {"manifest.json", INSTALLER_NAME, *RESOURCE_NAMES}
    with zipfile.ZipFile(bundle) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or set(names) != expected_names:
            raise ValueError("The SSH manager bundle has an unexpected file inventory.")
        for entry in entries:
            file_type = (entry.external_attr >> 16) & 0o170000
            if (
                entry.is_dir()
                or entry.flag_bits & 0x1
                or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                or file_type not in {0, stat.S_IFREG}
                or entry.file_size > MAX_MEMBER_BYTES
                or entry.compress_size > MAX_MEMBER_BYTES
                or entry.file_size > max(1, entry.compress_size) * 100
            ):
                raise ValueError("The SSH manager bundle contains an unsafe archive member.")
        if sum(entry.file_size for entry in entries) > MAX_TOTAL_BYTES:
            raise ValueError("The SSH manager bundle exceeds its expanded size limit.")
        content = {name: archive.read(name) for name in expected_names}

    installer = Path(__file__).read_bytes()
    if len(content[INSTALLER_NAME]) > MAX_MEMBER_BYTES:
        raise ValueError("The bundled installer exceeds its size limit.")
    try:
        manifest_value: object = json.loads(
            content["manifest.json"].decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
        )
    except (UnicodeError, ValueError) as exc:
        raise ValueError("The SSH manager bundle manifest is invalid.") from exc
    if not isinstance(manifest_value, dict):
        raise TypeError("The SSH manager bundle manifest must be an object.")
    manifest = cast(dict[str, object], manifest_value)
    if set(manifest) != {"format_version", "manager_sha256", "installer_sha256", "resources"}:
        raise ValueError("The SSH manager bundle manifest has an invalid schema.")
    if type(manifest["format_version"]) is not int or manifest["format_version"] != 1:
        raise ValueError("The SSH manager bundle format is unsupported.")
    digest = manifest["manager_sha256"]
    if not isinstance(digest, str) or MANAGER_SHA.fullmatch(digest) is None:
        raise ValueError("The SSH manager bundle digest is invalid.")
    if manifest["installer_sha256"] != hashlib.sha256(installer).hexdigest():
        raise ValueError("Use the install script packaged with this SSH manager bundle.")
    hashes_value = manifest["resources"]
    if not isinstance(hashes_value, dict):
        raise TypeError("The SSH manager resource manifest must be an object.")
    hashes_value = cast(dict[str, object], hashes_value)
    if set(hashes_value) != set(RESOURCE_NAMES):
        raise ValueError("The SSH manager resource manifest is incomplete.")
    hashes: dict[str, str] = {}
    for name in RESOURCE_NAMES:
        expected = hashes_value[name]
        if not isinstance(expected, str) or MANAGER_SHA.fullmatch(expected) is None:
            raise ValueError("The SSH manager resource digest is malformed.")
        actual = hashlib.sha256(content[name]).hexdigest()
        if actual != expected:
            raise ValueError(f"SSH manager resource digest mismatch: {name}.")
        hashes[name] = actual
    if manager_digest(hashes) != digest:
        raise ValueError("The SSH manager digest does not match its resource list.")
    return digest, {name: content[name] for name in RESOURCE_NAMES}


def _require_root_directory(path: Path, *, create: bool = False) -> None:
    if create:
        try:
            path.mkdir(mode=0o755)
        except FileExistsError:
            pass
    info = path.lstat()
    mode = stat.S_IMODE(info.st_mode)
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or (mode & 0o022 and not mode & stat.S_ISVTX)
    ):
        raise PermissionError(f"Expected a root-owned, non-writable directory: {path}")


def _write_root_file(path: Path, data: bytes, mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, mode)
    try:
        fchown = getattr(os, "fchown", None)
        if not callable(fchown):
            raise OSError("The Linux SSH manager installer requires os.fchown.")
        cast(Any, fchown)(descriptor, 0, 0)
        os.fchmod(descriptor, mode)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _release_matches(path: Path, resources: dict[str, bytes]) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise PermissionError("Existing manager release is not a root-owned real directory.")
    entries = {entry.name for entry in path.iterdir()}
    if entries not in (set(RESOURCE_NAMES), {*RESOURCE_NAMES, "manifest.json"}):
        raise PermissionError("Existing manager release has an unexpected file inventory.")
    if "manifest.json" in entries:
        manifest_info = (path / "manifest.json").lstat()
        if not stat.S_ISREG(manifest_info.st_mode) or manifest_info.st_uid != 0:
            raise PermissionError("Existing manager release manifest is unsafe.")
    for name, content in resources.items():
        target = path / name
        file_info = target.lstat()
        if not stat.S_ISREG(file_info.st_mode) or file_info.st_uid != 0:
            raise PermissionError("Existing manager release contains an unsafe file.")
        if target.read_bytes() != content:
            raise PermissionError("Existing manager release contents do not match their digest.")
    return True


def _refuse_active_executions() -> None:
    try:
        info = RECEIPTS.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise PermissionError("Execution receipt storage is not a root-owned real directory.")
    if any(RECEIPTS.glob("*.active")):
        raise RuntimeError(
            "Refusing manager upgrade while execution leases exist; finish or recover them first."
        )


def _wrapper_contents() -> bytes:
    manager_directory = "/opt/xaa-ssh-manager/current"
    arguments = " ".join(f"{manager_directory}/{name}" for name in RESOURCE_NAMES)
    return (
        "#!/bin/sh\n"
        f"exec /usr/bin/flock --shared {MANAGER_LOCK.as_posix()} /usr/bin/env -i "
        f"PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3 -I "
        f"{arguments}\n"
    ).encode()


def _install_wrapper() -> None:
    expected = _wrapper_contents()
    try:
        info = WRAPPER.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
            raise PermissionError("The SSH manager wrapper path is not a root-owned regular file.")
        if WRAPPER.read_bytes() != expected:
            raise PermissionError(
                "The existing SSH manager wrapper differs from the fixed wrapper."
            )
        os.chmod(WRAPPER, 0o755)
        return
    temporary = WRAPPER.with_name(f".{WRAPPER.name}.{uuid.uuid4().hex}.tmp")
    _write_root_file(temporary, expected, 0o755)
    os.replace(temporary, WRAPPER)


def _current_digest() -> str | None:
    try:
        info = CURRENT.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISLNK(info.st_mode) or info.st_uid != 0:
        raise PermissionError("The SSH manager current pointer must be a root-owned symlink.")
    previous_value = os.readlink(CURRENT)
    match = re.fullmatch(r"releases/([a-f0-9]{64})", previous_value)
    if match is None:
        raise PermissionError("The SSH manager current pointer escapes its release directory.")
    previous = match.group(1)
    previous_path = RELEASES / previous
    if not previous_path.is_dir() or previous_path.is_symlink():
        raise PermissionError("The current SSH manager release is missing or unsafe.")
    return previous


def _switch_current(digest: str) -> str | None:
    target = f"releases/{digest}"
    previous = _current_digest()
    if previous == digest:
        return previous
    temporary = ROOT / f".current-{uuid.uuid4().hex}"
    os.symlink(target, temporary)
    chown = getattr(os, "chown", None)
    if not callable(chown):
        raise OSError("The Linux SSH manager installer requires os.chown.")
    cast(Any, chown)(temporary, 0, 0, follow_symlinks=False)
    os.replace(temporary, CURRENT)
    return previous


def _lock_manager_exclusive() -> int:
    fcntl = cast(_FcntlModule, importlib.import_module("fcntl"))

    _require_root_directory(Path("/run"))
    _require_root_directory(Path("/run/lock"))
    descriptor = os.open(
        MANAGER_LOCK,
        os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    fchown = getattr(os, "fchown", None)
    if not callable(fchown):
        raise OSError("The Linux SSH manager installer requires os.fchown.")
    cast(Any, fchown)(descriptor, 0, 0)
    os.fchmod(descriptor, 0o600)
    deadline = time.monotonic() + 180
    while True:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return descriptor
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(descriptor)
                raise TimeoutError(
                    "Timed out waiting for active SSH manager calls before switching releases."
                )
            time.sleep(0.1)


def install(bundle_path: Path) -> dict[str, object]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise PermissionError("Run the SSH manager installer as root on the Linux target host.")
    fcntl = cast(_FcntlModule, importlib.import_module("fcntl"))

    digest, resources = read_bundle(bundle_path)
    _require_root_directory(Path("/opt"))
    _require_root_directory(Path("/usr"))
    _require_root_directory(Path("/usr/local"))
    _require_root_directory(Path("/usr/local/sbin"), create=True)
    _require_root_directory(ROOT, create=True)
    _require_root_directory(RELEASES, create=True)

    lock_path = ROOT / ".install.lock"
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.fchown(lock_fd, 0, 0)
        os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        release = RELEASES / digest
        if not _release_matches(release, resources):
            _refuse_active_executions()
            staging = Path(tempfile.mkdtemp(prefix=f".{digest}.", dir=RELEASES))
            os.chown(staging, 0, 0)
            os.chmod(staging, 0o755)
            try:
                for name, data in resources.items():
                    _write_root_file(staging / name, data, 0o644)
                try:
                    os.rename(staging, release)
                except FileExistsError:
                    if not _release_matches(release, resources):
                        raise
            except BaseException:
                if staging.exists() and not staging.is_symlink():
                    shutil.rmtree(staging)
                raise
        manager_lock_fd = _lock_manager_exclusive()
        try:
            previous = _current_digest()
            if previous != digest:
                _refuse_active_executions()
            _install_wrapper()
            previous = _switch_current(digest)
        finally:
            os.close(manager_lock_fd)
        return {"manager_sha256": digest, "previous_manager_sha256": previous, "installed": True}
    finally:
        os.close(lock_fd)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: install_ssh_manager.py BUNDLE.zip")
    result = install(Path(sys.argv[1]))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

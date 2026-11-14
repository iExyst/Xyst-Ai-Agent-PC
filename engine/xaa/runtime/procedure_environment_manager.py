"""Install exact approved Python wheelhouses into immutable WSL environments.

This module is executed only by ``wsl_manager.py`` as the trusted root-side
manager. Package installation itself runs as the existing unprivileged sandbox
identity in a network-denied Bubblewrap namespace.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

ROOT = Path("/var/lib/xaa-procedure-environments")
MAX_WHEELHOUSE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_WHEEL_BYTES = 256 * 1024 * 1024
MAX_ENVIRONMENT_BYTES = 640 * 1024 * 1024
MAX_ENVIRONMENT_FILES = 20_000
MAX_WHEELS = 256
MAX_WHEEL_MEMBERS = 20_000
MAX_INSTALL_SECONDS = 120


class EnvironmentInUseError(RuntimeError):
    """An active command holds a shared lease on the environment."""


_INSTALL_SCRIPT = r"""
import json, pathlib, subprocess, sys
root = pathlib.Path('/environment')
temp = root / '.install-tmp'
temp.mkdir(mode=0o777)
import os
os.environ['TMPDIR'] = str(temp)
created = subprocess.run(
    ['/usr/bin/python3', '-I', '-m', 'venv', '--copies', str(root / 'venv')],
    capture_output=True, timeout=30)
if created.returncode:
    sys.stderr.buffer.write(created.stdout[-32768:] + created.stderr[-32768:])
    raise SystemExit(created.returncode)
lib64 = root / 'venv' / 'lib64'
if lib64.is_symlink():
    if lib64.resolve() != (root / 'venv' / 'lib').resolve():
        raise RuntimeError('The new virtual environment contains an unexpected library link.')
    lib64.unlink()
python = str(root / 'venv' / 'bin' / 'python')
bootstrapped = subprocess.run([python, '-I', '-m', 'ensurepip', '--upgrade'],
                              capture_output=True, timeout=30)
if bootstrapped.returncode:
    sys.stderr.buffer.write(bootstrapped.stdout[-32768:] + bootstrapped.stderr[-32768:])
    raise SystemExit(bootstrapped.returncode)
files = json.loads(sys.argv[1])
installed = subprocess.run(
    [python, '-I', '-m', 'pip', 'install', '--isolated', '--no-index',
     '--no-cache-dir', '--no-compile', '--only-binary=:all:', '--no-deps',
     *['/wheelhouse/' + name for name in files]],
    capture_output=True, timeout=75)
if installed.returncode:
    sys.stderr.buffer.write(installed.stdout[-32768:] + installed.stderr[-32768:])
    raise SystemExit(installed.returncode)
import shutil
shutil.rmtree(temp)
"""


def _stop_install_unit(unit: str) -> None:
    subprocess.run(
        ["/usr/bin/systemctl", "stop", unit], check=False, capture_output=True, timeout=5
    )
    subprocess.run(
        ["/usr/bin/systemctl", "reset-failed", unit],
        check=False,
        capture_output=True,
        timeout=5,
    )
    load_state = (
        subprocess.run(
            ["/usr/bin/systemctl", "show", unit, "--property=LoadState", "--value"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        .stdout.decode("utf-8", "replace")
        .strip()
    )
    if load_state == "not-found":
        return
    state = (
        subprocess.run(
            ["/usr/bin/systemctl", "show", unit, "--property=ActiveState", "--value"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        .stdout.decode("utf-8", "replace")
        .strip()
    )
    if state != "inactive":
        raise RuntimeError("Cannot confirm the exact dependency installer unit is inactive.")


def _digest(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        raise ValueError("Procedure environment identity is invalid.")
    return value


def _private_root() -> None:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = ROOT.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError("Procedure environment storage must be a private root directory.")


def _tree_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    total = 0
    count = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if info.st_uid != 0:
            raise PermissionError("Installed environment contains a file with an unexpected owner.")
        if stat.S_ISDIR(info.st_mode):
            kind = b"d"
            body = b""
        elif stat.S_ISREG(info.st_mode):
            kind = b"f"
            total += info.st_size
            count += 1
            if count > MAX_ENVIRONMENT_FILES or total > MAX_ENVIRONMENT_BYTES:
                raise ValueError("Installed environment exceeds its file or storage limit.")
            file_digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    file_digest.update(chunk)
            body = file_digest.digest()
        else:
            raise ValueError("Installed environment contains a link or special file.")
        digest.update(kind + relative.encode("utf-8") + b"\0")
        digest.update((stat.S_IMODE(info.st_mode) & 0o111).to_bytes(2, "big"))
        digest.update(info.st_size.to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest(), total, count


def _source_wheelhouse(path: object, expected_digest: str, *, require_mapped: bool = True) -> Path:
    if isinstance(path, Path):
        source = path
        raw_path = str(path)
    elif isinstance(path, str):
        source = Path(path)
        raw_path = path
    else:
        raise TypeError("Wheelhouse path must be a string or path.")
    if "\0" in raw_path:
        raise ValueError("Wheelhouse path is invalid.")
    if require_mapped and not raw_path.startswith("/mnt/"):
        raise ValueError("Wheelhouse must be in the mapped local application data volume.")
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Mapped wheelhouse is not a regular directory.")
    resolved = source.resolve(strict=True)
    if require_mapped and not resolved.is_relative_to(Path("/mnt")):
        raise ValueError("Mapped wheelhouse escaped the Windows data mount.")
    if not require_mapped and not resolved.is_relative_to(ROOT):
        raise ValueError("Copied wheelhouse escaped managed WSL staging.")
    manifest_path = source / "manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 256 * 1024
    ):
        raise ValueError("Wheelhouse manifest is missing or invalid.")
    manifest: object = json.loads(manifest_path.read_bytes())
    if not isinstance(manifest, dict):
        raise TypeError("Wheelhouse manifest must be an object.")
    manifest_value = cast(dict[str, Any], manifest)
    if manifest_value.get("environment_digest") != expected_digest:
        raise ValueError("Wheelhouse manifest does not match the requested environment.")
    raw_files = manifest_value.get("files")
    if not isinstance(raw_files, list):
        raise TypeError("Wheelhouse manifest wheel list must be an array.")
    raw_file_list = cast(list[object], raw_files)
    if not 1 <= len(raw_file_list) <= MAX_WHEELS:
        raise ValueError("Wheelhouse manifest has an invalid wheel list.")
    records: dict[str, tuple[int, str]] = {}
    for item in raw_file_list:
        if not isinstance(item, dict):
            raise TypeError("Wheelhouse manifest entry must be an object.")
        entry = cast(dict[str, Any], item)
        filename, size, digest = entry.get("filename"), entry.get("size_bytes"), entry.get("sha256")
        if (
            not isinstance(filename, str)
            or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,240}\.whl", filename)
            or type(size) is not int
            or not 1 <= size <= MAX_WHEELHOUSE_BYTES
            or not isinstance(digest, str)
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or filename in records
        ):
            raise ValueError("Wheelhouse manifest entry is invalid.")
        records[filename] = (size, digest)
    if sum(size for size, _ in records.values()) > MAX_WHEELHOUSE_BYTES:
        raise ValueError("Wheelhouse exceeds its aggregate size limit.")
    if {entry.name for entry in source.iterdir()} != set(records) | {"manifest.json"}:
        raise ValueError("Wheelhouse contains unexpected files.")
    expanded = 0
    members_seen = 0
    for filename, (size, expected_hash) in records.items():
        wheel = source / filename
        info = wheel.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size != size:
            raise ValueError("Wheelhouse contains a non-regular or wrong-sized wheel.")
        hasher = hashlib.sha256()
        with wheel.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        if hasher.hexdigest() != expected_hash:
            raise ValueError("Wheelhouse wheel hash does not match its manifest.")
        with zipfile.ZipFile(wheel) as archive:
            members = archive.infolist()
            members_seen += len(members)
            if members_seen > MAX_WHEEL_MEMBERS:
                raise ValueError("Wheelhouse contains too many archive members.")
            names: set[str] = set()
            for member in members:
                parts = member.filename.rstrip("/").split("/")
                mode = member.external_attr >> 16
                if (
                    not member.filename
                    or member.filename.startswith("/")
                    or "\\" in member.filename
                    or any(part in {"", ".", ".."} for part in parts)
                    or member.filename in names
                    or stat.S_ISLNK(mode)
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                    or member.flag_bits & 1
                ):
                    raise ValueError("Wheel archive contains an unsafe member path.")
                names.add(member.filename)
                expanded += member.file_size
                if expanded > MAX_EXPANDED_WHEEL_BYTES:
                    raise ValueError("Wheelhouse expands beyond the installation storage limit.")
    return resolved


def _freeze_tree(root: Path) -> None:
    change_owner = getattr(os, "chown", None)
    if not callable(change_owner):
        raise TypeError("WSL environment sealing requires ownership controls.")
    for path in root.rglob("*"):
        info = path.lstat()
        change_owner(path, 0, 0)
        if stat.S_ISDIR(info.st_mode):
            path.chmod(0o555)
        elif stat.S_ISREG(info.st_mode):
            path.chmod(0o555 if info.st_mode & 0o111 else 0o444)
        else:
            raise ValueError(
                "Installed environment contains a link or special file: "
                + path.relative_to(root).as_posix()
            )
    change_owner(root, 0, 0)
    root.chmod(0o555)


def install(request: dict[str, Any], syscall_filter: bytes, file_lock: Any) -> dict[str, Any]:
    if set(request) != {"action", "environment_digest", "lock_json", "wheelhouse_path"}:
        raise ValueError("Invalid procedure environment install request.")
    digest = _digest(request["environment_digest"])
    lock_json = request["lock_json"]
    if (
        not isinstance(lock_json, str)
        or len(lock_json.encode("ascii", errors="strict")) > 256 * 1024
    ):
        raise ValueError("Procedure dependency lock exceeds its manager limit.")
    lock_sha = hashlib.sha256(lock_json.encode("ascii")).hexdigest()
    if lock_sha != digest:
        raise ValueError("Procedure environment digest does not match its lock.")
    lock: object = json.loads(lock_json)
    if not isinstance(lock, dict):
        raise TypeError("Procedure dependency lock must be an object.")
    lock_value = cast(dict[str, Any], lock)
    if lock_value.get("schema_version") != 1:
        raise ValueError("Procedure dependency lock is invalid.")
    wheelhouse = _source_wheelhouse(request["wheelhouse_path"], digest)
    raw_wheels = lock_value.get("wheels")
    if not isinstance(raw_wheels, list):
        raise TypeError("Procedure lock wheel list must be an array.")
    wheel_records = cast(list[object], raw_wheels)
    if not 1 <= len(wheel_records) <= MAX_WHEELS:
        raise ValueError("Procedure lock has an invalid wheel list.")
    expected_files: dict[str, tuple[int, str]] = {}
    for item in wheel_records:
        if not isinstance(item, dict):
            raise TypeError("Procedure lock wheel record is invalid.")
        record = cast(dict[str, Any], item)
        filename, size, wheel_hash = (
            record.get("filename"),
            record.get("size_bytes"),
            record.get("sha256"),
        )
        if (
            not isinstance(filename, str)
            or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,240}\.whl", filename)
            or type(size) is not int
            or not isinstance(wheel_hash, str)
            or not re.fullmatch(r"[a-f0-9]{64}", wheel_hash)
            or filename in expected_files
        ):
            raise ValueError("Procedure lock wheel record is invalid.")
        expected_files[filename] = (size, wheel_hash)
    names = sorted(expected_files)
    manifest: object = json.loads((wheelhouse / "manifest.json").read_bytes())
    if not isinstance(manifest, dict):
        raise TypeError("Wheelhouse manifest must be an object.")
    manifest_value = cast(dict[str, Any], manifest)
    if not isinstance(manifest_value.get("files"), list):
        raise TypeError("Wheelhouse manifest entries must be a list.")
    manifest_names = sorted(
        str(cast(dict[str, Any], item).get("filename", ""))
        for item in cast(list[object], manifest_value["files"])
        if isinstance(item, dict)
    )
    if names != manifest_names:
        raise ValueError("Wheelhouse membership does not match the reviewed lock.")
    if manifest_value.get("lock_sha256") != lock_sha:
        raise ValueError("Wheelhouse manifest is not bound to the reviewed lock bytes.")
    manifest_files = cast(list[object], manifest_value["files"])
    actual_files = {
        cast(dict[str, Any], item)["filename"]: (
            cast(dict[str, Any], item)["size_bytes"],
            cast(dict[str, Any], item)["sha256"],
        )
        for item in manifest_files
        if isinstance(item, dict)
    }
    if actual_files != expected_files:
        raise ValueError("Wheelhouse hashes or sizes do not match the reviewed lock.")

    _private_root()
    target = ROOT / digest
    unit = f"xaa-procenv-{digest[:32]}.service"
    lock_path = ROOT / "_manager.lock"
    with file_lock(lock_path):
        if target.exists() or target.is_symlink():
            existing = status(digest, lock_sha)
            if existing["installed"]:
                return existing
            info = target.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
                raise PermissionError("Refusing unsafe interrupted environment staging.")
            _stop_install_unit(unit)
            shutil.rmtree(target)
        target.mkdir(mode=0o700)
        incoming = target / "wheelhouse"
        incoming.mkdir(mode=0o700)
        try:
            for filename in names:
                with (
                    (wheelhouse / filename).open("rb") as source,
                    (incoming / filename).open("xb") as destination,
                ):
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                    destination.flush()
                    os.fsync(destination.fileno())
                (incoming / filename).chmod(0o444)
            shutil.copyfile(wheelhouse / "manifest.json", incoming / "manifest.json")
            (incoming / "manifest.json").chmod(0o444)
            incoming.chmod(0o755)
            copied = _source_wheelhouse(incoming, digest, require_mapped=False)
            environment = target / "environment"
            environment.mkdir(mode=0o777)
            environment.chmod(0o777)
            bwrap = [
                "/usr/bin/bwrap",
                "--unshare-all",
                "--unshare-user",
                "--unshare-cgroup",
                "--unshare-ipc",
                "--unshare-pid",
                "--unshare-net",
                "--unshare-uts",
                "--disable-userns",
                "--assert-userns-disabled",
                "--uid",
                "65534",
                "--gid",
                "65534",
                "--cap-drop",
                "ALL",
                "--new-session",
                "--die-with-parent",
                "--clearenv",
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                "--setenv",
                "LANG",
                "C.UTF-8",
                "--setenv",
                "HOME",
                "/tmp",
                "--setenv",
                "PYTHONDONTWRITEBYTECODE",
                "1",
                "--ro-bind",
                "/usr",
                "/usr",
                "--symlink",
                "usr/bin",
                "/bin",
                "--symlink",
                "usr/lib",
                "/lib",
                "--symlink",
                "usr/lib64",
                "/lib64",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--ro-bind",
                str(copied),
                "/wheelhouse",
                "--bind",
                str(environment),
                "/environment",
                "--size",
                "33554432",
                "--tmpfs",
                "/tmp",
                "--remount-ro",
                "/",
                "--remount-ro",
                "/dev",
                "--chdir",
                "/tmp",
                "--seccomp",
                "0",
                "--",
                "/usr/bin/python3",
                "-I",
                "-c",
                _INSTALL_SCRIPT,
                json.dumps(names),
            ]
            command = [
                "/usr/bin/systemd-run",
                "--unit",
                unit,
                "--wait",
                "--pipe",
                "--expand-environment=no",
                "--property=MemoryMax=768M",
                "--property=MemorySwapMax=0",
                "--property=CPUQuota=50%",
                "--property=TasksMax=32",
                f"--property=RuntimeMaxSec={MAX_INSTALL_SECONDS}s",
                "--property=TimeoutStopSec=2s",
                "--property=KillMode=control-group",
                *bwrap,
            ]
            completed = subprocess.run(
                command,
                check=False,
                input=syscall_filter,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=MAX_INSTALL_SECONDS + 10,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
            if completed.returncode != 0:
                raise RuntimeError("Offline procedure dependency installation failed.")
            shutil.rmtree(incoming)
            _freeze_tree(environment)
            tree_hash, total, file_count = _tree_digest(environment)
            if total > MAX_ENVIRONMENT_BYTES:
                raise ValueError("Installed environment exceeds its storage limit.")
            record = {
                "format": 1,
                "environment_digest": digest,
                "lock_sha256": lock_sha,
                "skill_id": lock_value.get("skill_id"),
                "skill_version": lock_value.get("skill_version"),
                "source_sha256": lock_value.get("source_sha256"),
                "python_version": cast(dict[str, Any], lock_value.get("target", {}))
                .get("environment", {})
                .get("python_full_version"),
                "tree_sha256": tree_hash,
                "file_count": file_count,
                "installed_bytes": total,
            }
            manifest_path = target / "environment-manifest.json"
            temporary_manifest = target / f".manifest-{uuid.uuid4().hex}"
            with temporary_manifest.open("xb") as output:
                output.write(
                    json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii")
                )
                output.flush()
                os.fsync(output.fileno())
            temporary_manifest.chmod(0o444)
            os.link(temporary_manifest, manifest_path, follow_symlinks=False)
            temporary_manifest.unlink()
            descriptor_target = os.open(target, os.O_RDONLY)
            try:
                os.fsync(descriptor_target)
            finally:
                os.close(descriptor_target)
            descriptor_dir = os.open(ROOT, os.O_RDONLY)
            try:
                os.fsync(descriptor_dir)
            finally:
                os.close(descriptor_dir)
            return status(digest, lock_sha)
        except BaseException:
            _stop_install_unit(unit)
            if target.exists() and not status(digest)["installed"]:
                shutil.rmtree(target, ignore_errors=True)
            raise


def status(digest_value: object, lock_sha: str | None = None) -> dict[str, Any]:
    digest = _digest(digest_value)
    _private_root()
    target = ROOT / digest
    if target.is_symlink() or not target.is_dir():
        return {"environment_digest": digest, "installed": False}
    manifest_path = target / "environment-manifest.json"
    if not manifest_path.exists():
        return {"environment_digest": digest, "installed": False}
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 4096
    ):
        raise PermissionError("Installed environment manifest is invalid.")
    target_info = target.lstat()
    if not stat.S_ISDIR(target_info.st_mode) or target_info.st_uid != 0:
        raise PermissionError("Installed environment directory is unsafe.")
    if {item.name for item in target.iterdir()} != {
        "environment",
        "environment-manifest.json",
    }:
        raise ValueError("Installed environment directory contains unexpected files.")
    record: object = json.loads(manifest_path.read_bytes())
    if (
        not isinstance(record, dict)
        or cast(dict[str, Any], record).get("format") != 1
        or cast(dict[str, Any], record).get("environment_digest") != digest
        or (lock_sha is not None and cast(dict[str, Any], record).get("lock_sha256") != lock_sha)
    ):
        raise ValueError("Installed environment manifest does not match its lock.")
    record_value = cast(dict[str, Any], record)
    environment_path = target / "environment"
    environment_info = environment_path.lstat()
    if not stat.S_ISDIR(environment_info.st_mode) or environment_info.st_uid != 0:
        raise PermissionError("Installed environment tree is unsafe.")
    tree_hash, total, files = _tree_digest(environment_path)
    if (
        tree_hash != record_value.get("tree_sha256")
        or total != record_value.get("installed_bytes")
        or files != record_value.get("file_count")
        or total > MAX_ENVIRONMENT_BYTES
        or files > MAX_ENVIRONMENT_FILES
    ):
        raise ValueError("Installed environment failed its content integrity check.")
    return {"environment_digest": digest, "installed": True, **record_value}


def execution_path(digest_value: object, tree_sha256: object) -> Path:
    """Return the root-owned environment only after rechecking its complete tree."""
    digest = _digest(digest_value)
    if not isinstance(tree_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", tree_sha256):
        raise ValueError("Procedure environment tree identity is invalid.")
    observed = status(digest)
    if not observed.get("installed") or observed.get("tree_sha256") != tree_sha256:
        raise ValueError("Installed procedure environment is missing or changed.")
    if observed.get("python_version") != sys.version.split()[0]:
        raise ValueError("WSL Python changed after this dependency environment was installed.")
    return ROOT / digest / "environment" / "venv"


@contextmanager
def execution_lease(digest_value: object, tree_sha256: object, file_lock: Any):
    """Pin an immutable environment until the managed command is fully cleaned up."""
    digest = _digest(digest_value)
    with file_lock(ROOT / f"_{digest}.lease", shared=True):
        yield execution_path(digest, tree_sha256)


def remove(digest_value: object, file_lock: Any) -> dict[str, Any]:
    digest = _digest(digest_value)
    _private_root()
    target = ROOT / digest
    with file_lock(ROOT / "_manager.lock"):
        try:
            with file_lock(ROOT / f"_{digest}.lease", timeout=0):
                existing = status(digest)
                if not existing["installed"]:
                    if target.is_symlink():
                        raise PermissionError("Refusing to remove a linked environment target.")
                    if target.exists():
                        info = target.lstat()
                        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
                            raise PermissionError(
                                "Refusing to remove an unowned environment target."
                            )
                        shutil.rmtree(target)
                    return {"environment_digest": digest, "removed": True}
                for path in target.rglob("*"):
                    info = path.lstat()
                    if stat.S_ISDIR(info.st_mode):
                        path.chmod(0o700)
                    elif stat.S_ISREG(info.st_mode):
                        path.chmod(0o600)
                    else:
                        raise ValueError("Environment contains a link or special file.")
                target.chmod(0o700)
                shutil.rmtree(target)
                directory = os.open(ROOT, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except TimeoutError as exc:
            raise EnvironmentInUseError("An active procedure is using this environment.") from exc
    return {"environment_digest": digest, "removed": True}


def dispatch(request: dict[str, Any], syscall_filter: bytes, file_lock: Any) -> dict[str, Any]:
    if request.get("action") == "install":
        return install(request, syscall_filter, file_lock)
    if request.get("action") == "status" and set(request) == {"action", "environment_digest"}:
        return status(request["environment_digest"])
    if request.get("action") == "remove" and set(request) == {"action", "environment_digest"}:
        return remove(request["environment_digest"], file_lock)
    raise ValueError("Unsupported procedure environment operation.")


def error_code(error: Exception) -> str:
    if isinstance(error, EnvironmentInUseError):
        return "environment_in_use"
    if isinstance(error, PermissionError):
        return "storage_denied"
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "operation_timeout"
    if isinstance(error, ValueError):
        message = str(error).casefold()
        if "digest" in message or "lock" in message or "reviewed" in message:
            return "lock_mismatch"
        if "manifest" in message:
            return "wheelhouse_manifest_invalid"
        if "hash" in message or "byte size" in message or "wrong-sized" in message:
            return "wheel_artifact_invalid"
        if "archive" in message or "member" in message:
            return "wheel_archive_invalid"
        if "expand" in message or "storage limit" in message or "too many" in message:
            return "environment_limit_exceeded"
        if "unexpected files" in message or "membership" in message:
            return "wheelhouse_membership_invalid"
        return "invalid_environment"
    if isinstance(error, OSError):
        return "storage_failed"
    return "operation_failed"


def error_detail(error: Exception) -> str:
    """Return bounded local validation text without mapped filesystem paths."""
    if isinstance(error, OSError):
        return "The managed WSL operation could not access its private storage or system service."
    value = re.sub(r"/mnt/[^\s'\"]+", "<local-path>", str(error))
    return value[:240] or "The managed WSL operation failed."

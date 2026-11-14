"""Install owner-approved npm locks into immutable, network-denied WSL roots.

The root-side manager validates and stages the exact lock and tarballs. npm itself
runs as the sandbox identity inside a network-isolated Bubblewrap namespace.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

ROOT = Path("/var/lib/xaa-node-environments")
MAX_TARBALLS = 512
MAX_TARBALL_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_TREE_BYTES = 1536 * 1024 * 1024
MAX_TREE_FILES = 50_000
MAX_INSTALL_SECONDS = 240

_INSTALL_SCRIPT = r"""
import json, os, pathlib, subprocess, sys
import shutil
root = pathlib.Path('/environment')
project = root / 'project'
cache = root / 'npm-cache'
temp = root / '.install-tmp'
for path in (project, cache, temp): path.mkdir(mode=0o777)
shutil.copyfile('/source/package.json', project / 'package.json')
shutil.copyfile('/source/package-lock.json', project / 'package-lock.json')
os.environ['TMPDIR'] = str(temp)
expected_node, expected_npm, packages = json.loads(sys.argv[1])
def run(args, timeout=30):
    result = subprocess.run(args, cwd=project, capture_output=True, timeout=timeout)
    if result.returncode:
        sys.stderr.buffer.write(result.stdout[-16384:] + result.stderr[-16384:])
        raise SystemExit(result.returncode)
    return result.stdout.decode('utf-8', 'replace').strip()
if run(['/usr/bin/node', '--version']) != 'v' + expected_node.removeprefix('v'):
    raise RuntimeError('WSL Node version changed after dependency review.')
if run(['/usr/bin/npm', '--version']) != expected_npm:
    raise RuntimeError('WSL npm version changed after dependency review.')
for filename in packages:
    run(['/usr/bin/npm', 'cache', 'add', '/tarballs/' + filename,
         '--offline', '--ignore-scripts', '--cache', str(cache)], timeout=60)
run(['/usr/bin/npm', 'ci', '--offline', '--ignore-scripts', '--no-audit', '--no-fund',
     '--cache', str(cache), '--prefix', str(project)], timeout=180)
shutil.rmtree(temp)
"""


class EnvironmentInUseError(RuntimeError):
    """An active procedure holds this Node environment."""


def _digest(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("Node environment identity is invalid.")
    return value


def _private_root() -> None:
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = ROOT.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
        raise PermissionError("Node environment storage must be a private root directory.")


def _unique_json(raw: bytes, maximum: int) -> Any:
    if not raw or len(raw) > maximum:
        raise ValueError("npm source file exceeds its size limit.")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("npm source file contains duplicate JSON fields.")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=unique)


def validate_linked_package_lock(lock_doc: dict[str, Any], packages_raw: object) -> None:
    lockfile_version = lock_doc.get("lockfileVersion")
    if type(lockfile_version) is not int or lockfile_version not in {1, 2, 3}:
        raise ValueError("Linked npm manifest or lockfile is invalid.")
    if lockfile_version == 1:
        dependencies = lock_doc.get("dependencies")
        if not isinstance(dependencies, dict) or not isinstance(packages_raw, list):
            raise ValueError("Linked npm lock membership differs from the reviewed proposal.")
        locked: dict[str, dict[str, Any]] = {}
        for item in cast(list[object], packages_raw):
            if not isinstance(item, dict):
                raise TypeError("Reviewed npm v1 package record is invalid.")
            package_record = cast(dict[str, Any], item)
            package_path = package_record.get("package_path")
            if not isinstance(package_path, str) or package_path in locked:
                raise ValueError("Reviewed npm package path is invalid or duplicated.")
            locked[package_path] = package_record
        flattened: dict[str, dict[str, Any]] = {}
        pending: list[tuple[str, object, int]] = [("", dependencies, 0)]
        while pending:
            parent_path, value, depth = pending.pop()
            if depth > 64 or not isinstance(value, dict):
                raise ValueError("Linked npm v1 dependency tree is invalid.")
            entries = cast(dict[str, Any], value)
            if len(flattened) + len(entries) > MAX_TARBALLS:
                raise ValueError("Linked npm v1 dependency tree exceeds the package limit.")
            for name, record_value in entries.items():
                if (
                    not isinstance(record_value, dict)
                    or re.fullmatch(
                        r"(?:@[a-z0-9][a-z0-9._-]{0,127}/)?[a-z0-9][a-z0-9._-]{0,127}",
                        name,
                    )
                    is None
                ):
                    raise ValueError("Linked npm v1 dependency record is invalid.")
                record = cast(dict[str, Any], record_value)
                package_path = (
                    f"{parent_path}/node_modules/{name}" if parent_path else f"node_modules/{name}"
                )
                if len(package_path.encode("utf-8")) > 1024:
                    raise ValueError("Linked npm v1 package path exceeds its limit.")
                reviewed = locked.get(package_path)
                if (
                    reviewed is None
                    or reviewed.get("name") != name
                    or reviewed.get("version") != record.get("version")
                    or reviewed.get("resolved_url") != record.get("resolved")
                    or reviewed.get("integrity") != record.get("integrity")
                    or record.get("link") is True
                    or record.get("inBundle") is True
                    or not isinstance(record.get("requires", {}), dict)
                    or not isinstance(record.get("dependencies", {}), dict)
                ):
                    raise ValueError("Linked npm v1 package differs from the reviewed proposal.")
                flattened[package_path] = record
                nested = record.get("dependencies", {})
                if nested:
                    pending.append((package_path, cast(object, nested), depth + 1))
        if set(flattened) != set(locked):
            raise ValueError("Linked npm v1 lock membership differs from the reviewed proposal.")
        return
    package_entries = lock_doc.get("packages")
    if (
        not isinstance(packages_raw, list)
        or not isinstance(package_entries, dict)
        or len(cast(list[object], packages_raw)) + 1 != len(cast(dict[str, Any], package_entries))
    ):
        raise ValueError("Linked npm lock membership differs from the reviewed proposal.")


def _source_tarballs(path_value: object, lock: dict[str, Any]) -> tuple[Path, list[dict[str, Any]]]:
    if not isinstance(path_value, str) or "\0" in path_value or not path_value.startswith("/mnt/"):
        raise ValueError("npm tarballs must be in the mapped local application data volume.")
    source = Path(path_value)
    if (
        source.is_symlink()
        or not source.is_dir()
        or not source.resolve(strict=True).is_relative_to(Path("/mnt"))
    ):
        raise ValueError("Mapped npm tarball path is not a local regular directory.")
    manifest_path = source / "manifest.json"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or manifest_path.stat().st_size > 256 * 1024
    ):
        raise ValueError("npm tarball manifest is missing or invalid.")
    manifest_value = _unique_json(manifest_path.read_bytes(), 256 * 1024)
    if not isinstance(manifest_value, dict):
        raise TypeError("npm tarball manifest must be an object.")
    manifest = cast(dict[str, Any], manifest_value)
    lock_sha = hashlib.sha256(
        json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    digest = lock_sha
    if (
        manifest.get("schema_version") != 1
        or manifest.get("environment_digest") != digest
        or manifest.get("lock_sha256") != lock_sha
    ):
        raise ValueError("npm tarball manifest does not match the exact reviewed lock.")
    packages_value = lock.get("packages")
    files_value = manifest.get("files")
    if not isinstance(packages_value, list) or not isinstance(files_value, list):
        raise TypeError("npm tarball package and file lists must be arrays.")
    packages, files = cast(list[object], packages_value), cast(list[object], files_value)
    if not 1 <= len(packages) <= MAX_TARBALLS or len(files) != len(packages):
        raise ValueError("npm tarball set is incomplete or exceeds its package limit.")
    total = 0
    expanded = 0
    records: list[dict[str, Any]] = []
    expected_files = {"manifest.json"}
    for index, (package_raw, record_raw) in enumerate(zip(packages, files, strict=True)):
        if not isinstance(package_raw, dict) or not isinstance(record_raw, dict):
            raise TypeError("npm tarball record is invalid.")
        package, record = cast(dict[str, Any], package_raw), cast(dict[str, Any], record_raw)
        filename = f"{index:04d}.tgz"
        archive = source / filename
        if archive.is_symlink() or not archive.is_file():
            raise ValueError("npm tarball is missing or is not a regular file.")
        size = archive.stat().st_size
        expected_size, archive_sha = record.get("size_bytes"), record.get("archive_sha256")
        if (
            record.get("filename") != filename
            or record.get("package_path") != package.get("package_path")
            or record.get("integrity") != package.get("integrity")
            or record.get("name") != package.get("name")
            or record.get("version") != package.get("version")
            or type(expected_size) is not int
            or size != expected_size
            or not isinstance(archive_sha, str)
            or re.fullmatch(r"[a-f0-9]{64}", archive_sha) is None
        ):
            raise ValueError("npm tarball record does not match the exact reviewed package.")
        sha256 = hashlib.sha256()
        sha512 = hashlib.sha512()
        with archive.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                sha256.update(chunk)
                sha512.update(chunk)
        integrity = "sha512-" + base64.b64encode(sha512.digest()).decode("ascii")
        if not hmac.compare_digest(sha256.hexdigest(), archive_sha) or not hmac.compare_digest(
            integrity, str(package.get("integrity"))
        ):
            raise ValueError("npm tarball bytes do not match their SHA-512 lock integrity.")
        member_count, expanded_size = record.get("member_count"), record.get("expanded_size_bytes")
        if (
            type(member_count) is not int
            or not 1 <= member_count <= 20_000
            or type(expanded_size) is not int
            or expanded_size < 1
        ):
            raise ValueError("npm tarball archive limits are missing.")
        total += size
        expanded += expanded_size
        if total > MAX_TARBALL_BYTES or expanded > MAX_EXPANDED_BYTES:
            raise ValueError("npm tarball set exceeds its aggregate installation limit.")
        expected_files.add(filename)
        records.append({"filename": filename, "sha256": archive_sha, "size": size})
    if {item.name for item in source.iterdir()} != expected_files:
        raise ValueError("npm tarball directory contains unexpected files.")
    return source.resolve(strict=True), records


def _tree_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    total = 0
    count = 0
    resolved_root = root.resolve(strict=True)
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        if info.st_uid != 0:
            raise PermissionError("Installed Node environment has an unexpected file owner.")
        relative = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            kind, body = b"d", b""
        elif stat.S_ISREG(info.st_mode):
            kind = b"f"
            file_digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    file_digest.update(chunk)
            body = file_digest.digest()
            total += info.st_size
            count += 1
        elif stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            if (
                not target
                or target.startswith("/")
                or "\0" in target
                or not path.resolve(strict=True).is_relative_to(resolved_root)
            ):
                raise ValueError("Installed npm tree contains an unsafe symbolic link.")
            kind, body = b"l", target.encode("utf-8")
            total += len(body)
            count += 1
        else:
            raise ValueError("Installed Node environment contains a special file.")
        if count > MAX_TREE_FILES or total > MAX_TREE_BYTES:
            raise ValueError("Installed Node environment exceeds its tree limit.")
        digest.update(kind + relative.encode("utf-8") + b"\0")
        digest.update((stat.S_IMODE(info.st_mode) & 0o111).to_bytes(2, "big"))
        digest.update(info.st_size.to_bytes(8, "big"))
        digest.update(body)
    return digest.hexdigest(), total, count


def _seal_tree(root: Path) -> None:
    change_owner = getattr(os, "chown", None)
    if not callable(change_owner):
        raise TypeError("WSL environment sealing requires ownership controls.")
    change_owner = cast(Callable[..., None], change_owner)
    for path in root.rglob("*"):
        info = path.lstat()
        change_owner(path, 0, 0, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            path.chmod(0o555)
        elif stat.S_ISREG(info.st_mode):
            path.chmod(0o555 if info.st_mode & 0o111 else 0o444)
        elif not stat.S_ISLNK(info.st_mode):
            raise ValueError("Installed npm tree contains a special file.")
    change_owner(root, 0, 0)
    root.chmod(0o555)


def install(request: dict[str, Any], syscall_filter: bytes, file_lock: Any) -> dict[str, Any]:
    required = {
        "action",
        "environment_digest",
        "lock_json",
        "package_manifest_b64",
        "package_lock_b64",
        "tarball_path",
    }
    if set(request) != required:
        raise ValueError("Invalid Node environment install request.")
    digest = _digest(request["environment_digest"])
    lock_json = request["lock_json"]
    if not isinstance(lock_json, str) or len(lock_json.encode("utf-8")) > 256 * 1024:
        raise ValueError("npm lock exceeds its manager limit.")
    lock_value = _unique_json(lock_json.encode("utf-8"), 256 * 1024)
    if (
        not isinstance(lock_value, dict)
        or hashlib.sha256(
            json.dumps(lock_value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        != digest
    ):
        raise ValueError("Node environment digest does not match its canonical lock.")
    lock = cast(dict[str, Any], lock_value)
    try:
        package_manifest = base64.b64decode(request["package_manifest_b64"], validate=True)
        package_lock = base64.b64decode(request["package_lock_b64"], validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("npm source resource encoding is invalid.") from exc
    if hashlib.sha256(package_manifest).hexdigest() != lock.get(
        "package_manifest_sha256"
    ) or hashlib.sha256(package_lock).hexdigest() != lock.get("lockfile_sha256"):
        raise ValueError("Linked package resources do not match the reviewed lock hashes.")
    manifest_doc, lock_doc = (
        _unique_json(package_manifest, 1024 * 1024),
        _unique_json(package_lock, 2 * 1024 * 1024),
    )
    if not isinstance(manifest_doc, dict) or not isinstance(lock_doc, dict):
        raise TypeError("Linked npm manifest and lockfile must be objects.")
    manifest_doc, lock_doc = cast(dict[str, Any], manifest_doc), cast(dict[str, Any], lock_doc)
    packages_raw = lock.get("packages")
    validate_linked_package_lock(lock_doc, packages_raw)
    packages_raw = cast(list[object], packages_raw)
    if not isinstance(request["package_manifest_b64"], str) or not isinstance(
        request["package_lock_b64"], str
    ):
        raise TypeError("npm source resources are malformed.")
    source, tarballs = _source_tarballs(request["tarball_path"], lock)
    node_version, npm_version = lock.get("node_version"), lock.get("npm_version")
    if not isinstance(node_version, str) or not isinstance(npm_version, str):
        raise TypeError("Reviewed Node and npm versions are invalid.")
    _private_root()
    target = ROOT / digest
    unit = f"xaa-nodeenv-{digest[:32]}.service"
    with file_lock(ROOT / "_manager.lock"):
        if target.exists() or target.is_symlink():
            try:
                existing = status(digest)
            except (OSError, ValueError, TypeError):
                existing = {"installed": False}
            if existing.get("installed") is True:
                return existing
            _remove_owned_target(digest, unit, file_lock)
        target.mkdir(mode=0o700)
        environment = target / "environment"
        environment.mkdir(mode=0o777)
        tarball_stage = target / "tarballs"
        tarball_stage.mkdir(mode=0o700)
        try:
            for record in tarballs:
                source_file = source / record["filename"]
                staged = tarball_stage / record["filename"]
                with source_file.open("rb") as incoming, staged.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                staged.chmod(0o444)
                if (
                    staged.stat().st_size != record["size"]
                    or hashlib.sha256(staged.read_bytes()).hexdigest() != record["sha256"]
                ):
                    raise ValueError("Copied npm tarball failed its content hash check.")
            tarball_stage.chmod(0o555)
            source_root = target / "source"
            source_root.mkdir(mode=0o755)
            (source_root / "package.json").write_bytes(package_manifest)
            (source_root / "package-lock.json").write_bytes(package_lock)
            for path in source_root.iterdir():
                path.chmod(0o444)
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
                "npm_config_update_notifier",
                "false",
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
                str(source_root.resolve()),
                "/source",
                "--ro-bind",
                str(tarball_stage.resolve()),
                "/tarballs",
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
                json.dumps([node_version, npm_version, [item["filename"] for item in tarballs]]),
            ]
            run_as_sandbox = [
                "/usr/bin/systemd-run",
                "--unit",
                unit,
                "--wait",
                "--pipe",
                "--expand-environment=no",
                "--property=MemoryMax=1024M",
                "--property=MemorySwapMax=0",
                "--property=CPUQuota=50%",
                "--property=TasksMax=64",
                f"--property=RuntimeMaxSec={MAX_INSTALL_SECONDS}s",
                "--property=TimeoutStopSec=2s",
                "--property=KillMode=control-group",
                *bwrap,
            ]
            completed = subprocess.run(
                run_as_sandbox,
                input=syscall_filter,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=MAX_INSTALL_SECONDS + 10,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
            if completed.returncode:
                diagnostic = completed.stderr.decode("utf-8", "replace")[-4096:]
                diagnostic = re.sub(r"[/\\]mnt[/\\][^\s'\"]+", "<local-file>", diagnostic)
                diagnostic = re.sub(r"/environment/[^\s'\"]+", "<environment-file>", diagnostic)
                diagnostic = re.sub(r"\s+", " ", diagnostic).strip()
                raise RuntimeError(
                    "Offline npm install failed inside the network-denied installer"
                    + (": " + diagnostic[-200:] if diagnostic else ".")
                )
            shutil.rmtree(tarball_stage)
            shutil.rmtree(source_root)
            _seal_tree(environment)
            tree_hash, total, count = _tree_digest(environment)
            record = {
                "format": 1,
                "environment_digest": digest,
                "lock_sha256": digest,
                "skill_id": lock.get("skill_id"),
                "skill_version": lock.get("skill_version"),
                "source_sha256": lock.get("source_sha256"),
                "distribution": lock.get("distribution"),
                "node_version": node_version,
                "npm_version": npm_version,
                "tree_sha256": tree_hash,
                "file_count": count,
                "installed_bytes": total,
            }
            manifest_path = target / "environment-manifest.json"
            temporary = target / (".manifest-" + uuid.uuid4().hex)
            with temporary.open("xb") as output:
                output.write(json.dumps(record, sort_keys=True, separators=(",", ":")).encode())
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o444)
            os.link(temporary, manifest_path, follow_symlinks=False)
            temporary.unlink()
            return status(digest, digest)
        except BaseException:
            _stop_unit(unit)
            if target.exists() and not status(digest)["installed"]:
                _make_writable(target)
                shutil.rmtree(target, ignore_errors=True)
            raise


def _make_writable(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        try:
            path.chmod(0o700 if path.is_dir() else 0o600)
        except OSError:
            pass
    try:
        root.chmod(0o700)
    except OSError:
        pass


def _stop_unit(unit: str) -> None:
    subprocess.run(
        ["/usr/bin/systemctl", "stop", unit], check=False, capture_output=True, timeout=5
    )
    subprocess.run(
        ["/usr/bin/systemctl", "reset-failed", unit], check=False, capture_output=True, timeout=5
    )
    state = (
        subprocess.run(
            ["/usr/bin/systemctl", "show", unit, "--property=ActiveState", "--value"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        .stdout.decode()
        .strip()
    )
    if state != "inactive":
        raise RuntimeError("Cannot confirm the owned npm installer unit is inactive.")


def status(digest_value: object, lock_sha: str | None = None) -> dict[str, Any]:
    digest = _digest(digest_value)
    _private_root()
    target = ROOT / digest
    if target.is_symlink() or not target.is_dir():
        return {"environment_digest": digest, "installed": False}
    manifest = target / "environment-manifest.json"
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 4096:
        return {"environment_digest": digest, "installed": False}
    info = target.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != 0
        or {item.name for item in target.iterdir()} != {"environment", "environment-manifest.json"}
    ):
        raise PermissionError("Installed Node environment directory is unsafe.")
    record_value = _unique_json(manifest.read_bytes(), 4096)
    if not isinstance(record_value, dict):
        raise TypeError("Installed Node environment manifest must be an object.")
    record = cast(dict[str, Any], record_value)
    if (
        record.get("format") != 1
        or record.get("environment_digest") != digest
        or (lock_sha and record.get("lock_sha256") != lock_sha)
    ):
        raise ValueError("Installed Node environment manifest does not match its lock.")
    tree = target / "environment"
    tree_info = tree.lstat()
    if not stat.S_ISDIR(tree_info.st_mode) or tree_info.st_uid != 0:
        raise PermissionError("Installed Node environment tree is unsafe.")
    actual, total, count = _tree_digest(tree)
    if (
        actual != record.get("tree_sha256")
        or total != record.get("installed_bytes")
        or count != record.get("file_count")
    ):
        raise ValueError("Installed Node environment failed its content integrity check.")
    return {"environment_digest": digest, "installed": True, **record}


@contextmanager
def execution_lease(digest_value: object, tree_sha256: object, file_lock: Any):
    digest = _digest(digest_value)
    if not isinstance(tree_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", tree_sha256) is None:
        raise ValueError("Node environment tree identity is invalid.")
    with file_lock(ROOT / f"_{digest}.lease", shared=True):
        observed = status(digest)
        if not observed["installed"] or observed.get("tree_sha256") != tree_sha256:
            raise ValueError("Installed Node environment is missing or changed.")
        yield ROOT / digest / "environment" / "project" / "node_modules"


def _remove_owned_target(digest: str, unit: str, file_lock: Any) -> None:
    target = ROOT / digest
    if not target.exists() and not target.is_symlink():
        return
    try:
        with file_lock(ROOT / f"_{digest}.lease", timeout=0):
            info = target.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
                raise PermissionError("Refusing unsafe Node environment removal target.")
            _stop_unit(unit)
            _make_writable(target)
            shutil.rmtree(target)
    except TimeoutError as exc:
        raise EnvironmentInUseError("A running procedure is using this Node environment.") from exc


def remove(digest_value: object, file_lock: Any) -> dict[str, Any]:
    digest = _digest(digest_value)
    _private_root()
    unit = f"xaa-nodeenv-{digest[:32]}.service"
    with file_lock(ROOT / "_manager.lock"):
        _remove_owned_target(digest, unit, file_lock)
    return {"environment_digest": digest, "removed": True}


def dispatch(request: dict[str, Any], syscall_filter: bytes, file_lock: Any) -> dict[str, Any]:
    action = request.get("action")
    if action == "install_node":
        return install(request, syscall_filter, file_lock)
    if action == "status_node" and set(request) == {"action", "environment_digest"}:
        return status(request["environment_digest"])
    if action == "remove_node" and set(request) == {"action", "environment_digest"}:
        return remove(request["environment_digest"], file_lock)
    raise ValueError("Unsupported Node environment operation.")


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
        if "tarball" in message or "integrity" in message or "hash" in message:
            return "npm_artifact_invalid"
        if "limit" in message or "too many" in message:
            return "environment_limit_exceeded"
        return "invalid_environment"
    if isinstance(error, OSError):
        return "storage_failed"
    return "operation_failed"


def error_detail(error: Exception) -> str:
    if isinstance(error, OSError):
        return "The managed WSL operation could not access its private storage or system service."
    value = re.sub(r"/mnt/[^\s'\"]+", "<local-path>", str(error))
    return value[:240] or "The managed WSL operation failed."

"""Durable content-addressed storage for exact npm tarballs."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from xaa.extensions.npm_dependencies import (
    MAX_NPM_TARBALL_BYTES,
    MAX_NPM_TARBALL_MEMBERS,
    MAX_NPM_TARBALL_TOTAL_BYTES,
    NpmVerifiedTarball,
    ProcedureNpmDependencyLock,
    verify_npm_tarball,
)

_MANIFEST_NAME = "manifest.json"
_MANIFEST_LIMIT = 2 * 1024 * 1024


class NpmTarballStorageError(ValueError):
    """An npm tarball set could not be safely stored or verified."""


class ProcedureNpmTarballStore:
    """Persist immutable tarballs under the digest of their exact procedure lock."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise NpmTarballStorageError("npm tarball storage root is not a regular directory.")
        self.root = root.resolve(strict=True)

    def present(self, lock: ProcedureNpmDependencyLock) -> bool:
        try:
            self.verify(lock)
        except NpmTarballStorageError:
            return False
        return True

    def store_files(
        self, lock: ProcedureNpmDependencyLock, artifacts: Mapping[str, Path]
    ) -> dict[str, NpmVerifiedTarball]:
        """Verify, atomically commit, then read back the exact tarball set."""
        expected = {package.package_path: package for package in lock.packages}
        if set(artifacts) != set(expected):
            raise NpmTarballStorageError("Tarball files do not match the exact npm lock.")
        reports: dict[str, NpmVerifiedTarball] = {}
        total_size = 0
        try:
            for package_path, package in expected.items():
                source = artifacts[package_path]
                if source.is_symlink() or source.is_junction() or not source.is_file():
                    raise NpmTarballStorageError("An npm tarball source is not a regular file.")
                size = source.stat().st_size
                total_size += size
                if size <= 0 or size > MAX_NPM_TARBALL_BYTES:
                    raise NpmTarballStorageError("An npm tarball exceeds its size limit.")
                if total_size > MAX_NPM_TARBALL_TOTAL_BYTES:
                    raise NpmTarballStorageError("npm tarballs exceed their aggregate size limit.")
                reports[package_path] = verify_npm_tarball(package, source.read_bytes())
        except (OSError, ValueError, TypeError) as exc:
            if isinstance(exc, NpmTarballStorageError):
                raise
            raise NpmTarballStorageError("npm tarball files failed lock verification.") from exc

        target = self.root / lock.environment_digest()
        if target.exists():
            self.verify(lock)
            return reports

        stage = self.root / f".stage-{uuid.uuid4().hex}"
        stage.mkdir(mode=0o700)
        try:
            files: list[dict[str, Any]] = []
            for index, package in enumerate(lock.packages):
                filename = f"{index:04d}.tgz"
                source = artifacts[package.package_path]
                destination = stage / filename
                with source.open("rb") as input_file, destination.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
                    output_file.flush()
                    os.fsync(output_file.fileno())
                report = reports[package.package_path]
                files.append(
                    {
                        "package_path": package.package_path,
                        "filename": filename,
                        "size_bytes": report.archive_size_bytes,
                        "archive_sha256": report.archive_sha256,
                        "integrity": report.integrity,
                        "name": report.name,
                        "version": report.version,
                        "member_count": report.member_count,
                        "expanded_size_bytes": report.expanded_size_bytes,
                        "install_lifecycle_scripts": list(report.install_lifecycle_scripts),
                    }
                )
            manifest = {
                "schema_version": 1,
                "environment_digest": lock.environment_digest(),
                "lock_sha256": hashlib.sha256(lock.canonical_json().encode("utf-8")).hexdigest(),
                "files": files,
            }
            self._write_new_file(
                stage / _MANIFEST_NAME,
                json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
            try:
                os.replace(stage, target)
            except OSError:
                if not target.exists():
                    raise
                self.verify(lock)
        except NpmTarballStorageError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise NpmTarballStorageError("npm tarballs could not be durably committed.") from exc
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
        self.verify(lock)
        return reports

    def verify(self, lock: ProcedureNpmDependencyLock) -> dict[str, Path]:
        """Recheck manifest, SRI, identity, paths, and archive contents from durable storage."""
        directory = self.root / lock.environment_digest()
        if directory.is_symlink() or not directory.is_dir():
            raise NpmTarballStorageError("Verified npm tarballs are not available for this lock.")
        try:
            resolved = directory.resolve(strict=True)
            if not resolved.is_relative_to(self.root):
                raise NpmTarballStorageError("npm tarball storage escaped its managed root.")
            manifest_path = directory / _MANIFEST_NAME
            manifest_raw = self._read_file(manifest_path, _MANIFEST_LIMIT)
            manifest: object = json.loads(manifest_raw)
            if not isinstance(manifest, dict):
                raise NpmTarballStorageError("npm tarball manifest is invalid.")
            value = cast(dict[str, Any], manifest)
            if (
                value.get("schema_version") != 1
                or value.get("environment_digest") != lock.environment_digest()
                or value.get("lock_sha256")
                != hashlib.sha256(lock.canonical_json().encode("utf-8")).hexdigest()
                or not isinstance(value.get("files"), list)
            ):
                raise NpmTarballStorageError("npm tarball manifest does not match its lock.")
            files = cast(list[object], value["files"])
            if len(files) != len(lock.packages):
                raise NpmTarballStorageError("npm tarball manifest has an incomplete package set.")
            expected_names = {_MANIFEST_NAME}
            paths: dict[str, Path] = {}
            total_size = 0
            for index, (package, raw_record) in enumerate(zip(lock.packages, files, strict=True)):
                if not isinstance(raw_record, dict):
                    raise NpmTarballStorageError("npm tarball manifest record is invalid.")
                record = cast(dict[str, Any], raw_record)
                filename = f"{index:04d}.tgz"
                if (
                    record.get("package_path") != package.package_path
                    or record.get("filename") != filename
                    or record.get("integrity") != package.integrity
                    or record.get("name") != package.name
                    or record.get("version") != package.version
                ):
                    raise NpmTarballStorageError(
                        "npm tarball manifest entry does not match its lock."
                    )
                path = directory / filename
                if path.is_symlink() or path.is_junction() or not path.is_file():
                    raise NpmTarballStorageError("Stored npm tarball is not a regular file.")
                size = path.stat().st_size
                digest = self._sha256(path, MAX_NPM_TARBALL_BYTES)
                if (
                    type(record.get("size_bytes")) is not int
                    or record["size_bytes"] != size
                    or not isinstance(record.get("archive_sha256"), str)
                    or record["archive_sha256"] != digest
                    or not re_full_sha256(record["archive_sha256"])
                ):
                    raise NpmTarballStorageError(
                        "Stored npm tarball hash does not match its manifest."
                    )
                if (
                    type(record.get("member_count")) is not int
                    or not 1 <= record["member_count"] <= MAX_NPM_TARBALL_MEMBERS
                ):
                    raise NpmTarballStorageError("Stored npm tarball member count is invalid.")
                scripts = record.get("install_lifecycle_scripts")
                if not isinstance(scripts, list):
                    raise NpmTarballStorageError("Stored npm lifecycle report is invalid.")
                scripts = cast(list[object], scripts)
                if any(not isinstance(item, str) for item in scripts):
                    raise NpmTarballStorageError("Stored npm lifecycle report is invalid.")
                report = verify_npm_tarball(package, path.read_bytes())
                if (
                    report.archive_sha256 != digest
                    or report.archive_size_bytes != size
                    or report.member_count != record["member_count"]
                    or list(report.install_lifecycle_scripts) != scripts
                    or report.expanded_size_bytes != record.get("expanded_size_bytes")
                ):
                    raise NpmTarballStorageError("Stored npm tarball verification report changed.")
                total_size += size
                if total_size > MAX_NPM_TARBALL_TOTAL_BYTES:
                    raise NpmTarballStorageError(
                        "Stored npm tarballs exceed their aggregate limit."
                    )
                expected_names.add(filename)
                paths[package.package_path] = path
            if {item.name for item in directory.iterdir()} != expected_names:
                raise NpmTarballStorageError("npm tarball directory contains unexpected files.")
            return paths
        except NpmTarballStorageError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise NpmTarballStorageError(
                "Stored npm tarballs failed integrity verification."
            ) from exc

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        with path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())

    @staticmethod
    def _read_file(path: Path, maximum: int) -> bytes:
        if path.is_symlink() or path.is_junction() or not path.is_file():
            raise NpmTarballStorageError("npm tarball manifest is not a regular file.")
        if path.stat().st_size > maximum:
            raise NpmTarballStorageError("npm tarball manifest exceeds its size limit.")
        return path.read_bytes()

    @staticmethod
    def _sha256(path: Path, maximum: int) -> str:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > maximum:
                    raise NpmTarballStorageError("npm tarball exceeds its size limit.")
                digest.update(chunk)
        return digest.hexdigest()


def re_full_sha256(value: str) -> bool:
    """Check the manifest digest without accepting non-hexadecimal encodings."""
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)

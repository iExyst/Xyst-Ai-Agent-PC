"""Durable content-addressed storage for exact verified procedure wheelhouses."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any, cast

from xaa.extensions.procedure_dependencies import (
    MAX_ENVIRONMENT_BYTES,
    ProcedurePythonDependencyLock,
    verify_locked_wheel_bytes,
)

_MANIFEST_NAME = "manifest.json"
_MANIFEST_LIMIT = 256 * 1024


class WheelhouseStorageError(ValueError):
    """A wheelhouse could not be safely committed or read back."""


class ProcedureWheelhouseStore:
    """Atomically store and re-verify immutable wheels under their lock digest."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink() or not root.is_dir():
            raise WheelhouseStorageError("Wheelhouse storage root is not a regular directory.")
        self.root = root.resolve(strict=True)

    def store(
        self, lock: ProcedurePythonDependencyLock, artifacts: dict[str, bytes]
    ) -> dict[str, bytes]:
        """Verify, atomically persist, and read back the exact lock-matched wheel set."""
        try:
            verified = verify_locked_wheel_bytes(lock, artifacts)
        except ValueError as exc:
            raise WheelhouseStorageError(
                "Wheelhouse bytes do not match the dependency lock."
            ) from exc
        if sum(map(len, verified.values())) > MAX_ENVIRONMENT_BYTES:
            raise WheelhouseStorageError("Wheelhouse exceeds its aggregate storage limit.")

        environment_digest = lock.environment_digest()
        target = self.root / environment_digest
        if target.exists():
            existing = self.read(lock)
            if existing != verified:
                raise WheelhouseStorageError(
                    "Immutable wheelhouse identity already contains other bytes."
                )
            return existing

        stage = self.root / f".stage-{uuid.uuid4().hex}"
        stage.mkdir(mode=0o700)
        try:
            for filename, content in verified.items():
                self._write_new_file(stage / filename, content)
            manifest = self._manifest(lock, verified)
            self._write_new_file(stage / _MANIFEST_NAME, manifest)
            try:
                os.replace(stage, target)
            except OSError:
                # A concurrent identical preparation may have committed first.
                if not target.exists():
                    raise
                existing = self.read(lock)
                if existing != verified:
                    raise WheelhouseStorageError(
                        "Immutable wheelhouse identity already contains other bytes."
                    )
        except OSError as exc:
            raise WheelhouseStorageError("Wheelhouse could not be durably committed.") from exc
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
        return self.read(lock)

    def read(self, lock: ProcedurePythonDependencyLock) -> dict[str, bytes]:
        """Read and independently verify an immutable wheelhouse for one lock."""
        environment_digest = lock.environment_digest()
        directory = self.root / environment_digest
        if directory.is_symlink() or not directory.is_dir():
            raise WheelhouseStorageError("Verified wheelhouse is not available for this lock.")
        try:
            resolved_directory = directory.resolve(strict=True)
            if not resolved_directory.is_relative_to(self.root):
                raise WheelhouseStorageError("Wheelhouse escaped managed storage.")
            manifest_path = directory / _MANIFEST_NAME
            manifest_bytes = self._read_file(manifest_path, _MANIFEST_LIMIT)
            manifest: object = json.loads(manifest_bytes)
            if not isinstance(manifest, dict):
                raise WheelhouseStorageError("Wheelhouse manifest is invalid.")
            manifest_value = cast(dict[str, Any], manifest)
            if (
                manifest_value.get("schema_version") != 1
                or manifest_value.get("environment_digest") != environment_digest
                or manifest_value.get("lock_sha256")
                != hashlib.sha256(lock.canonical_json().encode("utf-8")).hexdigest()
                or not isinstance(manifest_value.get("files"), list)
            ):
                raise WheelhouseStorageError("Wheelhouse manifest does not match its lock.")
            expected = {wheel.filename: wheel for wheel in lock.wheels}
            records = cast(list[object], manifest_value["files"])
            actual: dict[str, tuple[int, str]] = {}
            for record in records:
                if not isinstance(record, dict):
                    raise WheelhouseStorageError("Wheelhouse manifest entry is invalid.")
                entry = cast(dict[str, Any], record)
                filename = entry.get("filename")
                size = entry.get("size_bytes")
                digest = entry.get("sha256")
                if (
                    not isinstance(filename, str)
                    or not isinstance(size, int)
                    or isinstance(size, bool)
                    or not isinstance(digest, str)
                    or filename in actual
                ):
                    raise WheelhouseStorageError("Wheelhouse manifest entry is invalid.")
                actual[filename] = (size, digest)
            if set(actual) != set(expected) or {name: actual[name] for name in sorted(actual)} != {
                name: (wheel.size_bytes, wheel.sha256) for name, wheel in expected.items()
            }:
                raise WheelhouseStorageError("Wheelhouse manifest files do not match the lock.")
            if {item.name for item in directory.iterdir()} != set(expected) | {_MANIFEST_NAME}:
                raise WheelhouseStorageError("Wheelhouse contains unexpected files.")

            artifacts = {
                filename: self._read_file(directory / filename, record.size_bytes)
                for filename, record in expected.items()
            }
            return verify_locked_wheel_bytes(lock, artifacts)
        except WheelhouseStorageError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise WheelhouseStorageError(
                "Stored wheelhouse failed integrity verification."
            ) from exc

    @staticmethod
    def _manifest(lock: ProcedurePythonDependencyLock, artifacts: dict[str, bytes]) -> bytes:
        files = [
            {
                "filename": wheel.filename,
                "size_bytes": wheel.size_bytes,
                "sha256": wheel.sha256,
            }
            for wheel in sorted(lock.wheels, key=lambda item: item.filename)
        ]
        if artifacts:
            actual = {
                filename: (len(content), hashlib.sha256(content).hexdigest())
                for filename, content in artifacts.items()
            }
            if actual != {item["filename"]: (item["size_bytes"], item["sha256"]) for item in files}:
                raise WheelhouseStorageError("Wheelhouse manifest inputs do not match the lock.")
        value = {
            "schema_version": 1,
            "environment_digest": lock.environment_digest(),
            "lock_sha256": hashlib.sha256(lock.canonical_json().encode("utf-8")).hexdigest(),
            "files": files,
        }
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")

    @staticmethod
    def _write_new_file(path: Path, content: bytes) -> None:
        if len(content) > MAX_ENVIRONMENT_BYTES:
            raise WheelhouseStorageError("Wheelhouse file exceeds its storage limit.")
        with path.open("xb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())

    def _read_file(self, path: Path, maximum: int) -> bytes:
        if path.is_symlink() or not path.is_file():
            raise WheelhouseStorageError("Wheelhouse contains a non-regular file.")
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise WheelhouseStorageError("Wheelhouse file escaped managed storage.")
        with path.open("rb") as source:
            content = source.read(maximum + 1)
        if len(content) > maximum:
            raise WheelhouseStorageError("Wheelhouse file exceeds its stored size limit.")
        return content

    def present(self, lock: ProcedurePythonDependencyLock) -> bool:
        """Whether the exact lock has a manifest and exact regular-file set on disk."""
        directory = self.root / lock.environment_digest()
        if directory.is_symlink() or not directory.is_dir():
            return False
        try:
            resolved = directory.resolve(strict=True)
            if not resolved.is_relative_to(self.root):
                return False
            expected_names = {wheel.filename for wheel in lock.wheels} | {_MANIFEST_NAME}
            if {item.name for item in directory.iterdir()} != expected_names:
                return False
            if any((directory / name).is_symlink() for name in expected_names):
                return False
            manifest_value: object = json.loads(
                self._read_file(directory / _MANIFEST_NAME, _MANIFEST_LIMIT)
            )
            if not isinstance(manifest_value, dict):
                return False
            manifest = cast(dict[str, Any], manifest_value)
            return (
                manifest.get("schema_version") == 1
                and manifest.get("environment_digest") == lock.environment_digest()
                and manifest.get("lock_sha256")
                == hashlib.sha256(lock.canonical_json().encode("utf-8")).hexdigest()
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return False

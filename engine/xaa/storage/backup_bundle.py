"""Verified portable backups containing the database and managed artifact bytes."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import uuid4

from xaa.runtime.file_handles import open_for_read, opened_file_path
from xaa.storage.commands import command_artifact_root
from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunStore

FORMAT = "xaa-backup-v1"
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024


def create_backup_bundle(store: RunStore) -> Path:
    """Create an atomic ZIP backup and verify every stored artifact against SQLite."""
    directory = store.path.parent / "backups"
    directory.mkdir(exist_ok=True)
    destination = directory / f"{uuid4()}.xaa-backup"
    temporary = destination.with_suffix(".partial")
    database = store.backup()
    try:
        artifacts: list[tuple[str, bytes, str]] = []
        total = database.stat().st_size
        if total > MAX_BUNDLE_BYTES:
            raise ValueError("Backup exceeds the configured total size limit.")
        connection = sqlite3.connect(database)
        try:
            rows = connection.execute(
                "SELECT storage_key,size_bytes,sha256 FROM artifacts ORDER BY storage_key"
            ).fetchall()
        finally:
            connection.close()
        root = command_artifact_root(store, create=True)
        for key, expected_size, expected_digest in rows:
            if not isinstance(key, str) or Path(key).name != key or key in {"", ".", ".."}:
                raise ValueError("Database contains an unsafe artifact storage key.")
            candidate = root / key
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
                raise ValueError("Managed artifacts cannot be links or reparse points.")
            with open_for_read(candidate) as source:
                actual = opened_file_path(source)
                if not actual.is_relative_to(root):
                    raise ValueError("Artifact escaped managed storage.")
                data = source.read(MAX_ARTIFACT_BYTES + 1)
            digest = hashlib.sha256(data).hexdigest()
            if len(data) != expected_size or digest != expected_digest:
                raise ValueError(f"Artifact {key} failed size or digest verification.")
            total += len(data)
            if len(data) > MAX_ARTIFACT_BYTES or total > MAX_BUNDLE_BYTES:
                raise ValueError("Backup exceeds the configured artifact or total size limit.")
            artifacts.append((key, data, digest))
        database_bytes = database.read_bytes()
        manifest = {
            "format": FORMAT,
            "schema_version": DATABASE_SCHEMA_VERSION,
            "database": {
                "path": "xaa.sqlite3",
                "size_bytes": len(database_bytes),
                "sha256": hashlib.sha256(database_bytes).hexdigest(),
            },
            "artifacts": [
                {"path": f"artifacts/{key}", "size_bytes": len(data), "sha256": digest}
                for key, data, digest in artifacts
            ],
        }
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
            archive.writestr("xaa.sqlite3", database_bytes)
            for key, data, _ in artifacts:
                archive.writestr(f"artifacts/{key}", data)
        with temporary.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Backup archive CRC verification failed.")
        os.replace(temporary, destination)
        return destination
    finally:
        database.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def restore_backup_bundle(bundle: Path, destination: Path) -> Path:
    """Restore a verified bundle to a new directory, refusing traversal/overwrite."""
    bundle = bundle.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError("Restore destination already exists; select a new data directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.parent / f".{uuid4()}.xaa-restore-partial"
    staging.mkdir()
    try:
        with zipfile.ZipFile(bundle) as archive:
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)) or "manifest.json" not in names:
                raise ValueError("Backup archive has duplicate entries or no manifest.")
            if (
                any(item.file_size < 0 or item.file_size > MAX_BUNDLE_BYTES for item in infos)
                or sum(item.file_size for item in infos) > MAX_BUNDLE_BYTES
            ):
                raise ValueError("Backup archive exceeds the restore size limit.")
            if archive.getinfo("manifest.json").file_size > 2 * 1024 * 1024:
                raise ValueError("Backup manifest exceeds the restore size limit.")
            manifest_value: object = json.loads(archive.read("manifest.json"))
            if not isinstance(manifest_value, dict):
                raise TypeError("Backup format is unsupported.")
            manifest_raw = cast(dict[str, Any], manifest_value)
            raw_artifacts = manifest_raw.get("artifacts")
            if (
                manifest_raw.get("format") != FORMAT
                or type(manifest_raw.get("schema_version")) is not int
                or not isinstance(raw_artifacts, list)
            ):
                raise ValueError("Backup format is unsupported.")
            raw_artifacts = cast(list[Any], raw_artifacts)
            manifest: dict[str, Any] = manifest_raw
            artifact_metadata: list[dict[str, Any]] = []
            for raw_item in raw_artifacts:
                if not isinstance(raw_item, dict):
                    raise TypeError("Backup manifest contains malformed artifact metadata.")
                item = cast(dict[str, Any], raw_item)
                path_value = item.get("path")
                if not isinstance(path_value, str):
                    raise TypeError("Backup manifest contains malformed artifact metadata.")
                item["path"] = path_value
                artifact_metadata.append(item)
            expected = {"manifest.json", "xaa.sqlite3"}
            expected.update(item["path"] for item in artifact_metadata)
            if set(names) != expected:
                raise ValueError("Backup archive entries do not match its manifest.")
            database = archive.read("xaa.sqlite3")
            raw_db_info: Any = manifest.get("database")
            if not isinstance(raw_db_info, dict):
                raise TypeError("Backup database metadata is malformed.")
            db_info = cast(dict[str, Any], raw_db_info)
            if (
                db_info.get("path") != "xaa.sqlite3"
                or len(database) != db_info.get("size_bytes")
                or hashlib.sha256(database).hexdigest() != db_info.get("sha256")
            ):
                raise ValueError("Backup database failed manifest verification.")
            schema_db = staging / "validate.sqlite3"
            schema_db.write_bytes(database)
            connection = sqlite3.connect(schema_db.as_uri() + "?mode=ro", uri=True)
            try:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup database integrity check failed.")
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("Backup database has invalid foreign-key references.")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in {1, DATABASE_SCHEMA_VERSION}:
                    raise ValueError("Backup database schema is unsupported.")
                if not {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }.issuperset({"runs", "run_events", "tool_calls", "artifacts"}):
                    raise ValueError("Backup database is missing required engine tables.")
                stored = {
                    row[0]: (row[1], row[2])
                    for row in connection.execute(
                        "SELECT storage_key,size_bytes,sha256 FROM artifacts"
                    )
                }
            finally:
                connection.close()
            artifact_root = staging / "artifacts"
            artifact_root.mkdir()
            seen: set[str] = set()
            for item in artifact_metadata:
                path = item.get("path")
                if not isinstance(path, str):
                    raise TypeError("Backup manifest contains malformed artifact metadata.")
                pure = PurePosixPath(path)
                if (
                    len(pure.parts) != 2
                    or pure.parts[0] != "artifacts"
                    or pure.name in {"", ".", ".."}
                ):
                    raise ValueError("Backup manifest contains an unsafe artifact path.")
                key = pure.name
                if "\\" in key or ":" in key or Path(key).name != key:
                    raise ValueError("Backup manifest contains an unsafe artifact filename.")
                data = archive.read(path)
                digest = hashlib.sha256(data).hexdigest()
                if (
                    key in seen
                    or len(data) > MAX_ARTIFACT_BYTES
                    or len(data) != item.get("size_bytes")
                    or digest != item.get("sha256")
                    or stored.get(key) != (len(data), digest)
                ):
                    raise ValueError(f"Backup artifact {key} failed verification.")
                seen.add(key)
                (artifact_root / key).write_bytes(data)
            if seen != set(stored):
                raise ValueError("Backup is missing one or more database-referenced artifacts.")
            (staging / "xaa.sqlite3").write_bytes(database)
            schema_db.unlink()
        os.rename(staging, destination)
        return destination / "xaa.sqlite3"
    finally:
        if staging.exists():
            import shutil

            shutil.rmtree(staging)

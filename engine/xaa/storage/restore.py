"""Offline restoration into a new database without overwriting existing user data."""

import os
import sqlite3
from pathlib import Path
from uuid import uuid4

from xaa.storage.runs import DATABASE_SCHEMA_VERSION


def restore_database(snapshot: Path, destination: Path) -> Path:
    snapshot = snapshot.resolve(strict=True)
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError("Restore destination already exists; select a new data directory.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f".{uuid4()}.restore-partial"
    source = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
    try:
        if source.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Snapshot integrity check failed.")
        schema_version = source.execute("PRAGMA user_version").fetchone()[0]
        if schema_version < 1 or schema_version > DATABASE_SCHEMA_VERSION:
            raise ValueError("Snapshot schema version is unsupported.")
        if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("Snapshot contains invalid foreign-key references.")
        tables = {
            row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not {"runs", "run_events", "tool_calls"}.issubset(tables):
            raise ValueError("Snapshot is missing required engine tables.")
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restored database integrity check failed.")
        finally:
            target.close()
        # Same-directory hard link publishes atomically and refuses an existing destination.
        os.link(temporary, destination)
    finally:
        source.close()
        temporary.unlink(missing_ok=True)
    return destination

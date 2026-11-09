"""Seed and verify disposable backup data for installed-package acceptance."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunStore


def seed(directory: Path, report_path: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / name).exists() for name in ("xaa.sqlite3", "artifacts", "backups")):
        raise FileExistsError(
            "The disposable backup fixture data path already contains X.A.A. data."
        )
    store = RunStore(directory / "xaa.sqlite3")
    run = store.create_run(
        objective="Installed package backup and restore acceptance",
        acceptance_criteria=["Restore the durable run and managed binary artifact"],
        workspace=None,
        mode="observe",
        model=None,
    )
    run_id = str(run["id"])
    lease = store.acquire_lease(run_id, "installed-backup-acceptance")
    if lease is None:
        raise RuntimeError("Could not acquire the disposable run lease.")
    payload = b"Installed X.A.A. backup fixture\n\x00\xff\n"
    artifact_key = "installed-acceptance.bin"
    artifact_root = directory / "artifacts"
    artifact_root.mkdir()
    (artifact_root / artifact_key).write_bytes(payload)
    artifact = store.register_artifact(
        run_id,
        lease,
        artifact_key,
        provenance="disposable installed-package acceptance fixture",
    )
    if not store.release_lease(run_id, lease):
        raise RuntimeError("Could not release the disposable run lease.")
    persisted_run = store.get_run(run_id)
    if persisted_run is None:
        raise RuntimeError("The disposable run was not persisted.")
    report: dict[str, Any] = {
        "fixture": "installed-package-backup-restore",
        "directory": str(directory.resolve()),
        "run": persisted_run,
        "events": store.list_events(run_id),
        "artifact": store.get_artifact(str(artifact["id"])),
        "artifact_key": artifact_key,
        "artifact_hex": payload.hex(),
        "artifact_sha256": hashlib.sha256(payload).hexdigest(),
    }
    # Model the last released pre-identity-link database layout. The fixture is
    # created with current code so its run and artifact are valid, then only the
    # schema-19..22 additions are removed before the predecessor/current upgrade.
    with sqlite3.connect(directory / "xaa.sqlite3") as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("DROP INDEX IF EXISTS channel_bindings_identity_link_idx")
        db.execute("ALTER TABLE channel_bindings DROP COLUMN identity_link_id")
        db.execute("DROP TABLE channel_identity_link_events")
        db.execute("DROP TABLE channel_identity_links")
        db.execute("DROP TABLE idempotent_run_requests")
        db.execute("ALTER TABLE memories DROP COLUMN source_uri")
        db.execute("PRAGMA user_version=18")
        db.commit()
        seeded_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
    if seeded_version != 18 or integrity != "ok" or foreign_keys:
        raise RuntimeError("Could not construct a valid schema-18 installed upgrade fixture.")
    report["seeded_schema_version"] = seeded_version
    report["schema_downgrade_integrity"] = integrity
    report["schema_downgrade_foreign_key_violations"] = len(foreign_keys)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def verify(directory: Path, fixture_path: Path, report_path: Path) -> None:
    fixture: dict[str, Any] = json.loads(fixture_path.read_text(encoding="utf-8"))
    store = RunStore(directory / "xaa.sqlite3")
    run_id = str(fixture["run"]["id"])
    run = store.get_run(run_id)
    events = store.list_events(run_id)
    artifact = store.get_artifact(str(fixture["artifact"]["id"]))
    artifact_key = str(fixture["artifact_key"])
    artifact_bytes = (directory / "artifacts" / artifact_key).read_bytes()
    database_uri = (directory / "xaa.sqlite3").resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as db:
        schema_version = int(db.execute("PRAGMA user_version").fetchone()[0])
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = db.execute("PRAGMA foreign_key_check").fetchall()
    checks = {
        "seeded_from_schema_18": fixture.get("seeded_schema_version") == 18,
        "current_schema_after_upgrade": schema_version == DATABASE_SCHEMA_VERSION,
        "run_matches": run == fixture["run"],
        "events_match": events == fixture["events"],
        "artifact_metadata_matches": artifact == fixture["artifact"],
        "artifact_bytes_match": artifact_bytes == bytes.fromhex(str(fixture["artifact_hex"])),
        "artifact_digest_matches": hashlib.sha256(artifact_bytes).hexdigest()
        == fixture["artifact_sha256"],
        "database_integrity_ok": integrity == "ok",
        "foreign_key_check_empty": not foreign_keys,
    }
    if not all(checks.values()):
        raise AssertionError(f"Installed restore verification failed: {checks}")
    result = {
        "passed": True,
        "restored_directory": str(directory.resolve()),
        "run_id": run_id,
        "event_count": len(events),
        "artifact_id": fixture["artifact"]["id"],
        "artifact_size_bytes": len(artifact_bytes),
        "artifact_sha256": hashlib.sha256(artifact_bytes).hexdigest(),
        "database_integrity": integrity,
        "foreign_key_violations": len(foreign_keys),
        "checks": checks,
    }
    report_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)
    seed_parser = subparsers.add_parser("seed")
    seed_parser.add_argument("--directory", type=Path, required=True)
    seed_parser.add_argument("--report", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--directory", type=Path, required=True)
    verify_parser.add_argument("--fixture", type=Path, required=True)
    verify_parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "seed":
        seed(args.directory, args.report)
    else:
        verify(args.directory, args.fixture, args.report)


if __name__ == "__main__":
    main()

"""Exercise the offline backup restore CLI across fresh Python processes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from xaa.storage.backup_bundle import create_backup_bundle
from xaa.storage.runs import RunStore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path(".acceptance"))
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="backup-restore-cli-", dir=output))
    source_root = root / "source"
    source_root.mkdir()
    store = RunStore(source_root / "xaa.sqlite3")
    run = store.create_run(
        objective="Disposable backup restore CLI acceptance",
        acceptance_criteria=["Restore this run and its registered artifact exactly"],
        workspace=None,
        mode="observe",
        model=None,
    )
    run_id = str(run["id"])
    persisted_run = store.get_run(run_id)
    if persisted_run is None:
        raise RuntimeError("Disposable acceptance run was not persisted.")
    owner = "backup-restore-acceptance"
    lease = store.acquire_lease(run_id, owner)
    if lease is None:
        raise RuntimeError("Could not acquire disposable acceptance run lease.")
    artifact_bytes = b"X.A.A. backup acceptance artifact\n\x00\xff\n"
    artifact_key = "acceptance-output.bin"
    artifact_root = source_root / "artifacts"
    artifact_root.mkdir()
    (artifact_root / artifact_key).write_bytes(artifact_bytes)
    artifact = store.register_artifact(
        run_id, lease, artifact_key, provenance="disposable acceptance fixture"
    )
    if not store.release_lease(run_id, lease):
        raise RuntimeError("Could not release disposable acceptance run lease.")
    source_events = store.list_events(run_id)
    (root / "source-snapshot.json").write_text(
        json.dumps({"run": persisted_run, "events": source_events, "artifact": artifact}, indent=2),
        encoding="utf-8",
    )
    bundle = create_backup_bundle(store)
    destination = root / "restored"

    restore = subprocess.run(
        [
            sys.executable,
            "-m",
            "xaa.cli",
            "restore-bundle",
            str(bundle),
            "--destination",
            str(destination),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if restore.returncode != 0:
        raise RuntimeError(f"restore-bundle CLI failed: {restore.stderr.strip()}")

    verifier = r"""
import hashlib, json, sqlite3, sys
from pathlib import Path
from xaa.storage.runs import RunStore
root = Path(sys.argv[1])
run_id, artifact_key = sys.argv[2], sys.argv[3]
store = RunStore(root / 'xaa.sqlite3')
artifact = store.list_artifacts(run_id)[0]
data = (root / 'artifacts' / artifact_key).read_bytes()
with sqlite3.connect(root / 'xaa.sqlite3') as db:
    integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
print(json.dumps({
  'run': store.get_run(run_id),
  'events': store.list_events(run_id),
  'artifact': artifact,
  'artifact_sha256': hashlib.sha256(data).hexdigest(),
  'artifact_hex': data.hex(),
  'database_integrity': integrity,
}))
"""
    verify = subprocess.run(
        [sys.executable, "-c", verifier, str(destination), run_id, artifact_key],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if verify.returncode != 0:
        raise RuntimeError(f"fresh-process verification failed: {verify.stderr.strip()}")
    actual: dict[str, Any] = json.loads(verify.stdout)
    (root / "restore-verification.json").write_text(json.dumps(actual, indent=2), encoding="utf-8")
    expected_digest = hashlib.sha256(artifact_bytes).hexdigest()
    mismatches = [
        name
        for name, passed in [
            ("run", actual["run"] == persisted_run),
            ("events", actual["events"] == source_events),
            ("artifact_id", actual["artifact"]["id"] == artifact["id"]),
            ("artifact_metadata_digest", actual["artifact"]["sha256"] == expected_digest),
            ("artifact_bytes_digest", actual["artifact_sha256"] == expected_digest),
            ("artifact_exact_bytes", bytes.fromhex(actual["artifact_hex"]) == artifact_bytes),
            ("database_integrity", actual["database_integrity"] == "ok"),
        ]
        if not passed
    ]
    if mismatches:
        raise AssertionError(
            f"Restore verification mismatches: {mismatches}; diagnostics retained in {root}"
        )

    report = {
        "acceptance": "offline backup restore CLI round trip",
        "passed": True,
        "restore_command": "python -m xaa.cli restore-bundle",
        "fresh_process_reopen": True,
        "run_id": run_id,
        "event_count": len(source_events),
        "artifact_id": artifact["id"],
        "artifact_size_bytes": len(artifact_bytes),
        "artifact_sha256": expected_digest,
        "database_integrity": actual["database_integrity"],
        "bundle": str(bundle),
        "restored_directory": str(destination),
        "restore_stdout": restore.stdout.strip(),
    }
    report_path = root / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + os.linesep, encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

# Data backup and offline restore

`POST /backups` (also `xaa backup`) creates a `.xaa-backup` bundle under the engine data directory's `backups` folder. The bundle contains an online SQLite snapshot plus every database-referenced managed artifact. The writer checks artifact size and SHA-256 against the snapshot before publishing the archive atomically.

To restore without overwriting existing data, stop X.A.A. and run:

```powershell
uv run xaa restore-bundle C:\path\to\backup.xaa-backup --destination C:\new-data\XAA
$env:XAA_DATA_DIR = 'C:\new-data'
```

The restore validates archive membership, database integrity and foreign keys, schema compatibility, and each artifact's size and digest before publishing a new directory containing `xaa.sqlite3` and `artifacts/`. The destination must not already exist. With `XAA_DATA_DIR=C:\new-data`, the engine uses `C:\new-data\XAA\xaa.sqlite3` and its adjacent `artifacts/`, matching the restored directory. The older `xaa restore` command remains available for database-only `.sqlite3` snapshots; it does not recover artifact contents.

Bundles contain conversation and task data, so store them with the same care as the application data directory. This procedure is an offline snapshot/restore operation, not a live migration, cloud backup, retention policy, or tested disaster-recovery guarantee. Keep an independent copy and periodically perform a restore into a new directory.

## Disposable CLI round-trip evidence

On 2026-11-06, `uv run python scripts/backup_restore_cli_acceptance.py --output .acceptance` created an isolated database and registered binary artifact, generated a bundle, restored it through the actual `xaa.cli restore-bundle` command in a child process, then reopened it in a second fresh Python process. The acceptance compared the saved run and ordered event records, artifact ID and SHA-256, exact artifact bytes, and `PRAGMA integrity_check`. All checks passed. The retained report and disposable source/bundle/restored directories are under `.acceptance/backup-restore-cli-j9h6882c/`.

This verifies the offline restore command and bundle content across process boundaries using synthetic data. Bundle creation in this diagnostic calls the storage bundle function directly; it does not exercise authenticated `POST /backups` / `xaa backup`, installed-package restore, user-data migration, or failure recovery on a real user data set.

The follow-up acceptance supersedes that earlier creation limitation for the CLI/API path: `.acceptance/backup-restore-cli-6ut2_m6u/report.json` records a temporary authenticated API process, real `xaa backup`, bundle path confinement, real `xaa restore-bundle`, and fresh-process database/artifact verification. It still uses synthetic data and the source Python runtime; installed-package restore, migration of an existing user profile, and disaster recovery remain open.

## Installed-package restore evidence

The installed Windows package now has a local end-to-end acceptance at `.acceptance/native-installed-backup-7bb87895bd2d42058abea3bce8e583fa/report.json`. It installed the unsigned package into a temporary directory, seeded a disposable app data profile, started the installed desktop and frozen engine, ran both backup and restore commands through the installed engine, and verified restored records and artifact bytes from a fresh Python process. SQLite integrity and foreign-key checks passed; shutdown, uninstall, and the data-preservation marker also passed. The installer workflow now includes this scenario in `scripts/installer_ci_smoke.ps1`; hosted evidence for that change is pending. This does not verify migration of an existing user profile or disaster recovery.

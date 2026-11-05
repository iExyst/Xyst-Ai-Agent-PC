# Data backup and offline restore

`POST /backups` (also `xaa backup`) creates a `.xaa-backup` bundle under the engine data directory's `backups` folder. The bundle contains an online SQLite snapshot plus every database-referenced managed artifact. The writer checks artifact size and SHA-256 against the snapshot before publishing the archive atomically.

To restore without overwriting existing data, stop X.A.A. and run:

```powershell
uv run xaa restore-bundle C:\path\to\backup.xaa-backup --destination C:\path\to\new-data-directory
```

The restore validates archive membership, database integrity and foreign keys, schema compatibility, and each artifact's size and digest before publishing a new directory containing `xaa.sqlite3` and `artifacts/`. The destination must not already exist. The older `xaa restore` command remains available for database-only `.sqlite3` snapshots; it does not recover artifact contents.

Bundles contain conversation and task data, so store them with the same care as the application data directory. This procedure is an offline snapshot/restore operation, not a live migration, cloud backup, retention policy, or tested disaster-recovery guarantee. Keep an independent copy and periodically perform a restore into a new directory.

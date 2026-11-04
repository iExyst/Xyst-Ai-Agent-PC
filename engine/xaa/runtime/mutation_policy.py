"""Application-owned destinations are never workspace mutation authority."""

import sys
from pathlib import Path, PureWindowsPath


def require_mutable_destination(destination: Path, database: Path) -> None:
    """Check actual opened destinations, or a pinned parent plus new filename."""
    target = destination.resolve()
    data = database.resolve()
    source_root = Path(__file__).resolve().parents[3]
    protected = [
        Path(__file__).resolve().parents[2],
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        data.parent / "artifacts",
        data.parent / "backups",
    ]
    if getattr(sys, "frozen", False):
        protected.append(Path(sys.executable).resolve().parent)
        extracted = getattr(sys, "_MEIPASS", None)
        if extracted:
            protected.append(Path(extracted).resolve())
    else:
        protected.append(source_root / "apps" / "desktop")
    database_files = {Path(str(data) + suffix) for suffix in ("", "-wal", "-shm", "-journal")}
    source_entry = (
        None if getattr(sys, "frozen", False) else source_root / "scripts" / "engine_entry.py"
    )
    if (
        target in database_files
        or target == source_entry
        or any(target.is_relative_to(location.resolve()) for location in protected)
    ):
        raise PermissionError(
            "Workspace tools cannot modify live application code or private state."
        )
    windows = PureWindowsPath(str(target))
    if windows.drive.casefold().startswith(("\\\\wsl$\\", "\\\\wsl.localhost\\")):
        tail = tuple(part.casefold() for part in windows.parts[1:])
        for private in (("var", "lib", "xaa-execution-manager"), ("tmp", "xaa-executions")):
            if tail[: len(private)] == private:
                raise PermissionError(
                    "Workspace tools cannot modify execution-manager private state."
                )
    elif sys.platform == "linux" and any(
        target.is_relative_to(location)
        for location in (Path("/var/lib/xaa-execution-manager"), Path("/tmp/xaa-executions"))
    ):
        raise PermissionError("Workspace tools cannot modify execution-manager private state.")

"""Build a self-contained Windows engine executable for Tauri's sidecar bundle."""

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from xaa.runtime.wsl_transport import RESOURCE_NAMES, profile_fingerprint


def verify_execution_resources(executable: Path) -> dict[str, str]:
    """Verify actual frozen archive bytes, not merely packager command arguments."""
    readers = importlib.import_module("PyInstaller.archive.readers")
    archive = readers.CArchiveReader(str(executable))
    entries: dict[str, Any] = archive.toc
    names = {name.replace("\\", "/"): name for name in entries}
    fingerprints: dict[str, str] = {}
    for name in RESOURCE_NAMES:
        member = names.get("xaa/runtime/" + name)
        if member is None:
            raise RuntimeError(f"Frozen engine is missing execution resource {name}.")
        data: bytes = archive.extract(member)
        expected = Path("engine/xaa/runtime") / name
        if data != expected.read_bytes():
            raise RuntimeError(f"Frozen execution resource differs from source: {name}.")
        fingerprints[name] = hashlib.sha256(data).hexdigest()
    return fingerprints


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows engine on Windows.")
    destination = Path("apps/desktop/src-tauri/binaries")
    destination.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--onefile",
            "--name",
            "xaa-engine-x86_64-pc-windows-msvc",
            "--distpath",
            str(destination),
            "--workpath",
            "build/pyinstaller",
            "--specpath",
            "build",
            "--collect-all",
            "uvicorn",
            "--collect-all",
            "lmstudio",
            "--collect-all",
            "playwright",
            "--collect-all",
            "acp",
            "--hidden-import",
            "xaa.cli",
            "--hidden-import",
            "keyring.backends.Windows",
            *[
                argument
                for name in RESOURCE_NAMES
                for argument in (
                    "--add-data",
                    f"{(Path('engine/xaa/runtime') / name).resolve()}:xaa/runtime",
                )
            ],
            "scripts/engine_entry.py",
        ],
        check=True,
    )
    executable = destination / "xaa-engine-x86_64-pc-windows-msvc.exe"
    resources = verify_execution_resources(executable)
    manifest = {
        "engine_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "profile_sha256": profile_fingerprint()[0],
        "resource_sha256": resources,
    }
    Path("build/execution-resources.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()

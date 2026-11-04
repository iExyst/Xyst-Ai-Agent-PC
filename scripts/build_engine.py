"""Build a self-contained Windows engine executable for Tauri's sidecar bundle."""

import subprocess
import sys
from pathlib import Path


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
            "--hidden-import",
            "keyring.backends.Windows",
            "scripts/engine_entry.py",
        ],
        check=True,
    )


if __name__ == "__main__":
    main()

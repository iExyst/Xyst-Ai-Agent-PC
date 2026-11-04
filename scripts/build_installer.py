"""Build and stage an unsigned per-user Windows NSIS installer."""

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows installer on Windows.")
    npm = shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("npm.cmd is required for building the installer.")
    started_ns = time.time_ns()
    subprocess.run([npm, "run", "build:desktop"], check=True)
    subprocess.run([sys.executable, "scripts/build_engine.py"], check=True)
    subprocess.run(
        [
            npm,
            "exec",
            "--",
            "tauri",
            "build",
            "--ci",
            "--config",
            "src-tauri/tauri.release.conf.json",
            "--bundles",
            "nsis",
            "--no-sign",
            "--no-binary-patching",
            "--",
            "--locked",
        ],
        cwd="apps/desktop",
        check=True,
    )
    config = json.loads(Path("apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    version, product_name = config["version"], config["productName"]
    filename = f"{product_name}_{version}_x64-setup.exe"
    if Path(filename).name != filename:
        raise RuntimeError("Installer filename must remain within the bundle directory.")
    source = Path("apps/desktop/src-tauri/target/release/bundle/nsis") / filename
    if not source.is_file() or source.stat().st_mtime_ns < started_ns:
        raise RuntimeError("The current-version NSIS installer was not produced by this build.")
    destination = Path("build/installer")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    shutil.copy2(source, target)
    binaries = {
        "installer": target,
        "desktop": Path("apps/desktop/src-tauri/target/release/xaa-desktop.exe"),
        "engine": Path("apps/desktop/src-tauri/binaries/xaa-engine-x86_64-pc-windows-msvc.exe"),
    }
    manifest = {
        "version": version,
        "target": "x86_64-pc-windows-msvc",
        "signed": False,
        "binary_patching": False,
        "artifacts": {
            name: {
                "filename": path.name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in binaries.items()
        },
    }
    native = Path("build/native-release")
    native.mkdir(parents=True, exist_ok=True)
    native_manifest: dict[str, str] = {}
    for name, source_file in {
        "xaa-desktop.exe": binaries["desktop"],
        "xaa-engine.exe": binaries["engine"],
    }.items():
        shutil.copy2(source_file, native / name)
        native_manifest[name] = hashlib.sha256((native / name).read_bytes()).hexdigest()
    (native / "manifest.json").write_text(json.dumps(native_manifest, indent=2), encoding="utf-8")
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Installer staged: {target.resolve()}")


if __name__ == "__main__":
    main()

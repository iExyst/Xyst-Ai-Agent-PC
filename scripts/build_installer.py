"""Build and stage an unsigned per-user Windows NSIS installer."""

import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def _installer_install_mode(config_path: Path) -> str:
    config: Any = json.loads(config_path.read_text(encoding="utf-8"))
    install_mode = config.get("bundle", {}).get("windows", {}).get("nsis", {}).get("installMode")
    if not isinstance(install_mode, str) or install_mode != "currentUser":
        raise RuntimeError("The Windows installer must use the currentUser install mode.")
    return install_mode


def _run_tauri_build(npm: str) -> None:
    command = [
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
    ]
    transient_http_errors = ("http status: 502", "http status: 503", "http status: 504")
    for attempt in range(3):
        process = subprocess.Popen(
            command,
            cwd="apps/desktop",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if process.stdout is None:
            raise RuntimeError("Tauri build output stream was not created.")
        output: list[str] = []
        for line in process.stdout:
            print(line, end="", flush=True)
            output.append(line)
        process.stdout.close()
        returncode = process.wait()
        if returncode == 0:
            return
        captured = "".join(output)
        lowered = captured.casefold()
        if attempt < 2 and any(error in lowered for error in transient_http_errors):
            delay = 5 * (attempt + 1)
            print(
                f"Tauri encountered a transient HTTP download failure; retrying installer build in {delay} seconds.",
                flush=True,
            )
            time.sleep(delay)
            continue
        raise subprocess.CalledProcessError(returncode, command, output=captured)
    raise AssertionError("Tauri build retry loop ended unexpectedly.")


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows installer on Windows.")
    npm = shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("npm.cmd is required for building the installer.")
    started_ns = time.time_ns()
    subprocess.run([npm, "run", "build:desktop"], check=True)
    subprocess.run([sys.executable, "scripts/build_engine.py"], check=True)
    _run_tauri_build(npm)
    config = json.loads(Path("apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    version, product_name = config["version"], config["productName"]
    install_mode = _installer_install_mode(Path("apps/desktop/src-tauri/tauri.release.conf.json"))
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
    notices = {
        name: Path(source)
        for name, source in {
            "LICENSE": "LICENSE",
            "THIRD_PARTY_NOTICES.md": "docs/releases/THIRD_PARTY_NOTICES.md",
            "dependency-inventory.md": "docs/releases/dependency-inventory.md",
            "release-notes-0.1.1.md": "docs/releases/0.1.1.md",
        }.items()
    }
    for path in notices.values():
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError(f"Required release notice is missing or empty: {path}")
    manifest = {
        "version": version,
        "target": "x86_64-pc-windows-msvc",
        "signed": False,
        "binary_patching": False,
        "install_mode": install_mode,
        "artifacts": {
            name: {
                "filename": path.name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in binaries.items()
        },
        "notices": {
            name: {
                "filename": path.name,
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for name, path in notices.items()
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

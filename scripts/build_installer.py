"""Build and stage a per-user Windows NSIS installer."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


def _installer_install_mode(config_path: Path) -> str:
    config: Any = json.loads(config_path.read_text(encoding="utf-8"))
    install_mode = config.get("bundle", {}).get("windows", {}).get("nsis", {}).get("installMode")
    if not isinstance(install_mode, str) or install_mode != "currentUser":
        raise RuntimeError("The Windows installer must use the currentUser install mode.")
    return install_mode


def _installer_target_dir() -> Path:
    """Keep release outputs isolated from open development or installed app processes."""
    configured = os.environ.get("CARGO_TARGET_DIR", "build/installer-target")
    return Path(configured).resolve()


def _run_tauri_build(npm: str, *, signing: bool = False) -> None:
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
        "--no-binary-patching",
    ]
    if not signing:
        command.append("--no-sign")
    if signing:
        powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if powershell is None:
            raise RuntimeError("PowerShell is required for configured Authenticode signing.")
        signer = Path("scripts/sign_windows_artifacts.ps1").resolve(strict=True)
        overlay = {
            "bundle": {
                "windows": {
                    "digestAlgorithm": "sha256",
                    "signCommand": {
                        "cmd": powershell,
                        "args": [
                            "-NoLogo",
                            "-NoProfile",
                            "-NonInteractive",
                            "-ExecutionPolicy",
                            "Bypass",
                            "-File",
                            str(signer),
                            "-Files",
                            "%1",
                        ],
                    },
                }
            }
        }
        with tempfile.TemporaryDirectory(prefix="xaa-tauri-signing-") as temporary:
            config = Path(temporary) / "signing.json"
            config.write_text(json.dumps(overlay), encoding="utf-8")
            command.extend(["--config", str(config)])
            command.extend(["--", "--locked"])
            _run_tauri_build_command(command)
        return
    command.extend(["--", "--locked"])
    _run_tauri_build_command(command)


def _run_tauri_build_command(command: list[str]) -> None:
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


def _signing_configured() -> bool:
    certificate = os.environ.get("XAA_SIGNING_CERTIFICATE")
    password_present = "XAA_SIGNING_PASSWORD" in os.environ
    if not certificate:
        if password_present:
            raise RuntimeError("XAA_SIGNING_PASSWORD requires XAA_SIGNING_CERTIFICATE.")
        return False
    if not password_present:
        raise RuntimeError("XAA_SIGNING_CERTIFICATE requires XAA_SIGNING_PASSWORD to be set.")
    certificate_path = Path(certificate).resolve(strict=True)
    if not certificate_path.is_file():
        raise RuntimeError("XAA_SIGNING_CERTIFICATE must point to a PFX file.")
    os.environ["XAA_SIGNING_CERTIFICATE"] = str(certificate_path)
    sign_tool = os.environ.get("XAA_SIGNTOOL")
    if sign_tool:
        sign_tool_path = Path(sign_tool).resolve(strict=True)
        if not sign_tool_path.is_file():
            raise RuntimeError("XAA_SIGNTOOL must point to signtool.exe.")
        os.environ["XAA_SIGNTOOL"] = str(sign_tool_path)
    return True


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows installer on Windows.")
    npm = shutil.which("npm.cmd")
    if npm is None:
        raise SystemExit("npm.cmd is required for building the installer.")
    signing = _signing_configured()
    started_ns = time.time_ns()
    target_dir = _installer_target_dir()
    os.environ["CARGO_TARGET_DIR"] = str(target_dir)
    subprocess.run([npm, "run", "build:desktop"], check=True)
    subprocess.run([sys.executable, "scripts/build_engine.py"], check=True)
    _run_tauri_build(npm, signing=signing)
    config = json.loads(Path("apps/desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    version, product_name = config["version"], config["productName"]
    install_mode = _installer_install_mode(Path("apps/desktop/src-tauri/tauri.release.conf.json"))
    filename = f"{product_name}_{version}_x64-setup.exe"
    if Path(filename).name != filename:
        raise RuntimeError("Installer filename must remain within the bundle directory.")
    source = target_dir / "release/bundle/nsis" / filename
    if not source.is_file() or source.stat().st_mtime_ns < started_ns:
        raise RuntimeError("The current-version NSIS installer was not produced by this build.")
    destination = Path("build/installer")
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / source.name
    shutil.copy2(source, target)
    binaries = {
        "installer": target,
        "desktop": target_dir / "release/xaa-desktop.exe",
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
        "signed": signing,
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

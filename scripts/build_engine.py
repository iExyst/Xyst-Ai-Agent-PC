"""Build a self-contained Windows engine executable for Tauri's sidecar bundle."""

import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
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


def metadata_copy_arguments(exported_requirements: str) -> list[str]:
    """Copy locked runtime dist-info so read-only metadata checks work in the frozen app."""
    names = {"xaa-engine"}
    for line in exported_requirements.splitlines():
        requirement_text = line.strip()
        if not requirement_text or requirement_text.startswith(("#", "-")):
            continue
        requirement = Requirement(requirement_text)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        names.add(canonicalize_name(requirement.name))
    return [argument for name in sorted(names) for argument in ("--copy-metadata", name)]


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Build the Windows engine on Windows.")
    destination = Path("apps/desktop/src-tauri/binaries")
    destination.mkdir(parents=True, exist_ok=True)
    runtime_requirements = subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--format",
            "requirements-txt",
            "--no-emit-project",
            "--no-annotate",
            "--no-hashes",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    metadata_arguments = metadata_copy_arguments(runtime_requirements)
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
            "pypdfium2",
            "--collect-all",
            "pypdfium2_raw",
            "--collect-all",
            "playwright",
            "--collect-all",
            "acp",
            *metadata_arguments,
            "--hidden-import",
            "xaa.cli",
            "--hidden-import",
            "xaa.extensions.pypi_resolver_worker",
            "--hidden-import",
            "xaa.extensions.pypi_wheel_worker",
            "--hidden-import",
            "xaa.extensions.npm_tarball_worker",
            "--hidden-import",
            "xaa.extensions.npm_lock_worker",
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

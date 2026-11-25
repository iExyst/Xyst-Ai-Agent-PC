"""Create a bounded, hash-manifested SSH execution manager bundle for Linux hosts."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from xaa.runtime.wsl_transport import RESOURCE_NAMES, profile_fingerprint

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_ssh_manager.py"
MAX_BUNDLE_BYTES = 8 * 1024 * 1024


def manager_digest(hashes: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode("utf-8")).hexdigest()


def write_bundle(bundle_path: Path, installer_path: Path) -> dict[str, object]:
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    installer_path.parent.mkdir(parents=True, exist_ok=True)
    if bundle_path.resolve() == installer_path.resolve():
        raise ValueError("Bundle and installer outputs must be different files.")
    if bundle_path.exists() or installer_path.exists():
        raise FileExistsError("Refusing to overwrite an existing SSH manager release artifact.")
    manager_sha256, resources = profile_fingerprint()
    resource_bytes = {path.name: path.read_bytes() for path in resources}
    if set(resource_bytes) != set(RESOURCE_NAMES):
        raise RuntimeError("The current Linux manager resource inventory is incomplete.")
    resource_sha256 = {
        name: hashlib.sha256(resource_bytes[name]).hexdigest() for name in RESOURCE_NAMES
    }
    if manager_digest(resource_sha256) != manager_sha256:
        raise RuntimeError("The Linux manager profile changed while packaging.")
    installer_bytes = INSTALLER.read_bytes()
    manifest = {
        "format_version": 1,
        "manager_sha256": manager_sha256,
        "installer_sha256": hashlib.sha256(installer_bytes).hexdigest(),
        "resources": resource_sha256,
    }
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    created: list[Path] = []
    try:
        with installer_path.open("xb") as stream:
            stream.write(installer_bytes)
        created.append(installer_path)
        with bundle_path.open("xb") as stream:
            created.append(bundle_path)
            with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", manifest_bytes)
                archive.writestr("install_ssh_manager.py", installer_bytes)
                for name in RESOURCE_NAMES:
                    archive.writestr(name, resource_bytes[name])
        if bundle_path.stat().st_size > MAX_BUNDLE_BYTES:
            raise ValueError("The SSH manager bundle exceeds 8 MiB.")
    except BaseException:
        for path in reversed(created):
            if path.is_file() and not path.is_symlink():
                path.unlink()
        raise
    return {
        "bundle": str(bundle_path.resolve()),
        "bundle_bytes": bundle_path.stat().st_size,
        "manager_sha256": manager_sha256,
        "installer": str(installer_path.resolve()),
        "installer_sha256": hashlib.sha256(installer_bytes).hexdigest(),
        "resource_sha256": resource_sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="new manager ZIP bundle")
    parser.add_argument(
        "--installer-output",
        type=Path,
        help="new standalone remote installer (default: beside the ZIP bundle)",
    )
    args = parser.parse_args()
    installer_path = args.installer_output or args.output.with_name("install_ssh_manager.py")
    print(json.dumps(write_bundle(args.output, installer_path), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()

"""Exercise the packaged npm lock worker with deterministic AppContainer fixtures."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from xaa.extensions.npm_lock_inspector import (
    NpmLockInspectionError,
    inspect_npm_package_lock_in_appcontainer,
)


def _fixture() -> tuple[bytes, bytes]:
    manifest = b'{"name":"fixture","dependencies":{"left-pad":"^1.3.0"}}'
    integrity = base64.b64encode(hashlib.sha512(b"fixture tarball").digest()).decode("ascii")
    lock = json.dumps(
        {
            "name": "fixture",
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "fixture", "dependencies": {"left-pad": "^1.3.0"}},
                "node_modules/left-pad": {
                    "version": "1.3.0",
                    "resolved": "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
                    "integrity": f"sha512-{integrity}",
                },
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return manifest, lock


async def _accept(engine: Path) -> dict[str, Any]:
    manifest, lockfile = _fixture()
    npmrc = b"legacy-peer-deps=true\ninstall-links=false\n"
    proposal = await inspect_npm_package_lock_in_appcontainer(
        manifest,
        lockfile,
        skill_id="12345678-1234-4234-8234-123456789abc",
        skill_version=1,
        source_sha256="a" * 64,
        distribution="Ubuntu-24.04",
        node_version="v22.2.0",
        npm_version="10.8.1",
        requirements=("left-pad@^1.3.0",),
        npmrc=npmrc,
        test_worker_executable=engine,
    )
    if (
        proposal.npmrc_sha256 != hashlib.sha256(npmrc).hexdigest()
        or proposal.legacy_peer_deps is not True
        or proposal.install_links is not False
    ):
        raise RuntimeError("The installed worker did not preserve reviewed npmrc settings.")

    try:
        await inspect_npm_package_lock_in_appcontainer(
            manifest,
            lockfile,
            skill_id="12345678-1234-4234-8234-123456789abc",
            skill_version=1,
            source_sha256="a" * 64,
            distribution="Ubuntu-24.04",
            node_version="v22.2.0",
            npm_version="10.8.1",
            requirements=("left-pad@^1.3.0",),
            npmrc=b"registry=https://example.invalid/\n",
            test_worker_executable=engine,
        )
    except NpmLockInspectionError:
        rejected_unsupported_setting = True
    else:
        rejected_unsupported_setting = False
    if not rejected_unsupported_setting:
        raise RuntimeError("The installed worker accepted an unsupported registry override.")

    return {
        "passed": True,
        "engine": str(engine),
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
        "npmrc_sha256": proposal.npmrc_sha256,
        "legacy_peer_deps": proposal.legacy_peer_deps,
        "install_links": proposal.install_links,
        "unsupported_registry_override_rejected": rejected_unsupported_setting,
        "network_registry_access": False,
        "model_inference_requested": False,
    }


async def _main_async(args: argparse.Namespace) -> None:
    if os.name != "nt":
        raise SystemExit("Packaged npm lock worker acceptance requires Windows AppContainer.")
    engine = args.engine.resolve(strict=True)
    if engine.suffix.casefold() != ".exe" or not engine.is_file():
        raise SystemExit("--engine must point to a frozen Windows engine executable.")
    report = await _accept(engine)
    if args.report is not None:
        report_path = args.report.resolve()
        acceptance_root = Path(".acceptance").resolve()
        if not report_path.is_relative_to(acceptance_root) or report_path.exists():
            raise SystemExit("--report must be a new file inside .acceptance.")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    main()

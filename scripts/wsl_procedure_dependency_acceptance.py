"""Exercise a local wheel-only dependency install inside the restricted WSL command sandbox.

This is an acceptance probe for the WSL bootstrap and offline install prerequisite. It does not
exercise X.A.A.'s not-yet-implemented persistent dependency resolver or installer lifecycle.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import hashlib
import io
import json
import time
from typing import Any, cast
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport

INSTALL_PROBE = """
import json, pathlib, subprocess, tempfile
root = pathlib.Path('/workspace')
with tempfile.TemporaryDirectory(prefix='xaa-private-env-') as directory:
    environment = pathlib.Path(directory) / 'venv'
    created = subprocess.run(['/usr/bin/python3', '-I', '-m', 'venv', str(environment)],
                             capture_output=True, text=True, timeout=12)
    assert created.returncode == 0, created.stderr
    pip_python = str(environment / 'bin/python')
    installed = subprocess.run(
        [pip_python, '-I', '-m', 'pip', 'install', '--isolated', '--no-index',
         '--no-cache-dir', '--only-binary=:all:', '--find-links', str(root / 'wheelhouse'),
         'xaa-top==1.0'], capture_output=True, text=True, timeout=12)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    imported = subprocess.run(
        [pip_python, '-I', '-c', 'import xaa_top, xaa_leaf; print(xaa_top.VALUE, xaa_leaf.VALUE)'],
        capture_output=True, text=True, timeout=5)
    assert imported.returncode == 0 and imported.stdout.strip() == 'top-ready leaf-ready', \
        imported.stdout + imported.stderr
    ordinary = subprocess.run(
        ['/usr/bin/python3', '-I', '-c', 'import xaa_top'],
        capture_output=True, text=True, timeout=5)
    assert ordinary.returncode != 0 and 'No module named' in ordinary.stderr, \
        ordinary.stdout + ordinary.stderr
    system_pip = subprocess.run(
        ['/usr/bin/python3', '-I', '-m', 'pip', '--version'],
        capture_output=True, text=True, timeout=5)
    assert system_pip.returncode != 0 and 'No module named pip' in system_pip.stderr, \
        system_pip.stdout + system_pip.stderr
print(json.dumps({'private_venv_bootstrap': True, 'offline_transitive_install': True,
                  'private_import': True, 'ordinary_interpreter_isolated': True,
                  'system_python_unchanged': True}))
"""


def build_wheel(name: str, value: str, requires_dist: str | None = None) -> tuple[str, bytes]:
    normalized = name.replace("-", "_")
    version = "1.0"
    dist_info = f"{normalized}-{version}.dist-info"
    files = {
        f"{normalized}/__init__.py": f"VALUE = {value!r}\n".encode(),
        f"{dist_info}/METADATA": (
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            + (f"Requires-Dist: {requires_dist}\n" if requires_dist else "")
            + "\n"
        ).encode(),
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: xaa-acceptance\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n\n"
        ),
    }
    record = io.StringIO()
    writer = csv.writer(record, lineterminator="\n")
    for path, content in sorted(files.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        writer.writerow((path, f"sha256={digest}", len(content)))
    writer.writerow((f"{dist_info}/RECORD", "", ""))
    files[f"{dist_info}/RECORD"] = record.getvalue().encode("utf-8")
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return f"{normalized}-{version}-py3-none-any.whl", output.getvalue()


def output_object(encoded: object) -> dict[str, Any]:
    if not isinstance(encoded, str) or len(encoded) > 87_384:
        raise ValueError("The WSL acceptance output is missing or exceeds its limit.")
    raw = base64.b64decode(encoded, validate=True)
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise TypeError("The WSL acceptance output must be a JSON object.")
    return cast(dict[str, Any], value)


async def run(distribution: str) -> dict[str, Any]:
    transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
    leaf_name, leaf_bytes = build_wheel("xaa-leaf", "leaf-ready")
    top_name, top_bytes = build_wheel("xaa-top", "top-ready", "xaa-leaf==1.0")
    snapshot = WorkspaceSnapshot(
        files=(
            SnapshotFile(f"wheelhouse/{leaf_name}", leaf_bytes),
            SnapshotFile(f"wheelhouse/{top_name}", top_bytes),
        ),
        directories=("wheelhouse",),
    )
    intent = CommandIntent(
        argv=["/usr/bin/python3", "-I", "-c", INSTALL_PROBE],
        max_seconds=30,
        execution_id=uuid4().hex,
        backend="wsl-bubblewrap-v1",
        distribution=distribution,
        profile_sha256=transport.profile_sha256,
        workspace="application-owned-procedure-dependency-acceptance",
    )
    result = await transport.run(intent, snapshot, deadline=time.monotonic() + 60)
    if (
        result.get("succeeded") is not True
        or result.get("exit_code") != 0
        or result.get("cleanup_confirmed") is not True
        or result.get("snapshot_sha256") != snapshot.sha256
        or result.get("workspace_changes_persisted") is not False
        or result.get("limits", {}).get("network") != "none"
    ):
        raise RuntimeError("Restricted WSL offline dependency installation failed.")
    observed = output_object(result.get("stdout_b64"))
    expected = {
        "private_venv_bootstrap": True,
        "offline_transitive_install": True,
        "private_import": True,
        "ordinary_interpreter_isolated": True,
        "system_python_unchanged": True,
    }
    if observed != expected:
        raise RuntimeError("Restricted WSL offline dependency checks returned unexpected results.")
    return {"distribution": distribution, "checks": observed, "cleanup_confirmed": True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.distribution)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

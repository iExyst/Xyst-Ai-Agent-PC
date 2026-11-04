"""Probe an explicitly selected existing WSL distro without enabling agent execution."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import uuid4

from xaa.runtime.workspace_snapshot import capture_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.distribution):
        raise ValueError("Specify an existing distribution name, not WSL command options.")
    if os.name != "nt":
        raise SystemExit("Launch this probe from the Windows workspace.")
    root = Path(".acceptance") / str(uuid4())
    root.mkdir(parents=True)
    source = root / "windows-project"
    source.mkdir()
    (source / "fixture.txt").write_text("workspace-fixture", encoding="utf-8")
    (source / "r\u00e9sum\u00e9.bin").write_bytes(b"\x00\xff\r\n")
    (source / "empty").mkdir()
    snapshot = capture_workspace(source)
    wsl = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe")

    def linux_path(path: Path) -> str:
        return (
            subprocess.run(
                [
                    wsl,
                    "--distribution",
                    args.distribution,
                    "--exec",
                    "/usr/bin/wslpath",
                    "-a",
                    "-u",
                    str(path.resolve()),
                ],
                check=True,
                capture_output=True,
                timeout=10,
                creationflags=0x08000000,
            )
            .stdout.decode("utf-8")
            .strip()
        )

    host_script = linux_path(Path("scripts/wsl_sandbox_probe_host.py"))
    codec = linux_path(Path("engine/xaa/runtime/workspace_snapshot.py"))
    invocation = [
        wsl,
        "--distribution",
        args.distribution,
        "--exec",
        "/usr/bin/python3",
        host_script,
        codec,
    ]
    malformed = json.loads(snapshot.to_bytes())
    malformed["files"][0]["path"] = "../escape"
    rejected = subprocess.run(
        invocation,
        check=False,
        input=json.dumps(malformed).encode("utf-8"),
        capture_output=True,
        timeout=10,
        creationflags=0x08000000,
    )
    (root / "malformed-snapshot-stderr.log").write_bytes(rejected.stderr)
    if rejected.returncode == 0 or b"Snapshot path has invalid" not in rejected.stderr:
        raise RuntimeError("Linux manager did not reject the malformed snapshot as required.")
    result = subprocess.run(
        invocation,
        check=False,
        input=snapshot.to_bytes(),
        capture_output=True,
        timeout=60,
        creationflags=0x08000000,
    )
    (root / "stdout.log").write_bytes(result.stdout)
    (root / "stderr.log").write_bytes(result.stderr)
    if result.returncode != 0:
        raise SystemExit(f"WSL isolation probe failed; inspect {root}")
    report = json.loads(result.stdout)
    if (
        report["snapshot_sha256"] != snapshot.sha256
        or capture_workspace(source).sha256 != snapshot.sha256
    ):
        raise RuntimeError("Transferred snapshot or original Windows project changed.")
    report["malformed_snapshot_rejected"] = True
    report["original_windows_project_unchanged"] = True
    report["distribution"] = args.distribution
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"WSL restricted environment probes passed: {root}")


if __name__ == "__main__":
    main()

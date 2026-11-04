"""Probe an explicitly selected existing WSL distro without enabling agent execution."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    args = parser.parse_args()
    if os.name != "nt":
        raise SystemExit("Launch this probe from the Windows workspace.")
    root = Path(".acceptance") / str(uuid4())
    root.mkdir(parents=True)
    code = Path("scripts/wsl_sandbox_probe_host.py").read_bytes()
    result = subprocess.run(
        [
            str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe"),
            "--distribution",
            args.distribution,
            "--exec",
            "/usr/bin/python3",
            "-",
        ],
        check=False,
        input=code,
        capture_output=True,
        timeout=60,
        creationflags=0x08000000,
    )
    (root / "stdout.log").write_bytes(result.stdout)
    (root / "stderr.log").write_bytes(result.stderr)
    if result.returncode != 0:
        raise SystemExit(f"WSL isolation probe failed; inspect {root}")
    report = json.loads(result.stdout)
    report["distribution"] = args.distribution
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"WSL restricted environment probes passed: {root}")


if __name__ == "__main__":
    main()

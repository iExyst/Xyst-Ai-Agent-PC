"""Verify the frozen engine exposes ACP with packaged application metadata."""

from __future__ import annotations

import argparse
import json
import subprocess
from importlib.metadata import version
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    args = parser.parse_args()
    expected_version = version("xaa-engine")
    result = subprocess.run(
        [str(args.engine.resolve()), "--cli", "acp", "--version"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    expected_output = f"Xyst AI Agent ACP {expected_version}"
    if result.returncode != 0 or result.stdout.strip() != expected_output:
        raise RuntimeError(
            "Frozen ACP version check failed: "
            f"exit={result.returncode}; stdout={result.stdout[-300:]!r}; "
            f"stderr={result.stderr[-1000:]!r}"
        )
    report: dict[str, Any] = {
        "passed": True,
        "engine": args.engine.name,
        "version": expected_version,
        "acp_cli_version_verified": True,
        "model_inference_requested": False,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

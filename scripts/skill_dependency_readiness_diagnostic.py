"""Verify imported skill Python requirements against the restricted WSL interpreter."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from xaa.runtime.execution_readiness import (
    DEPENDENCY_PROBE,
    ExecutionReadiness,
    SkillDependencyReadinessRequest,
)
from xaa.runtime.wsl_transport import profile_fingerprint

ROOT = Path(__file__).resolve().parents[1]
ABSENT_PACKAGE = "xaa-impossible-fixture-package-20261113"


async def check(distribution: str) -> dict[str, object]:
    if "importlib.metadata" not in DEPENDENCY_PROBE or "importlib.import_module" in (
        DEPENDENCY_PROBE
    ):
        raise AssertionError(
            "The fixed WSL probe must inspect metadata without importing packages."
        )
    requirements = ["python-apt>=2.7", "requests>=2.0", f"{ABSENT_PACKAGE}>=1"]
    result = await ExecutionReadiness().check_python_dependencies(
        SkillDependencyReadinessRequest(
            distribution=distribution,
            python_dependency_specs=requirements,
        )
    )
    by_requirement = {item.requirement: item for item in result.dependencies}
    if result.state != "ready" or result.python_version is None:
        raise RuntimeError("The selected WSL sandbox could not check Python dependency metadata.")
    if by_requirement["python-apt>=2.7"].status != "available":
        raise AssertionError(
            "The Ubuntu WSL fixture no longer has its declared python-apt package."
        )
    if by_requirement["requests>=2.0"].status != "available":
        raise AssertionError("The Ubuntu WSL fixture no longer has its declared requests package.")
    if by_requirement[f"{ABSENT_PACKAGE}>=1"].status != "missing":
        raise AssertionError("The deliberately absent package was not reported missing.")
    profile_sha256, _ = profile_fingerprint()
    return {
        "passed": True,
        "checked_at": result.checked_at.isoformat(),
        "distribution": distribution,
        "backend": "wsl-bubblewrap-v1",
        "profile_sha256": profile_sha256,
        "python_version": result.python_version,
        "dependencies": [item.model_dump(mode="json") for item in result.dependencies],
        "unverified_dependency_count": result.unverified_dependency_count,
        "probe": "/usr/bin/python3 -I",
        "package_code_imported": False,
        "package_code_executed": False,
        "package_install_attempted": False,
        "network_access": "denied",
        "project_workspace_used": False,
        "model_inference_requested": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    args = parser.parse_args()
    report = asyncio.run(check(args.distribution))
    report["recorded_at"] = datetime.now(UTC).isoformat()
    destination = ROOT / ".acceptance" / "skill-dependency-readiness" / uuid4().hex
    destination.mkdir(parents=True, exist_ok=False)
    report_path = destination / "report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"passed": True, "report": str(report_path), "dependencies": report["dependencies"]}
        )
    )


if __name__ == "__main__":
    main()

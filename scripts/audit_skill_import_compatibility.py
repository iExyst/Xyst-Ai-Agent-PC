"""Exercise X.A.A.'s bounded importer against the pinned Hermes skill packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, cast

from audit_execution_inventory import ROOT, check_reference
from xaa.extensions.agent_plugins import read_plugin_skill

INVENTORY = Path("docs/parity/skill-package-inventory.json")


def audit() -> tuple[int, list[tuple[str, str]]]:
    check_reference()
    inventory: dict[str, Any] = json.loads(INVENTORY.read_text(encoding="utf-8"))
    if inventory.get("reference_commit") != "1e208b280deb18f01d015b0acdf3ef3552d08039":
        raise ValueError("skill package inventory is not for the pinned Hermes commit")
    raw_packages = inventory.get("packages")
    if not isinstance(raw_packages, list):
        raise TypeError("skill package inventory has no package list")
    packages = cast(list[Any], raw_packages)
    failures: list[tuple[str, str]] = []
    for raw_item in packages:
        if not isinstance(raw_item, dict):
            failures.append(("<invalid inventory row>", "package row must be an object"))
            continue
        item = cast(dict[str, Any], raw_item)
        path = item.get("skill_path")
        package_name = item.get("name")
        if not isinstance(path, str) or not isinstance(package_name, str):
            failures.append((str(path), "inventory row has no skill path/name"))
            continue
        directory = ROOT / Path(path).parent
        try:
            read_plugin_skill(str(directory), package_name, package_name)
        except (OSError, RuntimeError, ValueError) as exc:
            failures.append((path, f"{type(exc).__name__}: {exc}"))
    return len(packages), failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit unsuccessfully if any pinned package cannot be imported",
    )
    args = parser.parse_args()
    total, failures = audit()
    print(f"Pinned Hermes skill import compatibility: {total - len(failures)}/{total} passed.")
    for path, message in failures:
        print(f"{path}: {message}")
    if args.check and failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

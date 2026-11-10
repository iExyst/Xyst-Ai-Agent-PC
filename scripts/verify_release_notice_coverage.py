"""Check that scoped locked third-party packages have notice references."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


TARGET = "x86_64-pc-windows-msvc"
PYTHON_PROJECTS = {"xaa-engine"}
RUST_PROJECTS = {"xaa-desktop"}


def python_key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def npm_key(name: str) -> str:
    return name


def cargo_key(name: str) -> str:
    return name.replace("_", "-").lower()


KEYS = {"Python": python_key, "npm": npm_key, "Rust/Cargo": cargo_key}


def read_inventory(path: Path) -> dict[str, set[tuple[str, str]]]:
    result = {ecosystem: set() for ecosystem in KEYS}
    row = re.compile(r"^\| (Python|npm|Rust/Cargo) \| (.+?) \| ([^|]+) \|")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = row.match(line)
        if match:
            ecosystem, name, version = match.groups()
            result[ecosystem].add((KEYS[ecosystem](name.strip()), version.strip()))
    return result


def read_notice_references(path: Path) -> dict[str, set[tuple[str, str]]]:
    """Read explicit name@version and name==version references from the bundle."""
    result = {ecosystem: set() for ecosystem in KEYS}
    text = path.read_text(encoding="utf-8")
    references = re.compile(r"(?<![\w./-])([@\w./+-]+?)(?:@|==)([0-9][\w.+!-]*)")
    for match in references.finditer(text):
        name, version = match.groups()
        for ecosystem, normalize in KEYS.items():
            result[ecosystem].add((normalize(name), version))
    return result


def run_json(command: list[str], *, cwd: Path) -> Any:
    executable = shutil.which(command[0])
    if executable is None:
        raise RuntimeError(f"Required command not found: {command[0]}")
    command = [executable, *command[1:]]
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        shell=executable.lower().endswith((".cmd", ".bat")),
    )
    if completed.returncode:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{stderr}"
        )
    try:
        return json.loads(completed.stdout.decode("utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Command returned invalid JSON: {' '.join(command)}") from error


def expected_python(root: Path) -> set[tuple[str, str]]:
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    return {
        (python_key(package["name"]), str(package["version"]))
        for package in lock.get("package", [])
        if python_key(package["name"]) not in PYTHON_PROJECTS
    }


def expected_npm(root: Path) -> set[tuple[str, str]]:
    tree = run_json(["npm", "ls", "--omit=dev", "--all", "--json"], cwd=root)
    packages: set[tuple[str, str]] = set()

    def visit(dependencies: dict[str, Any]) -> None:
        for name, data in dependencies.items():
            if not isinstance(data, dict) or not data.get("version"):
                raise RuntimeError(f"npm dependency has no resolved version: {name}")
            packages.add((npm_key(name), str(data["version"])))
            visit(data.get("dependencies", {}))

    visit(tree.get("dependencies", {}))
    return packages


def expected_cargo(root: Path) -> set[tuple[str, str]]:
    metadata = run_json(
        [
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--locked",
            "--filter-platform",
            TARGET,
        ],
        cwd=root / "apps" / "desktop" / "src-tauri",
    )
    packages_by_id = {package["id"]: package for package in metadata["packages"]}
    active_ids = {
        package_id
        for node in (metadata.get("resolve") or {}).get("nodes", [])
        for package_id in [node["id"]]
    }
    return {
        (cargo_key(package["name"]), str(package["version"]))
        for package_id in active_ids
        if (package := packages_by_id[package_id]).get("source")
        and cargo_key(package["name"]) not in RUST_PROJECTS
    }


def missing_inventory_rows(
    ecosystem: str,
    expected: set[tuple[str, str]],
    inventory: dict[str, set[tuple[str, str]]],
) -> set[tuple[str, str]]:
    return expected - inventory[ecosystem]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = read_inventory(root / "docs/releases/dependency-inventory.md")
    notices = read_notice_references(root / "docs/releases/THIRD_PARTY_NOTICES.md")
    expected = {
        "Python": expected_python(root),
        "npm": expected_npm(root),
        "Rust/Cargo": expected_cargo(root),
    }

    errors: list[str] = []
    for ecosystem, packages in expected.items():
        missing_rows = missing_inventory_rows(ecosystem, packages, inventory)
        missing_notices = packages - notices[ecosystem]
        if missing_rows:
            errors.append(
                f"{ecosystem}: {len(missing_rows)} scoped lock package(s) missing from inventory: "
                + ", ".join(f"{name}@{version}" for name, version in sorted(missing_rows))
            )
        if missing_notices:
            errors.append(
                f"{ecosystem}: {len(missing_notices)} scoped package(s) missing a notice reference: "
                + ", ".join(f"{name}@{version}" for name, version in sorted(missing_notices))
            )

    if errors:
        print("Release notice coverage check failed:", file=sys.stderr)
        print("\n".join(f"- {error}" for error in errors), file=sys.stderr)
        return 1
    print(
        "Release notice coverage references all scoped locked packages: "
        + ", ".join(f"{ecosystem}={len(packages)}" for ecosystem, packages in expected.items())
    )
    print("This check validates package references only; it does not verify license text or legal completeness.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

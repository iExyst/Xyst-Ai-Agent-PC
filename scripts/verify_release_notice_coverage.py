"""Check release dependency inventory and scoped third-party notice references."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, NotRequired, TypedDict, cast

TARGET = "x86_64-pc-windows-msvc"
PYTHON_PROJECTS = {"xaa-engine"}
RUST_PROJECTS = {"xaa-desktop"}


class NpmPackage(TypedDict):
    version: NotRequired[str]
    dependencies: NotRequired[dict[str, NpmPackage]]


class NpmTree(TypedDict):
    dependencies: NotRequired[dict[str, NpmPackage]]


class CargoPackage(TypedDict):
    id: str
    name: str
    version: str
    source: NotRequired[str | None]


class CargoNode(TypedDict):
    id: str


class CargoResolve(TypedDict):
    nodes: NotRequired[list[CargoNode]]


class CargoMetadata(TypedDict):
    packages: list[CargoPackage]
    resolve: NotRequired[CargoResolve | None]


def python_key(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def npm_key(name: str) -> str:
    return name


def cargo_key(name: str) -> str:
    return name.replace("_", "-").lower()


KEYS = {"Python": python_key, "npm": npm_key, "Rust/Cargo": cargo_key}


def read_inventory(path: Path) -> dict[str, set[tuple[str, str]]]:
    result: dict[str, set[tuple[str, str]]] = {ecosystem: set() for ecosystem in KEYS}
    row = re.compile(r"^\| (Python|npm|Rust/Cargo) \| (.+?) \| ([^|]+) \|")
    for line in path.read_text(encoding="utf-8").splitlines():
        match = row.match(line)
        if match:
            ecosystem, name, version = match.groups()
            result[ecosystem].add((KEYS[ecosystem](name.strip()), version.strip()))
    return result


def inventory_metadata_errors(path: Path) -> list[str]:
    """Reject package rows without a declared license expression or source."""
    row = re.compile(r"^\| (Python|npm|Rust/Cargo) \| (.+?) \| ([^|]+) \| ([^|]+) \| ([^|]+) \|")
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = row.match(line)
        if match is None:
            continue
        ecosystem, name, version, license_expression, source = match.groups()
        package = f"{ecosystem}: {name.strip()}@{version.strip()}"
        license_value = license_expression.strip()
        if not license_value or license_value.casefold() in {"unknown", "noassertion"}:
            errors.append(f"{package} has no declared license expression (line {line_number}).")
        if not source.strip() or source.strip() in {"—", "-"}:
            errors.append(f"{package} has no source reference (line {line_number}).")
    return errors


def project_license_inventory_errors(root: Path) -> list[str]:
    """Ensure the release table agrees with each first-party package manifest."""
    python_project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    rust_project = tomllib.loads(
        (root / "apps/desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    )["package"]
    npm_project = json.loads((root / "package.json").read_text(encoding="utf-8"))

    declared = [
        (
            "Python",
            str(python_project["name"]),
            str(python_project["version"]),
            _license_expression(python_project["license"], "pyproject.toml"),
        ),
        (
            "Rust/Cargo",
            str(rust_project["name"]),
            str(rust_project["version"]),
            _license_expression(rust_project["license"], "Cargo.toml"),
        ),
        (
            "npm",
            str(npm_project["name"]),
            str(npm_project["version"]),
            _license_expression(npm_project["license"], "package.json"),
        ),
    ]
    rows: dict[tuple[str, str, str], str] = {}
    pattern = re.compile(r"^\| (Python|npm|Rust/Cargo) \| (.+?) \| ([^|]+) \| ([^|]+) \|")
    inventory = (root / "docs/releases/dependency-inventory.md").read_text(encoding="utf-8")
    for line in inventory.splitlines():
        match = pattern.match(line)
        if match:
            ecosystem, name, version, license_expression = match.groups()
            rows[(ecosystem, name.strip(), version.strip())] = license_expression.strip()

    errors: list[str] = []
    license_lines = (root / "LICENSE").read_text(encoding="utf-8").lstrip().splitlines()
    license_heading = license_lines[0].strip() if license_lines else ""
    if license_heading != "MIT License":
        errors.append("Root LICENSE does not identify the configured MIT project license.")
    for ecosystem, name, version, expected_license in declared:
        key = (ecosystem, name, version)
        recorded = rows.get(key)
        if recorded is None:
            errors.append(
                f"{ecosystem}: first-party package {name}@{version} is missing from inventory."
            )
        elif recorded.split(maxsplit=1)[0] != expected_license:
            errors.append(
                f"{ecosystem}: {name}@{version} declares {expected_license}, "
                f"but inventory records {recorded}."
            )
    return errors


def _license_expression(value: object, source: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        text_value = cast(dict[str, object], value).get("text")
        if isinstance(text_value, str) and text_value.strip():
            return text_value.strip()
    raise RuntimeError(f"First-party license metadata is missing or unsupported in {source}.")


def read_notice_references(path: Path) -> dict[str, set[tuple[str, str]]]:
    """Read package/version pairs from explicit package or verified-source lists."""
    result: dict[str, set[tuple[str, str]]] = {ecosystem: set() for ecosystem in KEYS}
    package_line = re.compile(r"^\*\*Packages:\*\*\s*(.*)$")
    reference = re.compile(r"(?<![\w./-])([@\w./+-]+?)(?:@|==)([0-9][\w.+!-]*)")
    source_section = False
    for line in path.read_text(encoding="utf-8").splitlines():
        package_attribution = package_line.match(line)
        if package_attribution is not None:
            source_section = False
            values = reference.findall(package_attribution.group(1))
        elif line.startswith(("**Packages and source files:**", "**Verified source files:**")):
            source_section = True
            continue
        elif line.startswith(("**", "##")):
            source_section = False
            values = []
        elif source_section:
            source = re.match(r"^\s*-\s+`([^`]+)`", line)
            if source is not None:
                values = reference.findall(source.group(1))
            else:
                source_section = not line.strip()
                values = []
        else:
            values = []
        for name, version in values:
            for ecosystem, normalize in KEYS.items():
                result[ecosystem].add((normalize(name), version))
    return result


def count_unknown_additional_notice_placeholders(path: Path) -> int:
    """Count scanner placeholders that are not verbatim third-party notices."""
    text = path.read_text(encoding="utf-8")
    return len(re.findall(r"(?m)^Additional notice:\r?\nUNKNOWN\r?$", text))


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
            f"Command failed ({completed.returncode}): {' '.join(command)}\n{stderr}"
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


def expected_python_inventory(root: Path) -> set[tuple[str, str]]:
    """Return every locked Python package, including the first-party engine."""
    lock = tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))
    return {
        (python_key(package["name"]), str(package["version"]))
        for package in lock.get("package", [])
    }


def expected_npm(root: Path) -> set[tuple[str, str]]:
    tree = cast(NpmTree, run_json(["npm", "ls", "--omit=dev", "--all", "--json"], cwd=root))
    packages: set[tuple[str, str]] = set()

    def visit(dependencies: dict[str, NpmPackage]) -> None:
        for name, data in dependencies.items():
            version = data.get("version")
            if not version:
                raise RuntimeError(f"npm dependency has no resolved version: {name}")
            packages.add((npm_key(name), version))
            visit(data.get("dependencies", {}))

    visit(tree.get("dependencies", {}))
    return packages


def expected_npm_inventory(root: Path) -> set[tuple[str, str]]:
    """Return the complete npm dependency tree installed for this build host."""
    tree = cast(NpmTree, run_json(["npm", "ls", "--all", "--json"], cwd=root))
    packages: set[tuple[str, str]] = set()

    def visit(dependencies: dict[str, NpmPackage]) -> None:
        for name, data in dependencies.items():
            version = data.get("version")
            if not version:
                # npm ls reports platform-incompatible optional packages without versions.
                continue
            packages.add((npm_key(name), version))
            visit(data.get("dependencies", {}))

    visit(tree.get("dependencies", {}))
    project = json.loads((root / "package.json").read_text(encoding="utf-8"))
    packages.add((npm_key(project["name"]), str(project["version"])))
    return packages


def expected_cargo(root: Path) -> set[tuple[str, str]]:
    metadata = cast(
        CargoMetadata,
        run_json(
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
        ),
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


def expected_cargo_inventory(root: Path) -> set[tuple[str, str]]:
    """Return all pinned Cargo packages, including non-Windows target entries."""
    lock = tomllib.loads(
        (root / "apps" / "desktop" / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8")
    )
    return {
        (cargo_key(package["name"]), str(package["version"])) for package in lock.get("package", [])
    }


def missing_inventory_rows(
    ecosystem: str,
    expected: set[tuple[str, str]],
    inventory: dict[str, set[tuple[str, str]]],
) -> set[tuple[str, str]]:
    return expected - inventory[ecosystem]


def inventory_set_mismatches(
    ecosystem: str,
    expected: set[tuple[str, str]],
    inventory: dict[str, set[tuple[str, str]]],
) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """Return missing and stale rows against the complete build inventory."""
    return expected - inventory[ecosystem], inventory[ecosystem] - expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    root = args.root.resolve()
    inventory = read_inventory(root / "docs/releases/dependency-inventory.md")
    notice_path = root / "docs/releases/THIRD_PARTY_NOTICES.md"
    notices = read_notice_references(notice_path)
    expected = {
        "Python": expected_python(root),
        "npm": expected_npm(root),
        "Rust/Cargo": expected_cargo(root),
    }
    expected_inventory = {
        "Python": expected_python_inventory(root),
        "npm": expected_npm_inventory(root),
        "Rust/Cargo": expected_cargo_inventory(root),
    }

    errors: list[str] = []
    errors.extend(inventory_metadata_errors(root / "docs/releases/dependency-inventory.md"))
    errors.extend(project_license_inventory_errors(root))
    for ecosystem, packages in expected_inventory.items():
        missing_rows, stale_rows = inventory_set_mismatches(ecosystem, packages, inventory)
        if missing_rows:
            errors.append(
                f"{ecosystem}: {len(missing_rows)} build package(s) missing from inventory: "
                + ", ".join(f"{name}@{version}" for name, version in sorted(missing_rows))
            )
        if stale_rows:
            errors.append(
                f"{ecosystem}: {len(stale_rows)} stale or non-target package row(s) in inventory: "
                + ", ".join(f"{name}@{version}" for name, version in sorted(stale_rows))
            )
    unknown_notice_placeholders = count_unknown_additional_notice_placeholders(notice_path)
    if unknown_notice_placeholders:
        errors.append(
            f"Third-party notices contain {unknown_notice_placeholders} scanner-generated "
            "`Additional notice: UNKNOWN` placeholder(s); replace them with verified source text."
        )
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
    print(
        "This check validates package references and populated metadata fields; it does not verify license text or legal completeness."
    )
    print(
        "Release inventory exactly matches its resolution sources: "
        + ", ".join(
            f"{ecosystem}={len(packages)}" for ecosystem, packages in expected_inventory.items()
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

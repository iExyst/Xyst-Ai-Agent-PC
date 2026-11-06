"""Inventory pinned bundled and optional Hermes skill packages without executing them."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import unquote, urlsplit

import yaml
from audit_execution_inventory import PIN, ROOT, check_reference

OUTPUT = PurePosixPath("docs/parity/skill-package-inventory.json")
SKILL_ROOTS = ("skills", "optional-skills")
MAX_SKILL_BYTES = 512 * 1024
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[^)]*)?\)")


def _git(*args: str) -> bytes:
    return subprocess.run(["git", "-C", str(ROOT), *args], check=True, capture_output=True).stdout


def _tree() -> list[tuple[str, str, str]]:
    records = _git("ls-tree", "-r", "-z", PIN, "--", *SKILL_ROOTS).split(b"\0")
    result: list[tuple[str, str, str]] = []
    for record in records:
        if not record:
            continue
        header, raw_path = record.split(b"\t", 1)
        mode, kind, oid = header.decode("ascii").split(" ", 2)
        if kind != "blob":
            continue
        result.append((raw_path.decode("utf-8"), mode, oid))
    return result


def _blobs(entries: list[tuple[str, str, str]]) -> dict[str, bytes]:
    request = "".join(f"{oid}\n" for _path, _mode, oid in entries).encode("ascii")
    raw = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "--batch"],
        input=request,
        check=True,
        capture_output=True,
    ).stdout
    blobs: dict[str, bytes] = {}
    offset = 0
    for path, _mode, expected_oid in entries:
        line_end = raw.index(b"\n", offset)
        oid, kind, size_text = raw[offset:line_end].decode("ascii").split(" ", 2)
        if oid != expected_oid or kind != "blob":
            raise ValueError(f"Pinned object mismatch for {path}")
        start = line_end + 1
        end = start + int(size_text)
        blobs[path] = raw[start:end]
        if raw[end : end + 1] != b"\n":
            raise ValueError(f"Malformed Git batch response for {path}")
        offset = end + 1
    return blobs


def _frontmatter(path: str, raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_SKILL_BYTES:
        raise ValueError(f"{path}: SKILL.md exceeds {MAX_SKILL_BYTES} bytes")
    text = raw.decode("utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{path}: missing YAML frontmatter opener")
    try:
        end = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration as exc:
        raise ValueError(f"{path}: missing YAML frontmatter closer") from exc
    loaded = yaml.safe_load("\n".join(lines[1:end]))
    if not isinstance(loaded, dict):
        raise TypeError(f"{path}: frontmatter must be a mapping")
    parsed = cast(dict[str, Any], loaded)
    allowed = parsed.get("allowed-tools", parsed.get("allowed_tools", []))
    allowed_tools: list[str] = []
    if isinstance(allowed, str):
        allowed_tools = [allowed]
    elif isinstance(allowed, list):
        allowed_tools = [item for item in cast(list[Any], allowed) if isinstance(item, str)]
    platforms = parsed.get("platforms", [])
    platform_names: list[str] = []
    if isinstance(platforms, str):
        platform_names = [platforms]
    elif isinstance(platforms, list):
        platform_names = [item for item in cast(list[Any], platforms) if isinstance(item, str)]
    prerequisites = parsed.get("prerequisites", {})
    if not isinstance(prerequisites, dict):
        prerequisites = {"declared_type": type(prerequisites).__name__}
    hermes = parsed.get("metadata", {})
    if not isinstance(hermes, dict):
        hermes = {}
    hermes = cast(dict[str, Any], hermes).get("hermes", {})
    if not isinstance(hermes, dict):
        hermes = {}
    hermes = cast(dict[str, Any], hermes)
    required_toolsets = hermes.get("requires_toolsets", [])
    if isinstance(required_toolsets, str):
        required_toolsets = [required_toolsets]
    if not isinstance(required_toolsets, list):
        required_toolsets = []
    hermes_category = hermes.get("category")
    return {
        "name": parsed.get("name") if isinstance(parsed.get("name"), str) else None,
        "description": parsed.get("description")
        if isinstance(parsed.get("description"), str)
        else None,
        "version": parsed.get("version") if isinstance(parsed.get("version"), str) else None,
        "platforms": sorted(set(platform_names)),
        "allowed_tools": sorted(set(allowed_tools)),
        "prerequisites": prerequisites,
        "dependencies": parsed.get("dependencies"),
        "required_commands": parsed.get("required_commands"),
        "required_environment_variables": parsed.get("required_environment_variables"),
        "hermes_metadata_keys": sorted(str(key) for key in hermes),
        "hermes_category": hermes_category if isinstance(hermes_category, str) else None,
        "hermes_requires_toolsets": sorted(
            item for item in cast(list[Any], required_toolsets) if isinstance(item, str)
        ),
        "frontmatter_keys": sorted(str(key) for key in parsed),
        "local_markdown_links": _local_links(text[end + 1 :]),
    }


def _local_links(body: str) -> list[str]:
    links: set[str] = set()
    for match in LINK_RE.finditer(body):
        parsed = urlsplit(match.group(1).strip())
        if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("#"):
            continue
        links.add(unquote(parsed.path))
    return sorted(links)


def inventory() -> dict[str, Any]:
    check_reference()
    entries = _tree()
    paths = {path: mode for path, mode, _oid in entries}
    blobs = _blobs(entries)
    skill_paths = [path for path in paths if PurePosixPath(path).name == "SKILL.md"]
    packages: list[dict[str, Any]] = []
    owned: set[str] = set()
    for skill_path in sorted(skill_paths):
        package_dir = str(PurePosixPath(skill_path).parent)
        files = [
            path
            for path in paths
            if str(PurePosixPath(path).parent).startswith(package_dir + "/")
            or str(PurePosixPath(path).parent) == package_dir
        ]
        package_files: list[dict[str, Any]] = []
        for path in sorted(files):
            raw = blobs[path]
            owned.add(path)
            package_files.append(
                {
                    "path": str(PurePosixPath(path).relative_to(PurePosixPath(package_dir))),
                    "mode": paths[path],
                    "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        raw_skill = blobs[skill_path]
        metadata = _frontmatter(skill_path, raw_skill)
        local_links = cast(list[str], metadata.pop("local_markdown_links"))
        package_file_paths = {
            str(PurePosixPath(package_dir, entry["path"])) for entry in package_files
        }
        normalized: list[dict[str, Any]] = []
        for link in local_links:
            candidate = str(PurePosixPath(package_dir, link))
            normalized.append(
                {"target": link, "exists_in_package": candidate in package_file_paths}
            )
        packages.append(
            {
                "root": "optional" if skill_path.startswith("optional-skills/") else "bundled",
                "skill_path": skill_path,
                "directory": package_dir,
                **metadata,
                "file_count": len(package_files),
                "files": package_files,
                "local_links": normalized,
            }
        )
    unowned = [
        {"path": path, "mode": paths[path], "sha256": hashlib.sha256(blobs[path]).hexdigest()}
        for path in sorted(set(paths) - owned)
    ]
    return {
        "reference_commit": PIN,
        "audit_date": "2026-11-06",
        "scope": "all tracked SKILL.md packages and adjacent files under skills/ and optional-skills/ at the pinned commit; metadata and hashes only; no package imported or executed",
        "bundled_package_count": sum(package["root"] == "bundled" for package in packages),
        "optional_package_count": sum(package["root"] == "optional" for package in packages),
        "package_count": len(packages),
        "tracked_file_count": len(entries),
        "unowned_tree_files": unowned,
        "packages": packages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output_path = str(OUTPUT)
    encoded = json.dumps(inventory(), indent=2, ensure_ascii=True) + "\n"
    destination = Path(output_path)
    if args.check:
        if destination.read_text(encoding="utf-8") != encoded:
            raise SystemExit(
                "Skill package inventory drifted; review pinned changes before regeneration."
            )
    else:
        destination.write_text(encoded, encoding="utf-8")
    data = json.loads(encoded)
    print(
        f"Pinned skill package inventory: {data['bundled_package_count']} bundled, "
        f"{data['optional_package_count']} optional, {data['tracked_file_count']} tracked files."
    )


if __name__ == "__main__":
    main()

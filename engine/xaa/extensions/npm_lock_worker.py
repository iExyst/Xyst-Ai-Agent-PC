"""Isolated parser for imported npm package manifests and v3 lockfiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

from xaa.extensions.npm_dependencies import (
    MAX_LOCKFILE_BYTES,
    MAX_PACKAGE_MANIFEST_BYTES,
    ProcedureNpmDependencyLock,
    parse_npm_package_lock,
    verify_npm_package_manifest,
)

MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_BYTES = 6 * 1024 * 1024


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("npm lock worker request contains duplicate fields.")
        result[key] = value
    return result


def _read_file(name: str, maximum: int) -> bytes:
    path = Path.cwd() / name
    if path.is_symlink() or not path.is_file():
        raise ValueError("npm lock worker input is missing.")
    if path.stat().st_size > maximum:
        raise ValueError("npm lock worker input exceeded its size limit.")
    content = path.read_bytes()
    if not content or len(content) > maximum:
        raise ValueError("npm lock worker input exceeded its size limit.")
    return content


def _read_request() -> dict[str, Any]:
    raw = _read_file("request.json", MAX_REQUEST_BYTES)
    value: object = json.loads(raw, object_pairs_hook=_unique_object)
    if not isinstance(value, dict):
        raise TypeError("npm lock worker request must be an object.")
    request = cast(dict[str, Any], value)
    if set(request) != {
        "skill_id",
        "skill_version",
        "source_sha256",
        "package_manifest_sha256",
        "distribution",
        "node_version",
        "npm_version",
        "requirements",
    }:
        raise ValueError("npm lock worker request fields are invalid.")
    requirements = request["requirements"]
    if (
        not isinstance(request["skill_id"], str)
        or type(request["skill_version"]) is not int
        or not isinstance(request["source_sha256"], str)
        or not isinstance(request["package_manifest_sha256"], str)
        or not isinstance(request["distribution"], str)
        or not isinstance(request["node_version"], str)
        or not isinstance(request["npm_version"], str)
        or not isinstance(requirements, list)
        or len(cast(list[object], requirements)) > 128
        or any(
            not isinstance(item, str) or len(item) > 256
            for item in cast(list[object], requirements)
        )
        or sum(len(item) for item in cast(list[str], requirements)) > 32 * 1024
    ):
        raise ValueError("npm lock worker request identity is invalid.")
    return request


def _inspect(request: dict[str, Any]) -> ProcedureNpmDependencyLock:
    manifest = _read_file("package.json", MAX_PACKAGE_MANIFEST_BYTES)
    lockfile = _read_file("package-lock.json", MAX_LOCKFILE_BYTES)
    proposal = parse_npm_package_lock(
        lockfile,
        skill_id=request["skill_id"],
        skill_version=request["skill_version"],
        source_sha256=request["source_sha256"],
        package_manifest_sha256=request["package_manifest_sha256"],
        distribution=request["distribution"],
        node_version=request["node_version"],
        npm_version=request["npm_version"],
        requirements=cast(list[str], request["requirements"]),
    )
    verify_npm_package_manifest(manifest, proposal)
    return proposal


def main() -> int:
    try:
        proposal = _inspect(_read_request())
        result: dict[str, object] = {"ok": True, "lock": proposal.model_dump(mode="json")}
    except Exception:  # noqa: BLE001 - the parent receives no untrusted parser details.
        result = {"ok": False, "error": "Imported npm manifest and lockfile were rejected."}
    raw = json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
    if len(raw) > MAX_RESULT_BYTES:
        result = {"ok": False, "error": "Imported npm lock result exceeded its size limit."}
        raw = json.dumps(result, separators=(",", ":")).encode("ascii") + b"\n"
    offset = 0
    while offset < len(raw):
        offset += os.write(1, raw[offset:])
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import tarfile
from typing import Any

import pytest
from pydantic import ValidationError
from xaa.extensions.npm_dependencies import (
    NpmLockedPackage,
    parse_npm_package_lock,
    verify_npm_package_manifest,
    verify_npm_tarball,
)

SKILL_ID = "12345678-1234-4234-8234-123456789abc"
SOURCE_SHA256 = "a" * 64
MANIFEST = b'{"name":"fixture","dependencies":{"left-pad":"^1.3.0"}}'
MANIFEST_SHA256 = hashlib.sha256(MANIFEST).hexdigest()
TARBALL = b"fixture package bytes"
INTEGRITY = "sha512-" + base64.b64encode(hashlib.sha512(TARBALL).digest()).decode("ascii")


def package_lock(
    *,
    lockfile_version: int = 3,
    root_range: str = "^1.3.0",
    version: str = "1.3.0",
    resolved: str | None = None,
    integrity: str = INTEGRITY,
    package_path: str = "node_modules/left-pad",
    linked: bool = False,
) -> bytes:
    record: dict[str, Any] = {
        "version": version,
        "resolved": resolved or f"https://registry.npmjs.org/left-pad/-/left-pad-{version}.tgz",
        "integrity": integrity,
    }
    if linked:
        record["link"] = True
    return json.dumps(
        {
            "name": "fixture",
            "lockfileVersion": lockfile_version,
            "packages": {
                "": {"name": "fixture", "dependencies": {"left-pad": root_range}},
                package_path: record,
            },
        },
        separators=(",", ":"),
    ).encode()


def parse(
    raw: bytes,
    *,
    requirements: tuple[str, ...] = ("left-pad@^1.3.0",),
    manifest: bytes = MANIFEST,
):
    return parse_npm_package_lock(
        raw,
        skill_id=SKILL_ID,
        skill_version=2,
        source_sha256=SOURCE_SHA256,
        package_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        distribution="Ubuntu-24.04",
        node_version="v22.2.0",
        npm_version="10.8.1",
        requirements=requirements,
    )


def test_npm_v3_lock_is_bound_to_procedure_assets_and_runtime() -> None:
    raw = package_lock()
    lock = parse(raw)

    assert lock.lockfile_sha256 == hashlib.sha256(raw).hexdigest()
    assert lock.packages[0].name == "left-pad"
    assert lock.packages[0].version == "1.3.0"
    assert lock.packages[0].integrity == INTEGRITY
    assert lock.root_dependencies[0].version_range == "^1.3.0"
    assert lock.source_sha256 == SOURCE_SHA256
    assert lock.package_manifest_sha256 == MANIFEST_SHA256
    assert len(lock.environment_digest()) == 64
    assert parse(raw).environment_digest() == lock.environment_digest()


def test_package_manifest_must_match_lockfile_root_dependencies() -> None:
    lock = parse(package_lock())
    manifest = MANIFEST

    assert verify_npm_package_manifest(manifest, lock) == hashlib.sha256(manifest).hexdigest()
    with pytest.raises(ValueError, match="do not match"):
        verify_npm_package_manifest(
            b'{"name":"fixture","dependencies":{"left-pad":"^2.0.0"}}', lock
        )
    with pytest.raises(ValueError, match="invalid JSON"):
        verify_npm_package_manifest(
            b'{"dependencies":{"left-pad":"^1.3.0"},"dependencies":{}}', lock
        )


def test_npm_lock_supports_scoped_package_paths() -> None:
    document = json.loads(package_lock(package_path="node_modules/@xyst/toolkit").decode())
    document["packages"][""]["dependencies"] = {"@xyst/toolkit": "~2.4.1"}
    document["packages"]["node_modules/@xyst/toolkit"]["version"] = "2.4.1"
    document["packages"]["node_modules/@xyst/toolkit"]["resolved"] = (
        "https://registry.npmjs.org/@xyst/toolkit/-/toolkit-2.4.1.tgz"
    )
    manifest = b'{"name":"fixture","dependencies":{"@xyst/toolkit":"~2.4.1"}}'

    lock = parse(
        json.dumps(document).encode(),
        requirements=("@xyst/toolkit@~2.4.1",),
        manifest=manifest,
    )
    assert lock.packages[0].name == "@xyst/toolkit"


@pytest.mark.parametrize(
    ("raw", "requirements"),
    [
        (package_lock(lockfile_version=2), ("left-pad@^1.3.0",)),
        (
            package_lock(resolved="https://evil.example/left-pad-1.3.0.tgz"),
            ("left-pad@^1.3.0",),
        ),
        (
            package_lock(resolved="https://user:secret@registry.npmjs.org/left-pad-1.3.0.tgz"),
            ("left-pad@^1.3.0",),
        ),
        (
            package_lock(resolved="https://registry.npmjs.org/left-pad/../secret.tgz"),
            ("left-pad@^1.3.0",),
        ),
        (package_lock(integrity="sha1-YWJj"), ("left-pad@^1.3.0",)),
        (package_lock(linked=True), ("left-pad@^1.3.0",)),
        (package_lock(package_path="node_modules/../left-pad"), ("left-pad@^1.3.0",)),
        (package_lock(), ("left-pad@^2.0.0",)),
    ],
)
def test_npm_lock_rejects_unsupported_or_mismatched_entries(
    raw: bytes, requirements: tuple[str, ...]
) -> None:
    with pytest.raises((ValueError, ValidationError)):
        parse(raw, requirements=requirements)


def test_npm_lock_rejects_duplicate_json_fields_and_size_overflow() -> None:
    duplicate = b'{"lockfileVersion":3,"lockfileVersion":3,"packages":{"":{}}}'
    with pytest.raises(ValueError, match="invalid JSON"):
        parse(duplicate)
    with pytest.raises(ValueError, match="size limit"):
        parse(b" " * (2 * 1024 * 1024 + 1))


@pytest.mark.parametrize(
    ("version_range", "version"),
    [
        ("^1.2.0", "1.9.9"),
        ("^0.2.0", "0.2.99"),
        ("~1.2.3", "1.2.9"),
        ("1.2.x", "1.2.8"),
        (">=1.2.0 <2.0.0 || >=3.0.0", "3.1.0"),
        ("1.2.3 - 1.2.9", "1.2.8"),
        ("^1.2.3-beta.1", "1.2.3-beta.2"),
    ],
)
def test_npm_lock_uses_npm_semver_ranges(version_range: str, version: str) -> None:
    raw = package_lock(root_range=version_range, version=version)
    lock = parse(raw, requirements=(f"left-pad@{version_range}",))

    assert lock.packages[0].version == version


@pytest.mark.parametrize(
    ("version_range", "version"),
    [
        ("^1.2.0", "2.0.0"),
        ("^0.2.0", "0.3.0"),
        ("~1.2.3", "1.3.0"),
        ("^1.2.3", "1.3.0-beta.1"),
    ],
)
def test_npm_lock_rejects_locked_versions_outside_npm_range(
    version_range: str, version: str
) -> None:
    with pytest.raises(ValueError, match="does not satisfy"):
        parse(package_lock(root_range=version_range, version=version))


def test_npm_lock_rejects_invalid_semver_range() -> None:
    with pytest.raises(ValueError, match="invalid semantic version range"):
        parse(package_lock(root_range=">wat"))


def package_record(
    name: str,
    version: str,
    *,
    dependencies: dict[str, str] | None = None,
    optional_dependencies: dict[str, str] | None = None,
    peer_dependencies: dict[str, str] | None = None,
    peer_dependencies_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "version": version,
        "resolved": f"https://registry.npmjs.org/{name}/-/{name.rsplit('/', 1)[-1]}-{version}.tgz",
        "integrity": INTEGRITY,
    }
    if dependencies is not None:
        record["dependencies"] = dependencies
    if optional_dependencies is not None:
        record["optionalDependencies"] = optional_dependencies
    if peer_dependencies is not None:
        record["peerDependencies"] = peer_dependencies
    if peer_dependencies_meta is not None:
        record["peerDependenciesMeta"] = peer_dependencies_meta
    return record


def test_npm_lock_checks_nested_transitive_dependency_edges() -> None:
    document = json.loads(package_lock().decode())
    document["packages"]["node_modules/left-pad"]["dependencies"] = {"repeat": "~1.0.0"}
    document["packages"]["node_modules/left-pad/node_modules/repeat"] = package_record(
        "repeat", "1.0.4"
    )

    lock = parse(json.dumps(document).encode())

    assert [package.package_path for package in lock.packages] == [
        "node_modules/left-pad",
        "node_modules/left-pad/node_modules/repeat",
    ]


def test_npm_lock_rejects_missing_mismatched_and_orphaned_transitive_packages() -> None:
    missing = json.loads(package_lock().decode())
    missing["packages"]["node_modules/left-pad"]["dependencies"] = {"repeat": "^1.0.0"}
    with pytest.raises(ValueError, match="is missing"):
        parse(json.dumps(missing).encode())

    mismatched = json.loads(package_lock().decode())
    mismatched["packages"]["node_modules/left-pad"]["dependencies"] = {"repeat": "^1.0.0"}
    mismatched["packages"]["node_modules/left-pad/node_modules/repeat"] = package_record(
        "repeat", "2.0.0"
    )
    with pytest.raises(ValueError, match="does not satisfy"):
        parse(json.dumps(mismatched).encode())

    orphaned = json.loads(package_lock().decode())
    orphaned["packages"]["node_modules/unreachable"] = package_record("unreachable", "1.0.0")
    with pytest.raises(ValueError, match="outside the dependency graph"):
        parse(json.dumps(orphaned).encode())


def test_npm_lock_handles_optional_and_peer_dependency_edges() -> None:
    optional = json.loads(package_lock().decode())
    optional["packages"]["node_modules/left-pad"]["optionalDependencies"] = {
        "platform-extra": "^1.0.0"
    }
    parse(json.dumps(optional).encode())

    peer = json.loads(package_lock().decode())
    peer["packages"][""]["dependencies"]["repeat"] = "^1.0.0"
    peer["packages"]["node_modules/repeat"] = package_record("repeat", "1.0.2")
    peer["packages"]["node_modules/left-pad"]["peerDependencies"] = {"repeat": "~1.0.0"}
    parse(json.dumps(peer).encode())

    optional_peer = json.loads(package_lock().decode())
    optional_peer["packages"]["node_modules/left-pad"]["peerDependencies"] = {
        "platform-peer": "^1.0.0"
    }
    optional_peer["packages"]["node_modules/left-pad"]["peerDependenciesMeta"] = {
        "platform-peer": {"optional": True}
    }
    parse(json.dumps(optional_peer).encode())


def make_tarball(
    *,
    metadata: bytes = b'{"name":"left-pad","version":"1.3.0","scripts":{"install":"node gyp.js"}}',
    entries: tuple[tuple[str, bytes], ...] = (("package/index.js", b"module.exports = 1;"),),
    link: tuple[str, str] | None = None,
) -> bytes:
    output = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive,
    ):
        metadata_info = tarfile.TarInfo("package/package.json")
        metadata_info.size = len(metadata)
        archive.addfile(metadata_info, io.BytesIO(metadata))
        for name, contents in entries:
            info = tarfile.TarInfo(name)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))
        if link is not None:
            info = tarfile.TarInfo(link[0])
            info.type = tarfile.SYMTYPE
            info.linkname = link[1]
            archive.addfile(info)
    return output.getvalue()


def locked_package(archive: bytes) -> NpmLockedPackage:
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(archive).digest()).decode("ascii")
    return NpmLockedPackage(
        package_path="node_modules/left-pad",
        name="left-pad",
        version="1.3.0",
        resolved_url="https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
        integrity=integrity,
    )


def test_npm_tarball_verification_checks_integrity_identity_and_lifecycle_names() -> None:
    archive = make_tarball()
    locked = locked_package(archive)

    verified = verify_npm_tarball(locked, archive)

    assert verified.package_path == locked.package_path
    assert (verified.name, verified.version) == (locked.name, locked.version)
    assert verified.integrity == locked.integrity
    assert verified.archive_sha256 == hashlib.sha256(archive).hexdigest()
    assert verified.archive_size_bytes == len(archive)
    assert verified.member_count == 2
    assert verified.expanded_size_bytes > 0
    assert verified.install_lifecycle_scripts == ("install",)


def test_npm_tarball_rejects_integrity_and_package_identity_mismatches() -> None:
    archive = make_tarball()
    locked = locked_package(archive)
    with pytest.raises(ValueError, match="integrity"):
        verify_npm_tarball(locked, archive + b"tampered")

    wrong_identity = make_tarball(metadata=b'{"name":"other","version":"1.3.0"}')
    with pytest.raises(ValueError, match="identity"):
        verify_npm_tarball(locked_package(wrong_identity), wrong_identity)


@pytest.mark.parametrize(
    ("entries", "link"),
    [
        ((("package/../escape", b"bad"),), None),
        ((("/package/absolute", b"bad"),), None),
        ((("package\\\\escape", b"bad"),), None),
        ((("other/file", b"bad"),), None),
        ((("package/index.js", b"one"), ("package/index.js", b"two")), None),
        ((("package/index.js", b""),), ("package/link", "../escape")),
    ],
)
def test_npm_tarball_rejects_unsafe_or_duplicate_members(
    entries: tuple[tuple[str, bytes], ...], link: tuple[str, str] | None
) -> None:
    archive = make_tarball(entries=entries, link=link)
    with pytest.raises(ValueError):
        verify_npm_tarball(locked_package(archive), archive)


def test_npm_tarball_rejects_malformed_archive_and_duplicate_metadata_fields() -> None:
    malformed = b"not a gzip tarball"
    with pytest.raises(ValueError, match="malformed"):
        verify_npm_tarball(locked_package(malformed), malformed)

    archive = make_tarball(metadata=b'{"name":"left-pad","name":"left-pad","version":"1.3.0"}')
    with pytest.raises(ValueError, match="metadata is invalid JSON"):
        verify_npm_tarball(locked_package(archive), archive)

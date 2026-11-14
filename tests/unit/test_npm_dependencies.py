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
    resolved: str = "https://registry.npmjs.org/left-pad/-/left-pad-1.3.0.tgz",
    integrity: str = INTEGRITY,
    package_path: str = "node_modules/left-pad",
    linked: bool = False,
) -> bytes:
    record: dict[str, Any] = {
        "version": "1.3.0",
        "resolved": resolved,
        "integrity": integrity,
    }
    if linked:
        record["link"] = True
    return json.dumps(
        {
            "name": "fixture",
            "lockfileVersion": lockfile_version,
            "packages": {
                "": {"name": "fixture", "dependencies": {"left-pad": "^1.3.0"}},
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

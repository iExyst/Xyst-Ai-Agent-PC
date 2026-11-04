import json
import os
import subprocess
from contextlib import contextmanager

import pytest
from xaa.runtime import file_handles
from xaa.runtime.workspace_snapshot import (
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_WIRE_BYTES,
    SnapshotFile,
    WorkspaceSnapshot,
    capture_workspace,
    materialize_snapshot,
)


def test_exact_bytes_unicode_empty_directories_and_exclusions(workspace_tmp_path):
    root = workspace_tmp_path / "project"
    root.mkdir()
    (root / "nested").mkdir()
    (root / "empty").mkdir()
    (root / "nested" / "r\u00e9sum\u00e9.bin").write_bytes(b"\x00\xff\r\n")
    (root / ".git").mkdir()
    (root / ".git" / "not-transferred").write_bytes(b"private repository metadata")
    snapshot = capture_workspace(root)
    assert snapshot.excluded == (".git",)
    wire = snapshot.to_bytes()
    assert str(root).encode() not in wire
    decoded = WorkspaceSnapshot.from_bytes(wire)
    assert decoded.sha256 == snapshot.sha256
    destination = workspace_tmp_path / "staging"
    materialize_snapshot(decoded, destination)
    assert (destination / "nested" / "r\u00e9sum\u00e9.bin").read_bytes() == b"\x00\xff\r\n"
    assert (destination / "empty").is_dir()
    assert not (destination / ".git").exists()
    (destination / "nested" / "r\u00e9sum\u00e9.bin").write_bytes(b"sandbox edit")
    assert (root / "nested" / "r\u00e9sum\u00e9.bin").read_bytes() == b"\x00\xff\r\n"
    with pytest.raises(FileExistsError):
        materialize_snapshot(decoded, destination)


@pytest.mark.parametrize(
    "path",
    [
        "../escape",
        "/absolute",
        "C:/escape",
        "x\\escape",
        "a//b",
        "x/../b",
        "nul.txt",
        "a.",
        "a\x00b",
    ],
)
def test_untrusted_paths_are_rejected_before_creation(path, workspace_tmp_path):
    payload = json.loads(WorkspaceSnapshot((SnapshotFile("safe", b"ok"),), ()).to_bytes())
    payload["files"][0]["path"] = path
    with pytest.raises(ValueError):
        WorkspaceSnapshot.from_bytes(json.dumps(payload).encode())
    assert not (workspace_tmp_path / "escape").exists()


def test_manifest_rejects_conflicts_tampering_and_duplicates():
    with pytest.raises(ValueError, match="colliding"):
        WorkspaceSnapshot((SnapshotFile("A", b""), SnapshotFile("a", b"")), ())
    with pytest.raises(ValueError, match="parent"):
        WorkspaceSnapshot((SnapshotFile("a/b", b""),), ())
    with pytest.raises(ValueError, match="colliding"):
        WorkspaceSnapshot((SnapshotFile("a", b""),), ("a",))
    snapshot = WorkspaceSnapshot((SnapshotFile("a", b"bytes"),), ())
    payload = json.loads(snapshot.to_bytes())
    payload["files"][0]["content_b64"] = "ZXZpbA=="
    with pytest.raises(ValueError, match="count|digest"):
        WorkspaceSnapshot.from_bytes(json.dumps(payload).encode())
    payload = json.loads(snapshot.to_bytes())
    payload["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="manifest digest"):
        WorkspaceSnapshot.from_bytes(json.dumps(payload).encode())
    with pytest.raises(ValueError, match="Duplicate"):
        WorkspaceSnapshot.from_bytes(b'{"format":1,"format":1}')
    with pytest.raises(ValueError):
        WorkspaceSnapshot.from_bytes(b" " * (MAX_WIRE_BYTES + 1))


def test_budget_failures_never_return_truncated_snapshots(workspace_tmp_path):
    root = workspace_tmp_path
    big = root / "big"
    big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="bounded"):
        capture_workspace(root)
    big.unlink()
    for index in range(MAX_FILES + 1):
        (root / str(index)).write_bytes(b"")
    with pytest.raises(ValueError, match="count"):
        capture_workspace(root)


def test_actual_junction_or_symlink_never_transfers_outside_bytes(workspace_tmp_path):
    root = workspace_tmp_path / "project"
    outside = workspace_tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"outside bytes")
    link = root / "redirect"
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(PermissionError, match="reparse|junction|symlink"):
            capture_workspace(root)
        assert sentinel.read_bytes() == b"outside bytes"
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_open_handle_redirection_is_denied(workspace_tmp_path, monkeypatch):
    root = workspace_tmp_path / "project"
    root.mkdir()
    (root / "file").write_bytes(b"inside")
    outside = workspace_tmp_path / "outside"
    outside.write_bytes(b"outside")
    original = file_handles.open_for_read

    @contextmanager
    def redirected(_):
        with original(outside) as source:
            yield source

    monkeypatch.setattr(file_handles, "open_for_read", redirected)
    with pytest.raises(PermissionError, match="Opened snapshot file escaped"):
        capture_workspace(root)


def test_snapshot_digest_is_independent_of_enumeration_order():
    a, b = SnapshotFile("a", b"A"), SnapshotFile("b", b"B")
    first = WorkspaceSnapshot((a, b), ())
    second = WorkspaceSnapshot((b, a), ())
    assert first.sha256 == second.sha256
    assert first.to_bytes() == second.to_bytes()

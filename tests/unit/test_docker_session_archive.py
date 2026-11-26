import io
import tarfile

import pytest
from xaa.runtime.docker_session_archive import validate_session_archive


def archive_with(*members: tuple[str, bytes, str]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:") as archive:
        for name, content, kind in members:
            entry = tarfile.TarInfo(name)
            if kind == "file":
                entry.size = len(content)
                archive.addfile(entry, io.BytesIO(content))
            elif kind == "symlink":
                entry.type = tarfile.SYMTYPE
                entry.linkname = content.decode()
                archive.addfile(entry)
            elif kind == "hardlink":
                entry.type = tarfile.LNKTYPE
                entry.linkname = content.decode()
                archive.addfile(entry)
            else:
                raise AssertionError(kind)
    return buffer.getvalue()


def test_session_archive_accepts_regular_relative_files_and_checks_digest() -> None:
    raw = archive_with(("./answer.txt", b"review me", "file"))

    result = validate_session_archive(raw)

    assert result.size_bytes == len(raw)
    assert result.file_count == 1
    assert len(result.sha256) == 64


@pytest.mark.parametrize(
    ("name", "content", "kind"),
    [
        ("../../outside.txt", b"x", "file"),
        ("C:/outside.txt", b"x", "file"),
        ("link", b"../../outside.txt", "symlink"),
        ("hardlink", b"answer.txt", "hardlink"),
    ],
)
def test_session_archive_rejects_traversal_and_links(name: str, content: bytes, kind: str) -> None:
    with pytest.raises(ValueError):
        validate_session_archive(archive_with((name, content, kind)))


def test_session_archive_rejects_duplicate_entries_and_oversized_input() -> None:
    duplicated = archive_with(("answer.txt", b"one", "file"), ("answer.txt", b"two", "file"))
    with pytest.raises(ValueError, match="duplicate"):
        validate_session_archive(duplicated)
    valid = archive_with(("answer.txt", b"complete", "file"))
    with pytest.raises(ValueError, match="malformed"):
        validate_session_archive(valid[:-1] + b"x")
    with pytest.raises(ValueError, match="8 MiB"):
        validate_session_archive(b"x" * (8 * 1024 * 1024 + 1))

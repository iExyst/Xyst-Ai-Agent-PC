from pathlib import Path

_MATRIX = Path(__file__).resolve().parents[2] / "docs" / "parity" / "hermes-matrix.md"
_ALLOWED_STATUSES = {"planned", "implemented", "verified", "blocked"}


def test_hermes_matrix_uses_the_declared_status_values() -> None:
    lines = _MATRIX.read_text(encoding="utf-8").splitlines()
    header_index = next(
        index
        for index, line in enumerate(lines)
        if line.startswith("| Capability / upstream evidence at pin |")
    )

    assert lines[header_index].rstrip().endswith("| Status | Scope notes |")
    rows: list[tuple[str, str]] = []
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        parts = line.rsplit("|", 3)
        assert len(parts) == 4
        rows.append((parts[1].strip(), parts[2].strip()))

    assert len(rows) >= 20
    assert {status for status, _ in rows} <= _ALLOWED_STATUSES
    assert all(scope_notes for _, scope_notes in rows)

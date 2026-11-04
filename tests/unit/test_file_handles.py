import os

import pytest
from xaa.runtime.file_handles import open_existing_for_edit


def test_hardlinked_edit_refused_before_any_write(workspace_tmp_path):
    outside = workspace_tmp_path / "outside.txt"
    outside.write_bytes(b"preserve")
    project = workspace_tmp_path / "project"
    project.mkdir()
    alias = project / "alias.txt"
    os.link(outside, alias)
    with (
        pytest.raises(PermissionError, match="hard-link"),
        open_existing_for_edit(alias) as destination,
    ):
        destination.write(b"changed!")
    assert outside.read_bytes() == b"preserve"

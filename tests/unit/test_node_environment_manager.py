from contextlib import contextmanager
from pathlib import Path

import pytest
from xaa.runtime import node_environment_manager as environments


def test_removal_recovers_a_corrupted_digest_without_touching_neighbors(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "a" * 64
    target = workspace_tmp_path / digest
    tree = target / "environment"
    tree.mkdir(parents=True)
    (tree / "partial-file").write_text("interrupted", encoding="utf-8")
    (target / "environment-manifest.json").write_text("{broken", encoding="utf-8")
    neighbor = workspace_tmp_path / ("b" * 64)
    neighbor.mkdir()
    outside = neighbor / "preserve.txt"
    outside.write_text("keep", encoding="utf-8")
    stopped: list[str] = []
    monkeypatch.setattr(environments, "ROOT", workspace_tmp_path)
    monkeypatch.setattr(environments, "_private_root", lambda: None)
    monkeypatch.setattr(environments, "_stop_unit", stopped.append)

    @contextmanager
    def lock(path: Path, *, shared: bool = False, timeout: float = 6):
        yield

    result = environments.remove(digest, lock)

    assert result == {"environment_digest": digest, "removed": True}
    assert not target.exists()
    assert outside.read_text(encoding="utf-8") == "keep"
    assert stopped == [f"xaa-nodeenv-{digest[:32]}.service"]


def test_removal_keeps_digest_when_a_procedure_holds_its_shared_lease(
    workspace_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "c" * 64
    target = workspace_tmp_path / digest
    target.mkdir()
    monkeypatch.setattr(environments, "ROOT", workspace_tmp_path)
    monkeypatch.setattr(environments, "_private_root", lambda: None)

    @contextmanager
    def lock(path: Path, *, shared: bool = False, timeout: float = 6):
        if path.name == "_manager.lock":
            yield
            return
        assert path.name == f"_{digest}.lease"
        assert timeout == 0
        raise TimeoutError("active shared lease")

    with pytest.raises(environments.EnvironmentInUseError, match="running procedure"):
        environments.remove(digest, lock)
    assert target.is_dir()

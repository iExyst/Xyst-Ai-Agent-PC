from contextlib import contextmanager
from pathlib import Path

import pytest
from xaa.runtime import procedure_environment_manager as environments


def test_execution_lease_uses_a_shared_digest_specific_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "a" * 64
    environment = tmp_path / "python"
    monkeypatch.setattr(environments, "execution_path", lambda actual, tree: environment)
    observed: list[tuple[Path, bool]] = []

    @contextmanager
    def lock(path: Path, *, shared: bool = False, timeout: float = 6):
        observed.append((path, shared))
        yield

    with environments.execution_lease(digest, "b" * 64, lock) as selected:
        assert selected == environment
    assert observed == [(environments.ROOT / f"_{digest}.lease", True)]


def test_environment_removal_refuses_an_active_shared_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "c" * 64
    target = tmp_path / digest
    target.mkdir()
    monkeypatch.setattr(environments, "ROOT", tmp_path)
    monkeypatch.setattr(environments, "_private_root", lambda: None)

    @contextmanager
    def lock(path: Path, *, shared: bool = False, timeout: float = 6):
        if path.name == "_manager.lock":
            yield
            return
        assert path.name == f"_{digest}.lease"
        assert timeout == 0
        raise TimeoutError("lease is held")

    with pytest.raises(environments.EnvironmentInUseError, match="active procedure"):
        environments.remove(digest, lock)
    assert target.is_dir()
    assert environments.error_code(environments.EnvironmentInUseError("active")) == (
        "environment_in_use"
    )

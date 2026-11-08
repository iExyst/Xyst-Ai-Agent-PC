import io
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

spec = spec_from_file_location(
    "xaa_build_installer", Path(__file__).parents[2] / "scripts" / "build_installer.py"
)
assert spec is not None and spec.loader is not None
build_installer = module_from_spec(spec)
spec.loader.exec_module(build_installer)


class FakeProcess:
    def __init__(self, output: str, returncode: int) -> None:
        self.stdout = io.StringIO(output)
        self.returncode = returncode

    def wait(self) -> int:
        return self.returncode


def test_tauri_build_retries_only_transient_http_failures(monkeypatch, capsys):
    outcomes = iter(
        [FakeProcess("failed to bundle: HTTP status: 504\n", 1), FakeProcess("built\n", 0)]
    )
    launches: list[tuple[object, dict[str, object]]] = []
    delays: list[int] = []

    def popen(command, **kwargs):
        launches.append((command, kwargs))
        return next(outcomes)

    monkeypatch.setattr(build_installer.subprocess, "Popen", popen)
    monkeypatch.setattr(build_installer.time, "sleep", delays.append)

    build_installer._run_tauri_build("npm.cmd")

    assert len(launches) == 2
    assert launches[0][1]["cwd"] == "apps/desktop"
    assert delays == [5]
    assert "retrying installer build" in capsys.readouterr().out


def test_tauri_build_does_not_retry_non_http_failures(monkeypatch):
    monkeypatch.setattr(
        build_installer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess("invalid bundle configuration\n", 2),
    )
    monkeypatch.setattr(
        build_installer.time, "sleep", lambda *_args: pytest.fail("unexpected retry")
    )

    with pytest.raises(subprocess.CalledProcessError) as failure:
        build_installer._run_tauri_build("npm.cmd")

    assert failure.value.returncode == 2
    assert failure.value.output == "invalid bundle configuration\n"


def test_tauri_build_stops_after_three_transient_failures(monkeypatch):
    monkeypatch.setattr(
        build_installer.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess("download: HTTP status: 503\n", 1),
    )
    delays: list[int] = []
    monkeypatch.setattr(build_installer.time, "sleep", delays.append)

    with pytest.raises(subprocess.CalledProcessError, match="npm.cmd"):
        build_installer._run_tauri_build("npm.cmd")

    assert delays == [5, 10]

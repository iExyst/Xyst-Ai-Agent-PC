import io
import json
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


def test_configured_tauri_signing_uses_verified_sign_command_overlay(monkeypatch):
    captured: list[list[str]] = []

    def record(command):
        captured.append(command)
        config_index = command.index("--config", command.index("--config") + 1)
        overlay = Path(command[config_index + 1])
        assert overlay.is_file()
        payload = json.loads(overlay.read_text(encoding="utf-8"))
        signing = payload["bundle"]["windows"]
        assert signing["digestAlgorithm"] == "sha256"
        assert signing["signCommand"]["args"][-1] == "%1"
        assert "-Files" in signing["signCommand"]["args"]

    monkeypatch.setattr(build_installer.shutil, "which", lambda name: "powershell.exe")
    monkeypatch.setattr(build_installer, "_run_tauri_build_command", record)
    build_installer._run_tauri_build("npm.cmd", signing=True)

    assert len(captured) == 1
    assert "--no-sign" not in captured[0]
    assert captured[0][-2:] == ["--", "--locked"]
    second_separator = captured[0].index("--", captured[0].index("--") + 1)
    assert captured[0].index("--config", captured[0].index("--config") + 1) < second_separator


def test_signing_requires_complete_certificate_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("XAA_SIGNING_CERTIFICATE", raising=False)
    monkeypatch.delenv("XAA_SIGNING_PASSWORD", raising=False)
    assert build_installer._signing_configured() is False

    monkeypatch.setenv("XAA_SIGNING_PASSWORD", "secret")
    with pytest.raises(RuntimeError, match="requires XAA_SIGNING_CERTIFICATE"):
        build_installer._signing_configured()

    monkeypatch.delenv("XAA_SIGNING_PASSWORD")
    certificate = tmp_path / "signing.pfx"
    certificate.write_bytes(b"test placeholder")
    monkeypatch.setenv("XAA_SIGNING_CERTIFICATE", str(certificate))
    with pytest.raises(RuntimeError, match="requires XAA_SIGNING_PASSWORD"):
        build_installer._signing_configured()

    monkeypatch.setenv("XAA_SIGNING_PASSWORD", "secret")
    assert build_installer._signing_configured() is True
    assert build_installer.os.environ["XAA_SIGNING_CERTIFICATE"] == str(certificate.resolve())


def test_signing_powershell_script_parses_without_errors():
    script = Path("scripts/sign_windows_artifacts.ps1").resolve()
    check = (
        "$tokens=$null;$errors=$null;"
        "[System.Management.Automation.Language.Parser]::ParseFile($env:XAA_SCRIPT_TO_PARSE,[ref]$tokens,[ref]$errors)|Out-Null;"
        "$errors|ForEach-Object{$_.ToString()};if($errors.Count){exit 1}"
    )
    environment = dict(build_installer.os.environ)
    environment["XAA_SCRIPT_TO_PARSE"] = str(script)
    subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", check],
        check=True,
        env=environment,
        capture_output=True,
        text=True,
    )


def test_release_installer_requires_current_user_install_mode(tmp_path):
    config = Path("apps/desktop/src-tauri/tauri.release.conf.json")
    assert build_installer._installer_install_mode(config) == "currentUser"

    machine_config = tmp_path / "machine-install.json"
    machine_config.write_text(
        '{"bundle":{"windows":{"nsis":{"installMode":"perMachine"}}}}', encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="currentUser"):
        build_installer._installer_install_mode(machine_config)

"""Tests for optional CI signing secret handling."""

import base64
import importlib.util
from pathlib import Path
from typing import Any, NoReturn

import pytest

MODULE_SPEC = importlib.util.spec_from_file_location(
    "xaa_build_installer_ci", Path(__file__).parents[2] / "scripts" / "build_installer_ci.py"
)
assert MODULE_SPEC and MODULE_SPEC.loader
build_installer_ci = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(build_installer_ci)
ci: Any = build_installer_ci


def _find_uv(_name: str) -> str:
    return "uv.exe"


def test_unsigned_build_strips_signing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    monkeypatch.setattr(ci.shutil, "which", _find_uv)

    def record(command: list[str], *, check: bool, env: dict[str, str]) -> None:
        assert check is True
        calls.append((command, env))

    monkeypatch.setattr(
        ci.subprocess,
        "run",
        record,
    )
    environment = {
        "XAA_SIGNING_CERTIFICATE": "stale.pfx",
        "XAA_SIGNING_PASSWORD": "secret-password",
    }

    assert ci.build(environment) == "unsigned"

    assert calls[0][0] == ["uv.exe", "run", "python", "scripts/build_installer.py"]
    assert not any(key.startswith("XAA_SIGNING_") for key in calls[0][1])


def test_signed_build_uses_and_removes_temporary_certificate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []
    certificate = b"\x30\x03PFX"
    encoded = base64.b64encode(certificate).decode("ascii")
    monkeypatch.setattr(ci.shutil, "which", _find_uv)

    def record(command: list[str], *, check: bool, env: dict[str, str]) -> None:
        assert check is True
        certificate_path = Path(env["XAA_SIGNING_CERTIFICATE"])
        assert certificate_path.read_bytes() == certificate
        assert env["XAA_SIGNING_PASSWORD"] == "secret-password"
        assert "XAA_SIGNING_CERTIFICATE_BASE64" not in env
        assert "secret-password" not in command
        calls.append((command, env))

    monkeypatch.setattr(ci.subprocess, "run", record)
    environment = {
        "XAA_SIGNING_CERTIFICATE_BASE64": encoded,
        "XAA_SIGNING_PASSWORD": "secret-password",
        "RUNNER_TEMP": str(tmp_path),
    }

    assert ci.build(environment) == "signed"

    assert len(calls) == 1
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("encoded", ["not-base64!", base64.b64encode(b"not-pfx").decode()])
def test_invalid_certificate_fails_without_running_builder(
    monkeypatch: pytest.MonkeyPatch, encoded: str
) -> None:
    monkeypatch.setattr(ci.shutil, "which", _find_uv)

    def unexpected(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("builder must not run for invalid certificate data")

    monkeypatch.setattr(
        ci.subprocess,
        "run",
        unexpected,
    )

    with pytest.raises(SystemExit, match="configured signing certificate"):
        ci.build(
            {"XAA_SIGNING_CERTIFICATE_BASE64": encoded, "XAA_SIGNING_PASSWORD": "secret-password"}
        )

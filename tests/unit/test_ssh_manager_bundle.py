from __future__ import annotations

import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
from xaa.runtime.wsl_transport import RESOURCE_NAMES, profile_fingerprint


def load_script(name: str):
    script_path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"xaa_test_{name}", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


install_ssh_manager = load_script("install_ssh_manager")
package_ssh_manager = load_script("package_ssh_manager")


def test_manager_bundle_matches_current_runtime_and_remote_installer(tmp_path: Path) -> None:
    bundle = tmp_path / "xaa-ssh-manager.zip"
    installer = tmp_path / "install_ssh_manager.py"
    package = package_ssh_manager.write_bundle(bundle, installer)

    digest, resources = profile_fingerprint()
    assert package["manager_sha256"] == digest
    assert package["installer_sha256"] == hashlib.sha256(installer.read_bytes()).hexdigest()
    remote_digest, remote_resources = install_ssh_manager.read_bundle(bundle)
    assert remote_digest == digest
    assert tuple(remote_resources) == RESOURCE_NAMES
    assert remote_resources == {path.name: path.read_bytes() for path in resources}

    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["resources"] == {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in resources
    }


def test_manager_bundle_rejects_tampered_runtime_bytes(tmp_path: Path) -> None:
    bundle = tmp_path / "xaa-ssh-manager.zip"
    package_ssh_manager.write_bundle(bundle, tmp_path / "install_ssh_manager.py")
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(tampered, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "wsl_manager.py":
                data += b"\n# tampered fixture\n"
            target.writestr(name, data)

    with pytest.raises(ValueError, match="resource digest mismatch"):
        install_ssh_manager.read_bundle(tampered)


def test_manager_bundle_rejects_missing_or_duplicate_members(tmp_path: Path) -> None:
    bundle = tmp_path / "xaa-ssh-manager.zip"
    package_ssh_manager.write_bundle(bundle, tmp_path / "install_ssh_manager.py")
    broken = tmp_path / "missing.zip"
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(broken, "w") as target:
        for name in source.namelist():
            if name != "node_environment_manager.py":
                target.writestr(name, source.read(name))

    with pytest.raises(ValueError, match="unexpected file inventory"):
        install_ssh_manager.read_bundle(broken)


def test_manager_bundle_outputs_never_overwrite_existing_files(tmp_path: Path) -> None:
    bundle = tmp_path / "xaa-ssh-manager.zip"
    installer = tmp_path / "install_ssh_manager.py"
    bundle.write_bytes(b"owner data")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        package_ssh_manager.write_bundle(bundle, installer)
    assert bundle.read_bytes() == b"owner data"
    assert not installer.exists()


def test_manager_upgrade_refuses_unreconciled_execution_leases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / ("a" * 32 + ".active")).write_text("reserved", encoding="utf-8")
    monkeypatch.setattr(install_ssh_manager, "RECEIPTS", receipts)
    with pytest.raises(RuntimeError, match="execution leases exist"):
        install_ssh_manager._refuse_active_executions()


def test_fixed_manager_wrapper_uses_read_only_shared_upgrade_lock() -> None:
    wrapper = install_ssh_manager._wrapper_contents().decode("utf-8")
    assert "flock --shared /run/lock/xaa-ssh-manager.lock" in wrapper
    assert "/usr/bin/python3 -I /opt/xaa-ssh-manager/current/wsl_manager.py" in wrapper
    for name in RESOURCE_NAMES[1:]:
        assert f"/current/{name}" in wrapper

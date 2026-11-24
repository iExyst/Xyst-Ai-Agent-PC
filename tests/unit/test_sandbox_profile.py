from pathlib import Path

from xaa.runtime.sandbox_profile import powershell_identity_mount, powershell_runtime_mount


def test_powershell_runtime_is_mounted_read_only_at_its_system_path(tmp_path: Path) -> None:
    home = tmp_path / "powershell"
    home.mkdir()
    (home / "pwsh").write_text("runtime", encoding="utf-8")

    assert powershell_runtime_mount(home) == [
        "--dir",
        "/opt",
        "--dir",
        "/opt/microsoft",
        "--dir",
        "/opt/microsoft/powershell",
        "--ro-bind",
        str(home.resolve()),
        "/opt/microsoft/powershell/7",
    ]


def test_powershell_runtime_mount_reuses_existing_opt_directory(tmp_path: Path) -> None:
    home = tmp_path / "powershell"
    home.mkdir()
    (home / "pwsh").write_text("runtime", encoding="utf-8")

    assert powershell_runtime_mount(home, existing_directories={"/opt"}) == [
        "--dir",
        "/opt/microsoft",
        "--dir",
        "/opt/microsoft/powershell",
        "--ro-bind",
        str(home.resolve()),
        "/opt/microsoft/powershell/7",
    ]


def test_missing_powershell_runtime_is_not_mounted(tmp_path: Path) -> None:
    assert powershell_runtime_mount(tmp_path / "not-installed") == []


def test_powershell_identity_mount_uses_private_synthetic_files(tmp_path: Path) -> None:
    passwd = tmp_path / "sandbox-passwd"
    group = tmp_path / "sandbox-group"
    passwd.write_text("nobody:x:65534:65534:nobody:/tmp:/usr/sbin/nologin\n", encoding="ascii")
    group.write_text("nogroup:x:65534:\n", encoding="ascii")

    assert powershell_identity_mount(passwd, group) == [
        "--dir",
        "/etc",
        "--ro-bind",
        str(passwd),
        "/etc/passwd",
        "--ro-bind",
        str(group),
        "/etc/group",
    ]

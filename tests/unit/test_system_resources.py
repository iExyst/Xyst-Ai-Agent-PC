from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from xaa.runtime import system_resources
from xaa.runtime.system_resources import SystemResourceMonitor


def test_parse_linux_cpu_times_includes_iowait_as_idle() -> None:
    assert system_resources.parse_linux_cpu_times(
        "cpu  10 2 3 40 5 1 1 0\ncpu0 5 1 1 20 2 0 0 0\n"
    ) == (62, 45)
    assert system_resources.parse_linux_cpu_times("cpu nope\n") is None
    assert system_resources.parse_linux_cpu_times("intr 10\n") is None


def test_parse_linux_memory_requires_total_and_available() -> None:
    parsed = system_resources.parse_linux_memory(
        "MemTotal:       1000 kB\nMemFree: 200 kB\nMemAvailable: 400 kB\n"
    )
    assert parsed == (1_024_000, 409_600)
    assert system_resources.parse_linux_memory("MemTotal: 1000 kB\n") is None


def test_parse_nvidia_smi_csv_supports_multiple_and_unavailable_metrics() -> None:
    assert system_resources.parse_nvidia_smi_csv(
        "NVIDIA GeForce RTX 5080, 16303, 5762, 95\nNVIDIA GPU, N/A, N/A, 0\nmalformed,row\n"
    ) == [
        {
            "name": "NVIDIA GeForce RTX 5080",
            "memory_total_bytes": 16303 * 1024 * 1024,
            "memory_free_bytes": 5762 * 1024 * 1024,
            "utilization_percent": 95,
        },
        {
            "name": "NVIDIA GPU",
            "memory_total_bytes": None,
            "memory_free_bytes": None,
            "utilization_percent": 0,
        },
    ]


def test_nvidia_smi_path_includes_wsl_driver_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_resources.sys, "platform", "linux")
    wsl_nvidia_smi = Path("/usr/lib/wsl/lib/nvidia-smi")
    monkeypatch.setattr(Path, "is_file", lambda path: path == wsl_nvidia_smi)

    assert system_resources._nvidia_smi_path() == wsl_nvidia_smi


def test_linux_gpu_devices_use_nvidia_smi_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_resources.sys, "platform", "linux")
    monkeypatch.setattr(system_resources, "_dxgi_gpu_devices", list)
    monkeypatch.setattr(system_resources, "linux_amd_gpu_devices", list)
    monkeypatch.setattr(
        system_resources, "_nvidia_smi_path", lambda: Path("/usr/lib/wsl/lib/nvidia-smi")
    )
    monkeypatch.setattr(
        system_resources.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="NVIDIA GeForce RTX 5080, 16303, 11873, 27\n"
        ),
    )

    assert system_resources._gpu_devices() == [
        {
            "name": "NVIDIA GeForce RTX 5080",
            "memory_total_bytes": 16303 * 1024 * 1024,
            "memory_free_bytes": 11873 * 1024 * 1024,
            "utilization_percent": 27,
        }
    ]


def test_gpu_devices_merge_dxgi_memory_with_nvidia_utilization() -> None:
    nvidia_devices = system_resources.parse_nvidia_smi_csv(
        "NVIDIA GeForce RTX 5080, 16384, 8192, 50\n"
    )
    devices = system_resources.merge_gpu_devices(
        [
            {
                "name": "NVIDIA GeForce RTX 5080",
                "memory_total_bytes": 16_000,
                "memory_free_bytes": 9_000,
                "utilization_percent": None,
            },
            {
                "name": "AMD Radeon(TM) Graphics",
                "memory_total_bytes": 2_000,
                "memory_free_bytes": 1_500,
                "utilization_percent": None,
            },
        ],
        nvidia_devices,
    )

    assert devices == [
        {
            "name": "NVIDIA GeForce RTX 5080",
            "memory_total_bytes": 16_384 * 1024 * 1024,
            "memory_free_bytes": 8192 * 1024 * 1024,
            "utilization_percent": 50,
        },
        {
            "name": "AMD Radeon(TM) Graphics",
            "memory_total_bytes": 2_000,
            "memory_free_bytes": 1_500,
            "utilization_percent": None,
        },
    ]


def test_linux_amd_gpu_devices_reads_only_bounded_amd_vram_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(system_resources.sys, "platform", "linux")
    drm = tmp_path / "drm"

    def device(card_name: str, vendor: str, total: str, used: str, name: str = "") -> None:
        path = drm / card_name / "device"
        path.mkdir(parents=True)
        (path / "vendor").write_text(vendor, encoding="ascii")
        (path / "mem_info_vram_total").write_text(total, encoding="ascii")
        (path / "mem_info_vram_used").write_text(used, encoding="ascii")
        if name:
            (path / "product_name").write_text(name, encoding="utf-8")

    device("card0", "0x10de", "800", "300", "NVIDIA ignored")
    device("card1", "0x1002", "1000", "250", "AMD Radeon Fixture")
    device("card2", "0x1002", "400", "900")
    device("card3", "0x1002", "bad", "0")
    (drm / "card0-DP-1").mkdir()

    assert system_resources.linux_amd_gpu_devices(drm) == [
        {
            "name": "AMD Radeon Fixture",
            "memory_total_bytes": 1000,
            "memory_free_bytes": 750,
            "utilization_percent": None,
        },
        {
            "name": "AMD GPU 2",
            "memory_total_bytes": 400,
            "memory_free_bytes": 0,
            "utilization_percent": None,
        },
    ]


def test_linux_amd_gpu_probe_is_disabled_on_other_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(system_resources.sys, "platform", "win32")
    assert system_resources.linux_amd_gpu_devices(tmp_path) == []


def test_monitor_reports_cpu_memory_disk_queue_and_missing_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cpu_samples = iter([(1000, 200), (2000, 500)])
    clocks = iter([1.0, 3.0])
    monkeypatch.setattr(system_resources, "_cpu_times", lambda: next(cpu_samples))
    monkeypatch.setattr(system_resources, "_memory_bytes", lambda: (1_600, 400))
    monkeypatch.setattr(system_resources, "_gpu_devices", list)
    monkeypatch.setattr(system_resources.time, "monotonic", lambda: next(clocks))

    def disk_usage(_path: Path) -> SimpleNamespace:
        return SimpleNamespace(total=10_000, used=4_000, free=6_000)

    monkeypatch.setattr(system_resources.shutil, "disk_usage", disk_usage)
    monitor = SystemResourceMonitor()
    queue = {
        "active": True,
        "waiting": 2,
        "reserved_tokens": 640,
        "max_reserved_tokens": 2_000_000,
        "max_pending": 16,
    }

    first = monitor.snapshot(queue)
    second = monitor.snapshot(queue)

    assert first["cpu_percent"] is None
    assert second["cpu_percent"] == 70.0
    assert second["memory_used_percent"] == 75.0
    assert second["disk_free_bytes"] == 6_000
    assert second["queue_active"] is True
    assert second["queue_waiting"] == 2
    assert second["queue_reserved_tokens"] == 640
    assert second["accelerator_telemetry_available"] is False
    assert not any("path" in key.lower() for key in second)

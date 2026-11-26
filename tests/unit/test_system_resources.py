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
            "device_id": None,
            "name": "NVIDIA GeForce RTX 5080",
            "memory_total_bytes": 16303 * 1024 * 1024,
            "memory_free_bytes": 5762 * 1024 * 1024,
            "utilization_percent": 95,
        },
        {
            "device_id": None,
            "name": "NVIDIA GPU",
            "memory_total_bytes": None,
            "memory_free_bytes": None,
            "utilization_percent": 0,
        },
    ]


def test_parse_nvidia_smi_csv_retains_only_well_formed_uuid() -> None:
    assert system_resources.parse_nvidia_smi_csv(
        "GPU-01234567-89ab-cdef-0123-456789abcdef, Twin GPU, 1000, 800, 20\n"
        "not-a-uuid, Twin GPU, 1000, 700, 30\n"
    ) == [
        {
            "device_id": "nvidia:GPU-01234567-89ab-cdef-0123-456789abcdef",
            "name": "Twin GPU",
            "memory_total_bytes": 1000 * 1024 * 1024,
            "memory_free_bytes": 800 * 1024 * 1024,
            "utilization_percent": 20,
        },
        {
            "device_id": None,
            "name": "Twin GPU",
            "memory_total_bytes": 1000 * 1024 * 1024,
            "memory_free_bytes": 700 * 1024 * 1024,
            "utilization_percent": 30,
        },
    ]


def test_resource_sample_summary_reports_host_and_per_gpu_peaks() -> None:
    result = system_resources.summarize_resource_samples(
        [
            {
                "cpu_percent": 32.5,
                "memory_total_bytes": 1000,
                "memory_available_bytes": 400,
                "queue_active": True,
                "queue_waiting": 1,
                "queue_reserved_weight": 200,
                "gpu_devices": [
                    {
                        "name": "GPU A",
                        "memory_total_bytes": 800,
                        "memory_free_bytes": 500,
                        "utilization_percent": 20,
                    },
                    {
                        "name": "GPU B",
                        "memory_total_bytes": 400,
                        "memory_free_bytes": None,
                        "utilization_percent": None,
                    },
                ],
            },
            {
                "cpu_percent": 81,
                "memory_total_bytes": 1000,
                "memory_available_bytes": 250,
                "queue_active": False,
                "queue_waiting": 0,
                "queue_reserved_weight": 50,
                "gpu_devices": [
                    {
                        "name": "GPU A",
                        "memory_total_bytes": 800,
                        "memory_free_bytes": 200,
                        "utilization_percent": 76,
                    }
                ],
            },
        ]
    )

    assert result == {
        "sample_count": 2,
        "host_cpu_utilization_peak_percent": 81.0,
        "host_ram_used_peak_bytes": 750,
        "queue_active_seen": True,
        "queue_waiting_peak": 1,
        "queue_reserved_weight_peak": 200,
        "gpu_devices": {
            "GPU A (device 1)": {
                "memory_used_peak_bytes": 600,
                "utilization_peak_percent": 76.0,
            },
            "GPU B (device 2)": {
                "memory_used_peak_bytes": None,
                "utilization_peak_percent": None,
            },
        },
        "scope": "host-wide system counters; includes unrelated processes and is not process-attributed",
    }


def test_resource_sample_summary_ignores_malformed_and_out_of_range_counters() -> None:
    result = system_resources.summarize_resource_samples(
        [
            {
                "cpu_percent": 101,
                "memory_total_bytes": 100,
                "memory_available_bytes": 101,
                "gpu_devices": [
                    {
                        "name": "GPU A",
                        "memory_total_bytes": 100,
                        "memory_free_bytes": 101,
                        "utilization_percent": 101,
                    },
                    {"name": "", "memory_total_bytes": 100, "memory_free_bytes": 0},
                ],
            }
        ]
    )

    assert result["host_cpu_utilization_peak_percent"] is None
    assert result["host_ram_used_peak_bytes"] is None
    assert result["gpu_devices"] == {
        "GPU A (device 1)": {
            "memory_used_peak_bytes": None,
            "utilization_peak_percent": None,
        }
    }


def test_resource_sample_summary_keeps_same_name_adapters_separate() -> None:
    result = system_resources.summarize_resource_samples(
        [
            {
                "gpu_devices": [
                    {
                        "name": "Twin GPU",
                        "memory_total_bytes": 1000,
                        "memory_free_bytes": 800,
                        "utilization_percent": 10,
                    },
                    {
                        "name": "Twin GPU",
                        "memory_total_bytes": 1000,
                        "memory_free_bytes": 400,
                        "utilization_percent": 60,
                    },
                ]
            },
            {
                "gpu_devices": [
                    {
                        "name": "Twin GPU",
                        "memory_total_bytes": 1000,
                        "memory_free_bytes": 700,
                        "utilization_percent": 20,
                    },
                    {
                        "name": "Twin GPU",
                        "memory_total_bytes": 1000,
                        "memory_free_bytes": 300,
                        "utilization_percent": 80,
                    },
                ]
            },
        ]
    )

    assert result["gpu_devices"] == {
        "Twin GPU (device 1)": {
            "memory_used_peak_bytes": 300,
            "utilization_peak_percent": 20.0,
        },
        "Twin GPU (device 2)": {
            "memory_used_peak_bytes": 700,
            "utilization_peak_percent": 80.0,
        },
    }


def test_merge_gpu_devices_pairs_duplicate_names_one_to_one() -> None:
    host_devices = [
        {
            "name": "Twin GPU",
            "memory_total_bytes": 1000,
            "memory_free_bytes": None,
            "utilization_percent": 25,
        },
        {
            "name": "Twin GPU",
            "memory_total_bytes": 1000,
            "memory_free_bytes": None,
            "utilization_percent": 75,
        },
    ]
    driver_devices = [
        {
            "name": "Twin GPU",
            "memory_total_bytes": 1000,
            "memory_free_bytes": 800,
            "utilization_percent": None,
        },
        {
            "name": "Twin GPU",
            "memory_total_bytes": 1000,
            "memory_free_bytes": 400,
            "utilization_percent": None,
        },
    ]

    assert system_resources.merge_gpu_devices(host_devices, driver_devices) == [
        {
            "name": "Twin GPU",
            "memory_total_bytes": 1000,
            "memory_free_bytes": 800,
            "utilization_percent": 25,
        },
        {
            "name": "Twin GPU",
            "memory_total_bytes": 1000,
            "memory_free_bytes": 400,
            "utilization_percent": 75,
        },
    ]


def test_resource_sample_summary_tracks_same_name_adapters_by_stable_id() -> None:
    result = system_resources.summarize_resource_samples(
        [
            {
                "gpu_devices": [
                    {
                        "device_id": "dxgi:00000000:00000001",
                        "name": "Twin GPU",
                        "memory_total_bytes": 1000,
                        "memory_free_bytes": 900,
                        "utilization_percent": 10,
                    },
                    {
                        "device_id": "dxgi:00000000:00000002",
                        "name": "Twin GPU",
                        "memory_total_bytes": 1000,
                        "memory_free_bytes": 800,
                        "utilization_percent": 20,
                    },
                ]
            },
            {
                "gpu_devices": [
                    {
                        "device_id": "dxgi:00000000:00000002",
                        "name": "Twin GPU",
                        "memory_total_bytes": 1000,
                        "memory_free_bytes": 300,
                        "utilization_percent": 80,
                    },
                    {
                        "device_id": "dxgi:00000000:00000001",
                        "name": "Twin GPU",
                        "memory_total_bytes": 1000,
                        "memory_free_bytes": 600,
                        "utilization_percent": 40,
                    },
                ]
            },
        ]
    )

    assert result["gpu_devices"] == {
        "Twin GPU (device 1)": {"memory_used_peak_bytes": 400, "utilization_peak_percent": 40.0},
        "Twin GPU (device 2)": {"memory_used_peak_bytes": 700, "utilization_peak_percent": 80.0},
    }


def test_merge_gpu_devices_prefers_matching_stable_ids_over_enumeration_order() -> None:
    host_devices = [
        {"device_id": "dxgi:00000000:00000001", "name": "Twin GPU", "memory_free_bytes": None},
        {"device_id": "dxgi:00000000:00000002", "name": "Twin GPU", "memory_free_bytes": None},
    ]
    driver_devices = [
        {"device_id": "dxgi:00000000:00000002", "name": "Twin GPU", "memory_free_bytes": 200},
        {"device_id": "dxgi:00000000:00000001", "name": "Twin GPU", "memory_free_bytes": 100},
    ]

    assert system_resources.merge_gpu_devices(host_devices, driver_devices) == [
        {"device_id": "dxgi:00000000:00000001", "name": "Twin GPU", "memory_free_bytes": 100},
        {"device_id": "dxgi:00000000:00000002", "name": "Twin GPU", "memory_free_bytes": 200},
    ]


def test_windows_gpu_engine_counters_aggregate_processes_per_physical_engine() -> None:
    counters = [
        ("pid_10_luid_0x00000000_0x00016631_phys_0_eng_0_engtype_3D", 20.0),
        ("pid_11_luid_0x00000000_0x00016631_phys_0_eng_0_engtype_3D", 25.0),
        ("pid_12_luid_0x00000000_0x00016631_phys_0_eng_1_engtype_Compute_0", 63.0),
        ("pid_13_luid_0x00000000_0x0001A4CB_phys_0_eng_0_engtype_3D", 41.0),
        ("not-a-gpu-engine-instance", 99.0),
        ("pid_14_luid_0x00000000_0x00016631_phys_0_eng_2_engtype_Copy", 101.0),
    ]

    assert system_resources.aggregate_windows_gpu_engine_counters(counters) == {
        (0, 0x16631): 63,
        (0, 0x1A4CB): 41,
    }


def test_windows_gpu_engine_counter_sum_is_bounded_at_one_hundred() -> None:
    counters = [
        ("pid_10_luid_0x00000001_0x00000002_phys_0_eng_4_engtype_3D", 75.0),
        ("pid_11_luid_0x00000001_0x00000002_phys_0_eng_4_engtype_3D", 50.0),
    ]

    assert system_resources.aggregate_windows_gpu_engine_counters(counters) == {
        (1, 2): 100,
    }


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
            "device_id": None,
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
            "device_id": "drm:card1",
            "name": "AMD Radeon Fixture",
            "memory_total_bytes": 1000,
            "memory_free_bytes": 750,
            "utilization_percent": None,
        },
        {
            "device_id": "drm:card2",
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
    monkeypatch.setattr(system_resources.sys, "platform", "linux")
    cpu_samples = iter([(1000, 200), (2000, 500)])
    clocks = iter([1.0, 3.0])
    monkeypatch.setattr(system_resources, "_cpu_times", lambda: next(cpu_samples))
    monkeypatch.setattr(system_resources, "_memory_bytes", lambda: (1_600, 400))
    monkeypatch.setattr(system_resources, "_gpu_devices", lambda *_args: [])
    monkeypatch.setattr(system_resources.time, "monotonic", lambda: next(clocks))

    def disk_usage(_path: Path) -> SimpleNamespace:
        return SimpleNamespace(total=10_000, used=4_000, free=6_000)

    monkeypatch.setattr(system_resources.shutil, "disk_usage", disk_usage)
    monitor = SystemResourceMonitor()
    queue = {
        "active": True,
        "waiting": 2,
        "reserved_weight": 640,
        "max_reserved_weight": 2_000_000,
        "max_pending": 16,
    }

    first = monitor.snapshot(queue)
    second = monitor.snapshot(queue)

    assert first["cpu_percent"] is None
    assert second["cpu_percent"] == 70.0
    assert second["memory_used_percent"] == 75.0
    assert second["disk_free_bytes"] == 6_000
    assert second["queue_active"] is True
    assert second["queue_other_process_active"] is None
    assert second["queue_waiting"] == 2
    assert second["queue_reserved_weight"] == 640
    assert second["accelerator_telemetry_available"] is False
    monitor.close()
    assert not any("path" in key.lower() for key in second)

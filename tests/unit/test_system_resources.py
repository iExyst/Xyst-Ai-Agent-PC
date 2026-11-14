from __future__ import annotations

from types import SimpleNamespace

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


def test_monitor_reports_cpu_memory_disk_and_queue_without_gpu_claim(monkeypatch) -> None:
    cpu_samples = iter([(1000, 200), (2000, 500)])
    clocks = iter([1.0, 3.0])
    monkeypatch.setattr(system_resources, "_cpu_times", lambda: next(cpu_samples))
    monkeypatch.setattr(system_resources, "_memory_bytes", lambda: (1_600, 400))
    monkeypatch.setattr(system_resources.time, "monotonic", lambda: next(clocks))
    monkeypatch.setattr(
        system_resources.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=10_000, used=4_000, free=6_000),
    )
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

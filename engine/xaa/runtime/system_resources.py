"""Local host and LM Studio queue measurements for the desktop monitor."""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _filetime_value(value: Any) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def _windows_cpu_times() -> tuple[int, int] | None:
    class FileTime(ctypes.Structure):
        _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]

    idle, kernel, user = FileTime(), FileTime(), FileTime()
    get_system_times = ctypes.windll.kernel32.GetSystemTimes
    get_system_times.argtypes = [
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
        ctypes.POINTER(FileTime),
    ]
    get_system_times.restype = ctypes.c_int
    if not get_system_times(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    idle_ticks = _filetime_value(idle)
    total_ticks = _filetime_value(kernel) + _filetime_value(user)
    return (total_ticks, idle_ticks)


def parse_linux_cpu_times(text: str) -> tuple[int, int] | None:
    """Return total and idle ticks from the aggregate /proc/stat CPU row."""
    first = text.splitlines()[0].split() if text.splitlines() else []
    if len(first) < 5 or first[0] != "cpu":
        return None
    try:
        values = [int(value) for value in first[1:]]
    except ValueError:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def _cpu_times() -> tuple[int, int] | None:
    if sys.platform == "win32":
        try:
            return _windows_cpu_times()
        except (AttributeError, OSError):
            return None
    if sys.platform.startswith("linux"):
        try:
            return parse_linux_cpu_times(Path("/proc/stat").read_text(encoding="ascii"))
        except OSError:
            return None
    return None


def parse_linux_memory(text: str) -> tuple[int, int] | None:
    """Return total and available bytes from /proc/meminfo."""
    fields: dict[str, int] = {}
    for line in text.splitlines():
        name, separator, value = line.partition(":")
        if not separator:
            continue
        parts = value.split()
        if len(parts) >= 2 and parts[1] == "kB":
            try:
                fields[name] = int(parts[0]) * 1024
            except ValueError:
                continue
    total = fields.get("MemTotal")
    available = fields.get("MemAvailable")
    if total is None or available is None or total <= 0:
        return None
    return total, max(0, min(total, available))


def _memory_bytes() -> tuple[int, int] | None:
    if sys.platform == "win32":
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        try:
            get_status = ctypes.windll.kernel32.GlobalMemoryStatusEx
            get_status.argtypes = [ctypes.POINTER(MemoryStatusEx)]
            get_status.restype = ctypes.c_int
            if get_status(ctypes.byref(status)):
                return int(status.ullTotalPhys), int(status.ullAvailPhys)
        except (AttributeError, OSError):
            return None
        return None
    if sys.platform.startswith("linux"):
        try:
            return parse_linux_memory(Path("/proc/meminfo").read_text(encoding="ascii"))
        except OSError:
            return None
    return None


class SystemResourceMonitor:
    """Sample host capacity and report conservative queue measurements."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previous_cpu: tuple[float, tuple[int, int]] | None = None

    @staticmethod
    def _cpu_percent(previous: tuple[int, int], current: tuple[int, int]) -> float | None:
        total_delta = current[0] - previous[0]
        idle_delta = current[1] - previous[1]
        if total_delta <= 0 or idle_delta < 0:
            return None
        return round(max(0.0, min(100.0, (total_delta - idle_delta) * 100 / total_delta)), 1)

    def snapshot(self, queue: dict[str, int | bool]) -> dict[str, Any]:
        now = time.monotonic()
        current_cpu = _cpu_times()
        cpu_percent: float | None = None
        with self._lock:
            previous = self._previous_cpu
            if current_cpu is not None:
                self._previous_cpu = (now, current_cpu)
            if previous is not None and current_cpu is not None and now > previous[0]:
                cpu_percent = self._cpu_percent(previous[1], current_cpu)

        memory = _memory_bytes()
        try:
            disk = shutil.disk_usage(Path.home())
            disk_total, disk_free = int(disk.total), int(disk.free)
        except OSError:
            disk_total = disk_free = None
        memory_total, memory_available = memory if memory is not None else (None, None)
        memory_used_percent = (
            round((memory_total - memory_available) * 100 / memory_total, 1)
            if memory_total and memory_available is not None
            else None
        )
        return {
            "sampled_at": datetime.now(UTC).isoformat(),
            "platform": sys.platform,
            "logical_processors": os.cpu_count() or 1,
            "cpu_percent": cpu_percent,
            "memory_total_bytes": memory_total,
            "memory_available_bytes": memory_available,
            "memory_used_percent": memory_used_percent,
            "disk_total_bytes": disk_total,
            "disk_free_bytes": disk_free,
            "queue_active": bool(queue["active"]),
            "queue_waiting": int(queue["waiting"]),
            "queue_reserved_tokens": int(queue["reserved_tokens"]),
            "queue_max_reserved_tokens": int(queue["max_reserved_tokens"]),
            "queue_max_pending": int(queue["max_pending"]),
            "accelerator_telemetry_available": False,
        }

"""Local host and LM Studio queue measurements for the desktop monitor."""

from __future__ import annotations

import csv
import ctypes
import ctypes.wintypes
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from itertools import islice
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


def parse_nvidia_smi_csv(text: str) -> list[dict[str, Any]]:
    """Parse bounded `nvidia-smi` CSV output without assuming a GPU model."""
    devices: list[dict[str, Any]] = []
    for row in csv.reader(text.splitlines()):
        if len(row) != 4 or len(devices) >= 8:
            continue
        name = row[0].strip()[:128]
        if not name:
            continue

        def integer(value: str) -> int | None:
            try:
                parsed = int(value.strip())
            except ValueError:
                return None
            return parsed if parsed >= 0 else None

        total_mib, free_mib, utilization = (integer(value) for value in row[1:])
        total = total_mib * 1024 * 1024 if total_mib is not None else None
        free = (
            min(total, free_mib * 1024 * 1024)
            if total is not None and free_mib is not None
            else free_mib * 1024 * 1024
            if free_mib is not None
            else None
        )
        devices.append(
            {
                "name": name,
                "memory_total_bytes": total,
                "memory_free_bytes": free,
                "utilization_percent": min(utilization, 100) if utilization is not None else None,
            }
        )
    return devices


def _nvidia_smi_path() -> Path | None:
    if sys.platform == "win32":
        system_directory = ctypes.create_unicode_buffer(32768)
        get_system_directory = ctypes.windll.kernel32.GetSystemDirectoryW
        get_system_directory.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        get_system_directory.restype = ctypes.c_uint
        length = get_system_directory(system_directory, len(system_directory))
        if not length or length >= len(system_directory):
            return None
        candidates = [Path(system_directory.value) / "nvidia-smi.exe"]
    else:
        candidates = [
            Path("/usr/bin/nvidia-smi"),
            Path("/usr/local/bin/nvidia-smi"),
            Path("/usr/lib/wsl/lib/nvidia-smi"),
        ]
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def linux_amd_gpu_devices(drm_root: Path = Path("/sys/class/drm")) -> list[dict[str, Any]]:
    """Read bounded system-wide AMD VRAM counters exposed by Linux DRM sysfs."""
    if not sys.platform.startswith("linux"):
        return []
    devices: list[dict[str, Any]] = []
    try:
        card_paths = (path for path in drm_root.glob("card[0-9]*") if path.name[4:].isdigit())
        cards = list(islice(card_paths, 32))
    except OSError:
        return []
    for card in cards:
        if len(devices) >= 8:
            continue
        device = card / "device"
        try:
            vendor = (device / "vendor").read_text(encoding="ascii").strip().lower()
            if vendor != "0x1002":
                continue
            total = int((device / "mem_info_vram_total").read_text(encoding="ascii").strip())
            used = int((device / "mem_info_vram_used").read_text(encoding="ascii").strip())
            if total <= 0 or used < 0:
                continue
        except (OSError, UnicodeError, ValueError):
            continue
        try:
            name = (device / "product_name").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            name = ""
        if not name:
            name = f"AMD GPU {card.name[4:]}"
        devices.append(
            {
                "name": name[:128],
                "memory_total_bytes": total,
                "memory_free_bytes": max(0, total - min(used, total)),
                "utilization_percent": None,
            }
        )
    return devices


def _com_method(instance: ctypes.c_void_p, index: int, restype: Any, argtypes: list[Any]) -> Any:
    """Resolve one method from a COM interface vtable."""
    table = ctypes.cast(instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    function_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
        restype, ctypes.c_void_p, *argtypes
    )
    return function_type(table[index])


def _dxgi_gpu_devices() -> list[dict[str, Any]]:
    """Read Windows GPU adapter identities and system-wide dedicated-memory usage."""
    if sys.platform != "win32":
        return []

    class AdapterDesc1(ctypes.Structure):
        _fields_ = [
            ("Description", ctypes.c_wchar * 128),
            ("VendorId", ctypes.wintypes.UINT),
            ("DeviceId", ctypes.wintypes.UINT),
            ("SubSysId", ctypes.wintypes.UINT),
            ("Revision", ctypes.wintypes.UINT),
            ("DedicatedVideoMemory", ctypes.c_size_t),
            ("DedicatedSystemMemory", ctypes.c_size_t),
            ("SharedSystemMemory", ctypes.c_size_t),
            ("AdapterLuid", ctypes.c_longlong),
            ("Flags", ctypes.wintypes.UINT),
        ]

    class PdhValueUnion(ctypes.Union):
        _fields_ = [
            ("long_value", ctypes.c_long),
            ("double_value", ctypes.c_double),
            ("large_value", ctypes.c_longlong),
            ("ansi_string", ctypes.c_char_p),
            ("wide_string", ctypes.c_wchar_p),
        ]

    class PdhFormattedValue(ctypes.Structure):
        _fields_ = [("status", ctypes.wintypes.DWORD), ("value", PdhValueUnion)]

    factory = ctypes.c_void_p()
    pdh_query = ctypes.c_void_p()
    adapters: list[ctypes.c_void_p] = []
    pdh = None
    try:
        dxgi = ctypes.WinDLL("dxgi.dll", use_last_error=True)
        create_factory = dxgi.CreateDXGIFactory1
        create_factory.argtypes = [ctypes.POINTER(ctypes.c_byte), ctypes.POINTER(ctypes.c_void_p)]
        create_factory.restype = ctypes.c_long
        factory_iid = (ctypes.c_byte * 16)(
            0x78,
            0xAE,
            0x0A,
            0x77,
            0x6F,
            0xF2,
            0xBA,
            0x4D,
            0xA8,
            0x29,
            0x25,
            0x3C,
            0x83,
            0xD1,
            0xB3,
            0x87,
        )  # IDXGIFactory1
        if (
            create_factory(
                ctypes.cast(factory_iid, ctypes.POINTER(ctypes.c_byte)), ctypes.byref(factory)
            )
            < 0
            or not factory.value
        ):
            return []

        enum_adapters = _com_method(
            factory, 12, ctypes.c_long, [ctypes.wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)]
        )
        devices: list[dict[str, Any]] = []
        pdh = ctypes.WinDLL("pdh.dll", use_last_error=True)
        open_query = pdh.PdhOpenQueryW
        open_query.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        open_query.restype = ctypes.wintypes.DWORD
        add_counter = pdh.PdhAddEnglishCounterW
        add_counter.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        add_counter.restype = ctypes.wintypes.DWORD
        collect_query = pdh.PdhCollectQueryData
        collect_query.argtypes = [ctypes.c_void_p]
        collect_query.restype = ctypes.wintypes.DWORD
        get_formatted = pdh.PdhGetFormattedCounterValue
        get_formatted.argtypes = [
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.DWORD),
            ctypes.POINTER(PdhFormattedValue),
        ]
        get_formatted.restype = ctypes.wintypes.DWORD
        if open_query(None, 0, ctypes.byref(pdh_query)) != 0:
            pdh_query = ctypes.c_void_p()
        pdh_counters: list[tuple[ctypes.c_void_p, int]] = []
        not_found = ctypes.c_long(0x887A0002).value
        for index in range(16):
            adapter = ctypes.c_void_p()
            result = enum_adapters(factory, index, ctypes.byref(adapter))
            if result == not_found or result < 0 or not adapter.value:
                break
            adapters.append(adapter)
            desc = AdapterDesc1()
            get_desc = _com_method(adapter, 10, ctypes.c_long, [ctypes.POINTER(AdapterDesc1)])
            if get_desc(adapter, ctypes.byref(desc)) < 0 or desc.Flags & 2:
                continue  # Skip software/WARP adapters.

            total = int(desc.DedicatedVideoMemory)
            name = desc.Description.strip()[:128]
            if not name or total <= 0:
                continue  # Integrated/shared memory is represented by host RAM.
            luid = ctypes.c_uint64(desc.AdapterLuid).value
            high, low = luid >> 32, luid & 0xFFFFFFFF
            counter = ctypes.c_void_p()
            if pdh_query.value:
                path = (
                    f"\\GPU Adapter Memory(luid_0x{high:08x}_0x{low:08x}_phys_0)\\Dedicated Usage"
                )
                if add_counter(pdh_query, path, 0, ctypes.byref(counter)) == 0 and counter.value:
                    pdh_counters.append((counter, len(devices)))
            devices.append(
                {
                    "name": name,
                    "memory_total_bytes": total,
                    "memory_free_bytes": None,
                    "utilization_percent": None,
                }
            )

        # Unlike DXGI's QueryVideoMemoryInfo usage, these performance counters
        # represent device usage across processes, including LM Studio.
        if pdh_query.value and collect_query(pdh_query) == 0:
            for counter, device_index in pdh_counters:
                counter_type = ctypes.wintypes.DWORD()
                value = PdhFormattedValue()
                if (
                    get_formatted(
                        counter, 0x00000400, ctypes.byref(counter_type), ctypes.byref(value)
                    )
                    != 0
                ):
                    continue
                if value.status != 0 or value.value.large_value < 0:
                    continue
                device = devices[device_index]
                total = device["memory_total_bytes"]
                if isinstance(total, int):
                    device["memory_free_bytes"] = max(0, total - value.value.large_value)
        return devices[:8]
    except (AttributeError, OSError, TypeError, ValueError):
        return []
    finally:
        for adapter in adapters:
            if adapter.value:
                _com_method(adapter, 2, ctypes.c_ulong, [])(adapter)
        if factory.value:
            _com_method(factory, 2, ctypes.c_ulong, [])(factory)
        if pdh_query.value and pdh is not None:
            close_query = pdh.PdhCloseQuery
            close_query.argtypes = [ctypes.c_void_p]
            close_query.restype = ctypes.wintypes.DWORD
            close_query(pdh_query)


def _gpu_devices() -> list[dict[str, Any]]:
    devices = _dxgi_gpu_devices()
    if sys.platform.startswith("linux"):
        devices = merge_gpu_devices(devices, linux_amd_gpu_devices())
    executable = _nvidia_smi_path()
    if executable is not None:
        try:
            result = subprocess.run(
                [
                    str(executable),
                    "--query-gpu=name,memory.total,memory.free,utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=1.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            devices = merge_gpu_devices(devices, parse_nvidia_smi_csv(result.stdout[:8192]))
        except (OSError, subprocess.SubprocessError):
            pass
    return devices[:8]


def merge_gpu_devices(
    host_devices: list[dict[str, Any]], driver_devices: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Keep host-wide devices while preferring matching vendor-driver metrics."""
    devices = list(host_devices)
    by_name = {device["name"].casefold(): index for index, device in enumerate(devices)}
    for device in driver_devices:
        match = by_name.get(device["name"].casefold())
        if match is None:
            by_name[device["name"].casefold()] = len(devices)
            devices.append(device)
        else:
            devices[match] = device
    return devices[:8]


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
        gpu_devices = _gpu_devices()
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
            "gpu_devices": gpu_devices,
            "accelerator_telemetry_available": bool(gpu_devices),
        }

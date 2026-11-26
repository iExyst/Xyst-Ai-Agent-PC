"""Local host and LM Studio queue measurements for the desktop monitor."""

from __future__ import annotations

import csv
import ctypes
import ctypes.wintypes
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any, cast


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


_GPU_ENGINE_INSTANCE = re.compile(
    r"luid_(0x[0-9a-f]+)_(0x[0-9a-f]+)_phys_(\d+)_eng_(\d+)_engtype_",
    re.IGNORECASE,
)


def aggregate_windows_gpu_engine_counters(
    counters: list[tuple[str, float]],
) -> dict[tuple[int, int], int]:
    """Return the busiest physical engine per adapter across process instances."""
    per_engine: dict[tuple[int, int, int, int], float] = {}
    for instance, raw_value in counters[:8192]:
        match = _GPU_ENGINE_INSTANCE.search(instance)
        if match is None:
            continue
        try:
            value = float(raw_value)
            adapter = (int(match.group(1), 16), int(match.group(2), 16))
            physical, engine = int(match.group(3)), int(match.group(4))
        except (OverflowError, ValueError):
            continue
        if not 0 <= value <= 100:
            continue
        key = (*adapter, physical, engine)
        per_engine[key] = min(100.0, per_engine.get(key, 0.0) + value)

    busiest: dict[tuple[int, int], float] = {}
    for (high, low, _physical, _engine), utilization in per_engine.items():
        adapter = (high, low)
        busiest[adapter] = max(busiest.get(adapter, 0.0), utilization)
    return {adapter: round(value) for adapter, value in busiest.items()}


class _PdhFormattedValueUnion(ctypes.Union):
    _fields_ = [
        ("long_value", ctypes.c_long),
        ("double_value", ctypes.c_double),
        ("large_value", ctypes.c_longlong),
        ("ansi_string", ctypes.c_char_p),
        ("wide_string", ctypes.c_wchar_p),
    ]


class _PdhFormattedValue(ctypes.Structure):
    _fields_ = [("status", ctypes.wintypes.DWORD), ("value", _PdhFormattedValueUnion)]


class _PdhFormattedCounterValueItem(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("formatted", _PdhFormattedValue)]


class _WindowsGpuEngineSampler:
    """Keep one PDH wildcard query so utilization has successive samples."""

    _PDH_MORE_DATA = 0x800007D2
    _MAX_BUFFER_BYTES = 4 * 1024 * 1024
    _MAX_COUNTERS = 8192

    def __init__(self) -> None:
        self._pdh: Any = None
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()
        self._last_sample_at: float | None = None
        self._last_values: dict[tuple[int, int], int] = {}
        try:
            self._pdh = ctypes.WinDLL("pdh.dll", use_last_error=True)
            open_query = self._pdh.PdhOpenQueryW
            open_query.argtypes = [
                ctypes.c_wchar_p,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            open_query.restype = ctypes.wintypes.DWORD
            add_counter = self._pdh.PdhAddEnglishCounterW
            add_counter.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_size_t,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            add_counter.restype = ctypes.wintypes.DWORD
            collect = self._pdh.PdhCollectQueryData
            collect.argtypes = [ctypes.c_void_p]
            collect.restype = ctypes.wintypes.DWORD
            if open_query(None, 0, ctypes.byref(self._query)) != 0:
                self.close()
                return
            if (
                add_counter(
                    self._query,
                    r"\GPU Engine(*)\Utilization Percentage",
                    0,
                    ctypes.byref(self._counter),
                )
                != 0
            ):
                self.close()
                return
            # Prime rate counters; the next sample supplies a meaningful interval.
            collect(self._query)
        except (AttributeError, OSError, TypeError, ValueError):
            self.close()

    def close(self) -> None:
        if self._query.value and self._pdh is not None:
            close_query = self._pdh.PdhCloseQuery
            close_query.argtypes = [ctypes.c_void_p]
            close_query.restype = ctypes.wintypes.DWORD
            close_query(self._query)
        self._query = ctypes.c_void_p()
        self._counter = ctypes.c_void_p()

    def sample(self) -> dict[tuple[int, int], int]:
        if not self._query.value or not self._counter.value or self._pdh is None:
            return {}
        now = time.monotonic()
        if self._last_sample_at is not None and now - self._last_sample_at < 1.0:
            return dict(self._last_values)
        try:
            if self._pdh.PdhCollectQueryData(self._query) != 0:
                return {}
            get_array = self._pdh.PdhGetFormattedCounterArrayW
            get_array.argtypes = [
                ctypes.c_void_p,
                ctypes.wintypes.DWORD,
                ctypes.POINTER(ctypes.wintypes.DWORD),
                ctypes.POINTER(ctypes.wintypes.DWORD),
                ctypes.c_void_p,
            ]
            get_array.restype = ctypes.wintypes.DWORD
            buffer_size = ctypes.wintypes.DWORD()
            item_count = ctypes.wintypes.DWORD()
            status = get_array(
                self._counter,
                0x00000200,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                None,
            )
            if status != self._PDH_MORE_DATA or not 0 < buffer_size.value <= self._MAX_BUFFER_BYTES:
                return {}
            buffer = ctypes.create_string_buffer(buffer_size.value)
            status = get_array(
                self._counter,
                0x00000200,
                ctypes.byref(buffer_size),
                ctypes.byref(item_count),
                ctypes.cast(buffer, ctypes.c_void_p),
            )
            if status != 0 or item_count.value > self._MAX_COUNTERS:
                return {}
            items = ctypes.cast(buffer, ctypes.POINTER(_PdhFormattedCounterValueItem))
            counters: list[tuple[str, float]] = []
            for index in range(item_count.value):
                item = items[index]
                if item.name and item.formatted.status == 0:
                    counters.append((item.name, item.formatted.value.double_value))
            self._last_values = aggregate_windows_gpu_engine_counters(counters)
            self._last_sample_at = now
            return dict(self._last_values)
        except (AttributeError, OSError, TypeError, ValueError):
            return {}


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


def _dxgi_gpu_devices(
    utilization_by_luid: dict[tuple[int, int], int] | None = None,
) -> list[dict[str, Any]]:
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
                    "utilization_percent": (utilization_by_luid or {}).get((high, low)),
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


def _gpu_devices(
    utilization_by_luid: dict[tuple[int, int], int] | None = None,
) -> list[dict[str, Any]]:
    devices = (
        _dxgi_gpu_devices()
        if utilization_by_luid is None
        else _dxgi_gpu_devices(utilization_by_luid)
    )
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


def summarize_resource_samples(samples: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Summarize host-wide peak counters without implying process attribution."""
    cpu_peak: float | None = None
    ram_peak: int | None = None
    gpu_peaks: dict[str, dict[str, int | float | None]] = {}
    queue_waiting_peak = 0
    queue_reserved_tokens_peak = 0
    queue_active_seen = False
    for sample in samples:
        cpu = sample.get("cpu_percent")
        if isinstance(cpu, (int, float)) and not isinstance(cpu, bool):
            cpu_value = float(cpu)
            if math.isfinite(cpu_value) and 0 <= cpu_value <= 100:
                cpu_peak = max(cpu_value, cpu_peak) if cpu_peak is not None else cpu_value

        total = sample.get("memory_total_bytes")
        available = sample.get("memory_available_bytes")
        if type(total) is int and type(available) is int and 0 <= available <= total:
            used = total - available
            ram_peak = max(used, ram_peak) if ram_peak is not None else used

        waiting = sample.get("queue_waiting")
        if type(waiting) is int and waiting >= 0:
            queue_waiting_peak = max(queue_waiting_peak, waiting)
        reserved_tokens = sample.get("queue_reserved_tokens")
        if type(reserved_tokens) is int and reserved_tokens >= 0:
            queue_reserved_tokens_peak = max(queue_reserved_tokens_peak, reserved_tokens)
        queue_active_seen = queue_active_seen or sample.get("queue_active") is True

        devices = sample.get("gpu_devices")
        if not isinstance(devices, list):
            continue
        for value in cast(list[object], devices):
            if not isinstance(value, dict):
                continue
            device = cast(dict[str, object], value)
            name = device.get("name")
            if not isinstance(name, str) or not name:
                continue
            current = gpu_peaks.setdefault(
                name, {"memory_used_peak_bytes": None, "utilization_peak_percent": None}
            )
            gpu_total = device.get("memory_total_bytes")
            gpu_free = device.get("memory_free_bytes")
            if type(gpu_total) is int and type(gpu_free) is int and 0 <= gpu_free <= gpu_total:
                gpu_used = gpu_total - gpu_free
                previous_used = current["memory_used_peak_bytes"]
                current["memory_used_peak_bytes"] = (
                    gpu_used if previous_used is None else max(gpu_used, int(previous_used))
                )
            utilization = device.get("utilization_percent")
            if isinstance(utilization, (int, float)) and not isinstance(utilization, bool):
                utilization_value = float(utilization)
                if math.isfinite(utilization_value) and 0 <= utilization_value <= 100:
                    previous_utilization = current["utilization_peak_percent"]
                    current["utilization_peak_percent"] = (
                        utilization_value
                        if previous_utilization is None
                        else max(utilization_value, float(previous_utilization))
                    )
    return {
        "sample_count": len(samples),
        "host_cpu_utilization_peak_percent": cpu_peak,
        "host_ram_used_peak_bytes": ram_peak,
        "queue_active_seen": queue_active_seen,
        "queue_waiting_peak": queue_waiting_peak,
        "queue_reserved_tokens_peak": queue_reserved_tokens_peak,
        "gpu_devices": gpu_peaks,
        "scope": "host-wide system counters; includes unrelated processes and is not process-attributed",
    }


class SystemResourceMonitor:
    """Sample host capacity and report conservative queue measurements."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._previous_cpu: tuple[float, tuple[int, int]] | None = None
        self._gpu_engine_sampler: _WindowsGpuEngineSampler | None = None

    def close(self) -> None:
        """Release the persistent Windows performance-counter query, if opened."""
        with self._lock:
            if self._gpu_engine_sampler is not None:
                self._gpu_engine_sampler.close()
                self._gpu_engine_sampler = None

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
        utilization_by_luid: dict[tuple[int, int], int] | None = None
        with self._lock:
            previous = self._previous_cpu
            if current_cpu is not None:
                self._previous_cpu = (now, current_cpu)
            if previous is not None and current_cpu is not None and now > previous[0]:
                cpu_percent = self._cpu_percent(previous[1], current_cpu)
            if sys.platform == "win32":
                if self._gpu_engine_sampler is None:
                    self._gpu_engine_sampler = _WindowsGpuEngineSampler()
                utilization_by_luid = self._gpu_engine_sampler.sample()

        memory = _memory_bytes()
        try:
            disk = shutil.disk_usage(Path.home())
            disk_total, disk_free = int(disk.total), int(disk.free)
        except OSError:
            disk_total = disk_free = None
        memory_total, memory_available = memory if memory is not None else (None, None)
        gpu_devices = _gpu_devices(utilization_by_luid)
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

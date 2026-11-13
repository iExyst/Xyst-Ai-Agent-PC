# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
"""Launch a one-shot MCP stdio child in a Windows AppContainer.

The wrapper is started by the MCP SDK and assigned to its existing Job Object
before it receives a bootstrap record. It then creates the actual server child
with no network capabilities and inherits the wrapper's protocol pipe handles.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import marshal
import os
import re
import subprocess
import sys
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

BOOTSTRAP_PREFIX = b"XAA_APPCONTAINER_V1 "
READY_FRAME = b"\x00XAA_APPCONTAINER_READY\n"
ERROR_FRAME_PREFIX = b"\x00XAE"
MAX_STARTUP_ERROR_FRAME_BYTES = 32
MAX_BOOTSTRAP_BYTES = 64 * 1024
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_GENERIC_READ = 0x120089
FILE_GENERIC_WRITE = 0x120116
FILE_GENERIC_EXECUTE = 0x1200A0
MOVEFILE_REPLACE_EXISTING = 0x00000001
MOVEFILE_WRITE_THROUGH = 0x00000008
MAX_ACL_OBJECTS = 50_000
_TEMP_JOURNAL_NAME = re.compile(r"\.(XAA-MCP-[0-9a-f]{32})-(\d+)-(\d+)-[A-Za-z0-9_-]+\.tmp")
_RECOVERY_LOCK = threading.Lock()
_RECOVERED_JOURNAL_DIRS: set[str] = set()

ERROR_ALREADY_EXISTS = 183
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION = 15
JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x00000001
JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x00000004
COMMAND_MEMORY_LIMIT_BYTES = 128 * 1024 * 1024
COMMAND_ACTIVE_PROCESS_LIMIT = 32
COMMAND_CPU_RATE = 5000


class MCPAppContainerError(RuntimeError):
    """Windows could not create an isolated MCP child process."""


def startup_os_error_code(error: BaseException) -> int | None:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "winerror", None)
        if not isinstance(code, int):
            code = getattr(current, "errno", None)
        if isinstance(code, int) and not isinstance(code, bool) and 0 <= code <= 0xFFFFFFFF:
            return code
        current = current.__cause__ or current.__context__
    return None


def startup_error_frame(error: BaseException) -> bytes:
    """Return a bounded internal startup frame with only a numeric OS error."""
    code = startup_os_error_code(error)
    value = 0 if code is None else code
    payload = value.to_bytes(4, "big", signed=False)
    return ERROR_FRAME_PREFIX + len(payload).to_bytes(2, "big") + payload


def startup_error_message(frame: bytes) -> str:
    """Decode the internal frame into a path-free, user-safe diagnostic."""
    if not frame.startswith(ERROR_FRAME_PREFIX) or len(frame) < len(ERROR_FRAME_PREFIX) + 2:
        return "Windows could not start the network-isolated MCP server."
    header_end = len(ERROR_FRAME_PREFIX) + 2
    size = int.from_bytes(frame[len(ERROR_FRAME_PREFIX) : header_end], "big")
    if size != 4 or size > MAX_STARTUP_ERROR_FRAME_BYTES or len(frame) != header_end + size:
        return "Windows could not start the network-isolated MCP server."
    code = int.from_bytes(frame[header_end:], "big")
    if code == 0:
        return "Windows could not start the network-isolated MCP server."
    return f"Windows could not start the network-isolated MCP server (Windows error {code})."


def _replace_journal_file(source: str, destination: Path) -> None:
    """Atomically replace a same-directory journal and flush the rename."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move_file = kernel32.MoveFileExW
    move_file.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move_file.restype = wintypes.BOOL
    flags = MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
    if not move_file(source, str(destination), flags):
        raise ctypes.WinError(ctypes.get_last_error())


class SECURITY_CAPABILITIES(ctypes.Structure):
    _fields_ = [
        ("AppContainerSid", ctypes.c_void_p),
        ("Capabilities", ctypes.c_void_p),
        ("CapabilityCount", wintypes.DWORD),
        ("Reserved", wintypes.DWORD),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
    _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]


def _win32_api() -> tuple[Any, Any]:
    if sys.platform != "win32":
        raise MCPAppContainerError("MCP network isolation is currently supported on Windows only.")
    return (
        ctypes.WinDLL("kernel32", use_last_error=True),
        ctypes.WinDLL("userenv", use_last_error=True),
    )


def _appcontainer_sid(profile_name: str) -> tuple[Any, bytes, Any]:
    kernel32, userenv = _win32_api()
    sid = ctypes.c_void_p()
    create_profile = userenv.CreateAppContainerProfile
    create_profile.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    create_profile.restype = ctypes.c_long
    result = create_profile(
        profile_name, profile_name, "Xyst MCP server", None, 0, ctypes.byref(sid)
    )
    if (result & 0xFFFFFFFF) == (0x80070000 | ERROR_ALREADY_EXISTS):
        derive_sid = userenv.DeriveAppContainerSidFromAppContainerName
        derive_sid.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
        derive_sid.restype = ctypes.c_long
        result = derive_sid(profile_name, ctypes.byref(sid))
    if result < 0 or not sid.value:
        raise MCPAppContainerError("Windows could not create an MCP network isolation profile.")

    sid_text = ctypes.c_wchar_p()
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    convert_sid = advapi32.ConvertSidToStringSidW
    convert_sid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
    convert_sid.restype = wintypes.BOOL
    if not convert_sid(sid, ctypes.byref(sid_text)):
        free_sid = advapi32.FreeSid
        free_sid.argtypes = [ctypes.c_void_p]
        free_sid.restype = ctypes.c_void_p
        free_sid(sid)
        raise MCPAppContainerError("Windows could not read the MCP isolation identity.")
    try:
        # Convert back to bytes so the source SID can be freed before CreateProcess.
        import win32security

        sid_string = sid_text.value
        if not sid_string:
            raise MCPAppContainerError("Windows could not read the MCP isolation identity.")
        sid_object = win32security.ConvertStringSidToSid(sid_string)
        sid_bytes = bytes(cast(Any, win32security.GetBinarySid(sid_string)))
    finally:
        local_free = kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(sid_text, ctypes.c_void_p))
        free_sid = advapi32.FreeSid
        free_sid.argtypes = [ctypes.c_void_p]
        free_sid.restype = ctypes.c_void_p
        free_sid(sid)
    return kernel32, sid_bytes, sid_object


def _appcontainer_folder_path(sid: Any) -> Path:
    """Return the profile-local data directory used for AppContainer temp files."""
    import win32security

    sid_text = win32security.ConvertSidToStringSid(sid)
    folder = ctypes.c_wchar_p()
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    get_folder = userenv.GetAppContainerFolderPath
    get_folder.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_wchar_p)]
    get_folder.restype = ctypes.c_long
    result = get_folder(sid_text, ctypes.byref(folder))
    if result != 0 or not folder.value:
        raise MCPAppContainerError("Windows could not locate the AppContainer local data folder.")
    try:
        return Path(folder.value)
    finally:
        free = ctypes.WinDLL("ole32").CoTaskMemFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = None
        free(ctypes.cast(folder, ctypes.c_void_p))


class _FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]


def _process_identity(process_id: int) -> tuple[bool | None, int | None]:
    """Return whether a PID is live and, when available, its creation FILETIME."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    process = open_process(0x1000 | 0x00100000, False, process_id)  # Query + SYNCHRONIZE
    if not process:
        error = ctypes.get_last_error()
        # Access denied and other inspection failures are treated as live so
        # another running engine's grants are never revoked.
        return (False, None) if error == 87 else (None, None)
    try:
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        wait_for_single_object.restype = wintypes.DWORD
        wait_result = wait_for_single_object(process, 0)
        if wait_result == 0:  # WAIT_OBJECT_0
            return False, None
        if wait_result != 258:  # WAIT_TIMEOUT; any other value is an inspection failure.
            return None, None

        creation = _FILETIME()
        exited = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        get_times = kernel32.GetProcessTimes
        get_times.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        get_times.restype = wintypes.BOOL
        if not get_times(
            process,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None, None
        creation_time = (creation.high << 32) | creation.low
        return True, creation_time
    finally:
        kernel32.CloseHandle(process)


def process_identity(process_id: int) -> tuple[bool | None, int | None]:
    """Read a Windows PID's live state and creation time for owned-process fencing."""
    return _process_identity(process_id)


class AppContainerAccess:
    """Temporary AppContainer profile and narrowly granted filesystem paths."""

    def __init__(
        self,
        command: str,
        args: list[str],
        cwd: str | None,
        read_roots: list[str],
        write_roots: list[str],
        journal_directory: str | Path,
        *,
        grant_argument_paths: bool = True,
        grant_executable_directory: bool = True,
    ) -> None:
        if sys.platform != "win32":
            raise MCPAppContainerError(
                "MCP network isolation is currently supported on Windows only."
            )
        import win32security

        self._security = win32security
        self._journal_directory = Path(journal_directory)
        self._journal_directory.mkdir(parents=True, exist_ok=True)
        self._recover_stale_grants(self._journal_directory)
        owner_running, owner_creation_time = _process_identity(os.getpid())
        if not owner_running or owner_creation_time is None:
            raise MCPAppContainerError(
                "Windows could not identify the MCP isolation owner process."
            )
        self._owner_creation_time = owner_creation_time
        self.profile_name = f"XAA-MCP-{uuid4().hex}"
        self._grants: list[tuple[str, int, int]] = []
        self._journal_path = self._journal_directory / f"{self.profile_name}.json"
        # Persist the profile intent before Windows creates the persistent profile.
        # An empty grants list makes recovery safe if the engine dies before the
        # first ACL mutation is journaled.
        self._write_journal()
        _kernel32, _sid_bytes, self._sid = _appcontainer_sid(self.profile_name)
        try:
            self.private_temp_path = _appcontainer_folder_path(self._sid) / "Temp"
            self.private_temp_path.mkdir(parents=True, exist_ok=True)
            write_roots.append(str(self.private_temp_path))
            executable = Path(command).resolve(strict=True)
            if not executable.is_file() or executable.suffix.lower() in {".cmd", ".bat"}:
                raise MCPAppContainerError(
                    "Network-isolated MCP servers must use a directly executable Windows program."
                )
            if grant_executable_directory:
                read_roots.append(str(executable.parent))
                if (
                    executable.name.lower() in {"python.exe", "pythonw.exe"}
                    and executable.parent.name.lower() == "scripts"
                ):
                    read_roots.append(str(executable.parent.parent))
                    venv_configuration = executable.parent.parent / "pyvenv.cfg"
                    if venv_configuration.is_file():
                        for line in venv_configuration.read_text(encoding="utf-8").splitlines():
                            key, separator, value = line.partition("=")
                            if separator and key.strip().lower() == "home":
                                base_runtime = Path(value.strip()).resolve(strict=True)
                                if (
                                    not base_runtime.is_dir()
                                    or not (base_runtime / "python.exe").is_file()
                                    or not any(base_runtime.glob("python*.dll"))
                                ):
                                    raise MCPAppContainerError(
                                        "Windows could not identify the base runtime for this MCP Python environment."
                                    )
                                read_roots.append(str(base_runtime))
                                break
            else:
                self._grant_file(str(executable), FILE_GENERIC_READ | FILE_GENERIC_EXECUTE)
            if grant_argument_paths:
                for argument in args:
                    candidate = Path(argument)
                    if candidate.is_absolute() and candidate.exists():
                        read_roots.append(
                            str(candidate if candidate.is_dir() else candidate.parent)
                        )
            read_only_paths = [Path(path).resolve(strict=True) for path in read_roots]
            if cwd and not any(
                Path(cwd).resolve(strict=True).is_relative_to(path) for path in read_only_paths
            ):
                write_roots.append(cwd)

            for path in self._collapse_roots(read_roots):
                self._grant_tree(path, FILE_GENERIC_READ | FILE_GENERIC_EXECUTE)
            for path in self._collapse_roots(write_roots):
                self._grant_tree(
                    path, FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE
                )
        except Exception:
            self.close()
            raise

    @staticmethod
    def _collapse_roots(paths: list[str]) -> list[str]:
        resolved = sorted(
            {Path(path).resolve(strict=True) for path in paths}, key=lambda path: len(path.parts)
        )
        roots: list[Path] = []
        for path in resolved:
            if not any(path == root or path.is_relative_to(root) for root in roots):
                roots.append(path)
        return [str(path) for path in roots]

    def _grant_tree(self, raw_path: str, mask: int) -> None:
        import win32file

        requested = Path(os.path.abspath(raw_path))
        for component in (requested, *requested.parents):
            try:
                attributes = cast(int, win32file.GetFileAttributes(str(component)))
                if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    raise MCPAppContainerError(
                        "MCP isolation does not follow reparse-point access paths."
                    )
            except OSError as error:
                raise MCPAppContainerError(
                    "Windows could not inspect an MCP isolation path."
                ) from error

        root = requested.resolve(strict=True)
        if not root.is_dir():
            raise MCPAppContainerError("A selected MCP access path is not a directory.")
        pending = [root]
        object_count = 0
        while pending:
            directory = pending.pop()
            try:
                with os.scandir(directory) as children:
                    for child in children:
                        object_count += 1
                        if object_count > MAX_ACL_OBJECTS:
                            raise MCPAppContainerError(
                                f"An MCP isolation access path exceeds the {MAX_ACL_OBJECTS}-entry Windows limit."
                            )
                        attributes = child.stat(follow_symlinks=False).st_file_attributes
                        if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                            # Do not traverse reparse targets while bounding the
                            # ACL walk. The access grant remains on the selected
                            # root; the target keeps its own security descriptor.
                            continue
                        if child.is_dir(follow_symlinks=False):
                            pending.append(Path(child.path))
            except OSError as error:
                raise MCPAppContainerError(
                    "Windows could not inspect the complete MCP isolation access path."
                ) from error

        # Windows grants traverse privilege to app processes. Avoid editing
        # protected ancestors such as C:\Users; access is granted only at the
        # explicitly selected subtree below.
        self._add_ace(
            str(root),
            mask,
            self._security.CONTAINER_INHERIT_ACE | self._security.OBJECT_INHERIT_ACE,
        )

    def _grant_file(self, raw_path: str, mask: int) -> None:
        import win32file

        requested = Path(os.path.abspath(raw_path))
        for component in (requested, *requested.parents):
            try:
                attributes = cast(int, win32file.GetFileAttributes(str(component)))
                if attributes & FILE_ATTRIBUTE_REPARSE_POINT:
                    raise MCPAppContainerError(
                        "MCP isolation does not follow reparse-point access paths."
                    )
            except OSError as error:
                raise MCPAppContainerError(
                    "Windows could not inspect an MCP isolation program path."
                ) from error
        target = requested.resolve(strict=True)
        if not target.is_file():
            raise MCPAppContainerError("A selected MCP program is not a regular file.")
        self._add_ace(str(target), mask, 0)

    def _add_ace(self, path: str, mask: int, flags: int) -> None:
        grant = (path, mask, flags)
        if grant not in self._grants:
            self._grants.append(grant)
            self._write_journal()
        security = self._security
        descriptor = security.GetNamedSecurityInfo(
            path, security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
        )
        dacl = cast(Any, descriptor.GetSecurityDescriptorDacl())
        if dacl is None:
            return
        sid_string = security.ConvertSidToStringSid(self._sid)
        for index in range(dacl.GetAceCount()):
            ace_header, ace_mask, ace_sid = dacl.GetAce(index)
            ace_type, ace_flags = ace_header
            if (
                ace_type == security.ACCESS_ALLOWED_ACE_TYPE
                and ace_flags == flags
                and ace_mask == mask
                and security.ConvertSidToStringSid(ace_sid) == sid_string
            ):
                return
        dacl.AddAccessAllowedAceEx(security.ACL_REVISION_DS, flags, mask, self._sid)
        security.SetNamedSecurityInfo(
            path,
            security.SE_FILE_OBJECT,
            security.DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )

    def _write_journal(self) -> None:
        payload = {
            "version": 2,
            "profile_name": self.profile_name,
            "owner_pid": os.getpid(),
            "owner_creation_time": self._owner_creation_time,
            "grants": [
                {"path": path, "mask": mask, "flags": flags} for path, mask, flags in self._grants
            ],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=(f".{self.profile_name}-{os.getpid()}-{self._owner_creation_time}-"),
            suffix=".tmp",
            dir=self._journal_directory,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
                json.dump(payload, destination, separators=(",", ":"))
                destination.flush()
                os.fsync(destination.fileno())
            _replace_journal_file(temporary_name, self._journal_path)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    @classmethod
    def _recover_stale_grants(cls, journal_directory: Path) -> None:
        journal_root = journal_directory.resolve(strict=True)
        key = os.path.normcase(str(journal_root))
        with _RECOVERY_LOCK:
            if key in _RECOVERED_JOURNAL_DIRS:
                return
            import win32security

            for journal_path in journal_root.glob("XAA-MCP-*.json"):
                if journal_path.is_symlink() or journal_path.stat().st_size > 1024 * 1024:
                    raise MCPAppContainerError("An MCP isolation recovery record is unsafe.")
                try:
                    record = json.loads(journal_path.read_text(encoding="utf-8"))
                    profile_name = record["profile_name"]
                    owner_pid = record["owner_pid"]
                    version = record.get("version")
                    owner_creation_time = record.get("owner_creation_time")
                    raw_grants = record["grants"]
                    if (
                        version not in {1, 2}
                        or not isinstance(profile_name, str)
                        or re.fullmatch(r"XAA-MCP-[0-9a-f]{32}", profile_name) is None
                        or not isinstance(owner_pid, int)
                        or owner_pid <= 0
                        or not isinstance(raw_grants, list)
                        or len(raw_grants) > 16
                        or (
                            version == 2
                            and (
                                not isinstance(owner_creation_time, int)
                                or isinstance(owner_creation_time, bool)
                                or owner_creation_time <= 0
                            )
                        )
                    ):
                        raise ValueError("invalid journal shape")
                    grants: list[tuple[str, int, int]] = []
                    for grant in raw_grants:
                        path = grant["path"]
                        mask = grant["mask"]
                        flags = grant["flags"]
                        if (
                            not isinstance(path, str)
                            or not Path(path).is_absolute()
                            or not isinstance(mask, int)
                            or mask
                            not in {
                                FILE_GENERIC_READ | FILE_GENERIC_EXECUTE,
                                FILE_GENERIC_READ | FILE_GENERIC_WRITE | FILE_GENERIC_EXECUTE,
                            }
                            or not isinstance(flags, int)
                            or flags
                            not in {
                                0,
                                win32security.CONTAINER_INHERIT_ACE
                                | win32security.OBJECT_INHERIT_ACE,
                            }
                        ):
                            raise ValueError("invalid journal grant")
                        grants.append((path, mask, flags))
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
                    raise MCPAppContainerError(
                        "An MCP isolation recovery record could not be validated."
                    ) from error
                owner_running, current_creation_time = _process_identity(owner_pid)
                if owner_running is None or (
                    owner_running
                    and (
                        owner_creation_time is None
                        or current_creation_time is None
                        or owner_creation_time == current_creation_time
                    )
                ):
                    continue
                _kernel32, _sid_bytes, sid = _appcontainer_sid(profile_name)
                sid_string = win32security.ConvertSidToStringSid(sid)
                failures: list[Exception] = []
                for path, mask, flags in reversed(grants):
                    if not Path(path).exists():
                        continue
                    try:
                        descriptor = win32security.GetNamedSecurityInfo(
                            path,
                            win32security.SE_FILE_OBJECT,
                            win32security.DACL_SECURITY_INFORMATION,
                        )
                        dacl = cast(Any, descriptor.GetSecurityDescriptorDacl())
                        if dacl is None:
                            continue
                        for index in reversed(range(dacl.GetAceCount())):
                            ace_header, ace_mask, ace_sid = dacl.GetAce(index)
                            ace_type, ace_flags = ace_header
                            if (
                                ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE
                                and ace_flags == flags
                                and ace_mask == mask
                                and win32security.ConvertSidToStringSid(ace_sid) == sid_string
                            ):
                                dacl.DeleteAce(index)
                                break
                        win32security.SetNamedSecurityInfo(
                            path,
                            win32security.SE_FILE_OBJECT,
                            win32security.DACL_SECURITY_INFORMATION,
                            None,
                            None,
                            dacl,
                            None,
                        )
                    except OSError as error:
                        failures.append(error)
                if not failures:
                    delete_profile = ctypes.WinDLL(
                        "userenv", use_last_error=True
                    ).DeleteAppContainerProfile
                    delete_profile.argtypes = [wintypes.LPCWSTR]
                    delete_profile.restype = ctypes.c_long
                    result = delete_profile(profile_name)
                    if result != 0:
                        failures.append(
                            MCPAppContainerError("Could not remove a stale isolation profile.")
                        )
                if failures:
                    raise MCPAppContainerError(
                        "Windows could not recover temporary MCP isolation permissions."
                    ) from failures[0]
                journal_path.unlink()
            for temporary_path in journal_root.glob(".XAA-MCP-*.tmp"):
                match = _TEMP_JOURNAL_NAME.fullmatch(temporary_path.name)
                if match is None:
                    continue
                owner_pid = int(match.group(2))
                owner_creation_time = int(match.group(3))
                if (
                    owner_pid <= 0
                    or owner_pid > 0xFFFFFFFF
                    or owner_creation_time <= 0
                    or owner_creation_time > 0xFFFFFFFFFFFFFFFF
                ):
                    continue
                owner_running, current_creation_time = _process_identity(owner_pid)
                if owner_running is False or (
                    owner_running is True
                    and current_creation_time is not None
                    and current_creation_time != owner_creation_time
                ):
                    try:
                        temporary_path.unlink(missing_ok=True)
                    except OSError as error:
                        raise MCPAppContainerError(
                            "Windows could not remove an abandoned MCP isolation journal file."
                        ) from error
            _RECOVERED_JOURNAL_DIRS.add(key)

    def close(self) -> None:
        security = getattr(self, "_security", None)
        failures: list[Exception] = []
        if security is not None:
            sid_string = security.ConvertSidToStringSid(self._sid)
            for path, mask, flags in reversed(self._grants):
                try:
                    descriptor = security.GetNamedSecurityInfo(
                        path, security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
                    )
                    dacl = descriptor.GetSecurityDescriptorDacl()
                    if dacl is None:
                        continue
                    for index in reversed(range(dacl.GetAceCount())):
                        ace_header, ace_mask, ace_sid = dacl.GetAce(index)
                        ace_type, ace_flags = ace_header
                        if (
                            ace_type == security.ACCESS_ALLOWED_ACE_TYPE
                            and ace_flags == flags
                            and ace_mask == mask
                            and security.ConvertSidToStringSid(ace_sid) == sid_string
                        ):
                            dacl.DeleteAce(index)
                            break
                    security.SetNamedSecurityInfo(
                        path,
                        security.SE_FILE_OBJECT,
                        security.DACL_SECURITY_INFORMATION,
                        None,
                        None,
                        dacl,
                        None,
                    )
                except OSError as error:
                    # Keep profile identity until ACL revocation has been attempted;
                    # a stale ACE for a deleted profile does not grant another SID access.
                    failures.append(error)
        if not failures:
            try:
                if hasattr(self, "profile_name"):
                    delete_profile = ctypes.WinDLL(
                        "userenv", use_last_error=True
                    ).DeleteAppContainerProfile
                    delete_profile.argtypes = [wintypes.LPCWSTR]
                    delete_profile.restype = ctypes.c_long
                    result = delete_profile(self.profile_name)
                    if result != 0:
                        failures.append(
                            MCPAppContainerError(
                                "Windows could not remove the temporary MCP isolation profile."
                            )
                        )
            except OSError as error:
                failures.append(error)
        if not failures and hasattr(self, "_journal_path"):
            self._journal_path.unlink(missing_ok=True)
        self._grants = []
        if failures:
            raise MCPAppContainerError(
                "Windows could not fully remove temporary MCP isolation permissions."
            ) from failures[0]

    @classmethod
    def fingerprint_code(cls) -> tuple[Any, ...]:
        """Expose implementation code objects for frozen profile binding."""
        return (
            cls.__init__.__code__,
            cls._grant_tree.__code__,
            cls._add_ace.__code__,
            cls._recover_stale_grants.__func__.__code__,
            cls.close.__code__,
            _appcontainer_folder_path.__code__,
        )


def _create_no_network_child(
    command: str,
    args: list[str],
    cwd: str | None,
    profile_name: str,
    *,
    owner_pid: int | None = None,
    owner_creation_time: int | None = None,
    max_seconds: int | None = None,
    command_limits: bool = False,
    child_environment: dict[str, str] | None = None,
) -> int:
    stage = "prepare AppContainer identity"
    kernel32, sid_bytes, _sid = _appcontainer_sid(profile_name)
    sid_buffer = ctypes.create_string_buffer(sid_bytes)
    capabilities = SECURITY_CAPABILITIES(ctypes.cast(sid_buffer, ctypes.c_void_p), None, 0, 0)
    attribute_bytes = ctypes.c_size_t()
    initialize_attributes = kernel32.InitializeProcThreadAttributeList
    initialize_attributes.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    initialize_attributes.restype = wintypes.BOOL
    if initialize_attributes(None, 2, 0, ctypes.byref(attribute_bytes)):
        raise MCPAppContainerError("Windows returned an unexpected AppContainer attribute size.")
    attributes = ctypes.create_string_buffer(attribute_bytes.value)
    attribute_list = ctypes.cast(attributes, ctypes.c_void_p)
    if not initialize_attributes(attribute_list, 2, 0, ctypes.byref(attribute_bytes)):
        raise ctypes.WinError(ctypes.get_last_error())

    process_info = PROCESS_INFORMATION()
    job_handle: Any | None = None
    job_assigned = False
    startup = STARTUPINFOEXW()
    startup.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES
    handle_flags: list[tuple[Any, int]] = []
    get_std_handle = kernel32.GetStdHandle
    get_std_handle.argtypes = [wintypes.DWORD]
    get_std_handle.restype = wintypes.HANDLE
    get_handle_information = kernel32.GetHandleInformation
    get_handle_information.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    get_handle_information.restype = wintypes.BOOL
    set_handle_information = kernel32.SetHandleInformation
    set_handle_information.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
    set_handle_information.restype = wintypes.BOOL
    try:
        stage = "inspect inherited process handles"
        stdio_handles: list[Any] = []
        for field, handle_id in (("hStdInput", -10), ("hStdOutput", -11), ("hStdError", -12)):
            handle = get_std_handle(ctypes.c_uint32(handle_id).value)
            if not handle or handle == wintypes.HANDLE(-1).value:
                raise MCPAppContainerError(
                    "MCP stdio handles are unavailable for network isolation."
                )
            old_flags = wintypes.DWORD()
            if not get_handle_information(handle, ctypes.byref(old_flags)):
                raise ctypes.WinError(ctypes.get_last_error())
            if not set_handle_information(handle, 1, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            handle_flags.append((handle, old_flags.value))
            stdio_handles.append(handle)
            setattr(startup.StartupInfo, field, handle)

        startup.lpAttributeList = attribute_list
        stage = "apply AppContainer security capabilities"
        if not kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
            ctypes.byref(capabilities),
            ctypes.sizeof(capabilities),
            None,
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        stage = "restrict inherited process handles"
        unique_handles = list(dict.fromkeys(stdio_handles))
        handle_list = (wintypes.HANDLE * len(unique_handles))(*unique_handles)
        if not kernel32.UpdateProcThreadAttribute(
            attribute_list,
            0,
            PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
            ctypes.cast(handle_list, ctypes.c_void_p),
            ctypes.sizeof(handle_list),
            None,
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())

        stage = "create suspended AppContainer process"
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline([command, *args]))
        environment_buffer: Any | None = None
        environment_pointer: ctypes.c_void_p | None = None
        if child_environment is not None:
            environment_text = (
                "\0".join(
                    f"{key}={value}"
                    for key, value in sorted(
                        child_environment.items(), key=lambda item: item[0].casefold()
                    )
                )
                + "\0\0"
            )
            environment_buffer = ctypes.create_unicode_buffer(environment_text)
            environment_pointer = ctypes.cast(environment_buffer, ctypes.c_void_p)
        create_process = kernel32.CreateProcessW
        create_process.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.POINTER(STARTUPINFOEXW),
            ctypes.POINTER(PROCESS_INFORMATION),
        ]
        create_process.restype = wintypes.BOOL
        flags = (
            EXTENDED_STARTUPINFO_PRESENT
            | CREATE_UNICODE_ENVIRONMENT
            | CREATE_SUSPENDED
            | CREATE_NO_WINDOW
        )
        if not create_process(
            command,
            command_line,
            None,
            None,
            True,
            flags,
            environment_pointer,
            cwd,
            ctypes.byref(startup),
            ctypes.byref(process_info),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        stage = "create process-tree job"
        create_job = kernel32.CreateJobObjectW
        create_job.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        create_job.restype = wintypes.HANDLE
        job_handle = create_job(None, None)
        if not job_handle:
            raise ctypes.WinError(ctypes.get_last_error())
        job_limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        job_limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if command_limits:
            job_limits.BasicLimitInformation.LimitFlags |= (
                JOB_OBJECT_LIMIT_ACTIVE_PROCESS | JOB_OBJECT_LIMIT_JOB_MEMORY
            )
            job_limits.BasicLimitInformation.ActiveProcessLimit = COMMAND_ACTIVE_PROCESS_LIMIT
            job_limits.JobMemoryLimit = COMMAND_MEMORY_LIMIT_BYTES
        set_job_info = kernel32.SetInformationJobObject
        set_job_info.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        set_job_info.restype = wintypes.BOOL
        stage = "apply process-tree memory and task limits"
        if not set_job_info(
            job_handle,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(job_limits),
            ctypes.sizeof(job_limits),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if command_limits:
            cpu_limits = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(
                JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
                COMMAND_CPU_RATE,
            )
            stage = "apply process-tree CPU limit"
            if not set_job_info(
                job_handle,
                JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION,
                ctypes.byref(cpu_limits),
                ctypes.sizeof(cpu_limits),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        stage = "contain process tree"
        assign_job = kernel32.AssignProcessToJobObject
        assign_job.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        assign_job.restype = wintypes.BOOL
        if not assign_job(job_handle, process_info.hProcess):
            raise ctypes.WinError(ctypes.get_last_error())
        job_assigned = True
        stage = "resume isolated child"
        # Announce readiness before the child can write any MCP bytes. The parent
        # consumes this private frame before handing the transport to the SDK.
        sys.stdout.buffer.write(READY_FRAME)
        sys.stdout.buffer.flush()
        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            terminate_job = kernel32.TerminateJobObject
            terminate_job.argtypes = [wintypes.HANDLE, wintypes.UINT]
            terminate_job.restype = wintypes.BOOL
            terminate_job(job_handle, 1)
            raise ctypes.WinError(ctypes.get_last_error())
        if owner_pid is None and owner_creation_time is None:
            wait_result = kernel32.WaitForSingleObject(process_info.hProcess, 0xFFFFFFFF)
        else:
            if (
                not isinstance(owner_pid, int)
                or isinstance(owner_pid, bool)
                or owner_pid <= 0
                or not isinstance(owner_creation_time, int)
                or isinstance(owner_creation_time, bool)
                or owner_creation_time <= 0
                or not isinstance(max_seconds, int)
                or isinstance(max_seconds, bool)
                or not 1 <= max_seconds <= 30
            ):
                raise MCPAppContainerError("The MCP process lifetime policy was invalid.")
            open_owner = kernel32.OpenProcess
            open_owner.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            open_owner.restype = wintypes.HANDLE
            owner = open_owner(0x00100000 | 0x1000, False, owner_pid)
            if not owner:
                raise MCPAppContainerError("The MCP isolation owner is no longer running.")
            try:
                owner_running, observed_creation_time = _process_identity(owner_pid)
                if owner_running is not True or observed_creation_time != owner_creation_time:
                    raise MCPAppContainerError("The MCP isolation owner identity changed.")
                handles = (wintypes.HANDLE * 2)(process_info.hProcess, owner)
                wait_many = kernel32.WaitForMultipleObjects
                wait_many.argtypes = [
                    wintypes.DWORD,
                    ctypes.POINTER(wintypes.HANDLE),
                    wintypes.BOOL,
                    wintypes.DWORD,
                ]
                wait_many.restype = wintypes.DWORD
                wait_result = wait_many(2, handles, False, max_seconds * 1000)
            finally:
                kernel32.CloseHandle(owner)
            if wait_result == 0x00000102:
                terminate_job = kernel32.TerminateJobObject
                terminate_job.argtypes = [wintypes.HANDLE, wintypes.UINT]
                terminate_job.restype = wintypes.BOOL
                if not terminate_job(job_handle, 124):
                    raise ctypes.WinError(ctypes.get_last_error())
                if kernel32.WaitForSingleObject(process_info.hProcess, 5000) != 0:
                    raise MCPAppContainerError(
                        "Windows could not stop the isolated MCP process tree."
                    )
                return 124
            if wait_result == 1:
                terminate_job = kernel32.TerminateJobObject
                terminate_job.argtypes = [wintypes.HANDLE, wintypes.UINT]
                terminate_job.restype = wintypes.BOOL
                if not terminate_job(job_handle, 125):
                    raise ctypes.WinError(ctypes.get_last_error())
                if kernel32.WaitForSingleObject(process_info.hProcess, 5000) != 0:
                    raise MCPAppContainerError(
                        "Windows could not stop the isolated MCP process tree."
                    )
                return 125
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(exit_code.value)
    except BaseException as error:
        if isinstance(error, OSError):
            error.add_note(f"AppContainer launch stage: {stage}")
        raise
    finally:
        for handle, original_flags in handle_flags:
            set_handle_information(handle, 1, original_flags & 1)
        kernel32.DeleteProcThreadAttributeList(attribute_list)
        if process_info.hThread:
            kernel32.CloseHandle(process_info.hThread)
        if process_info.hProcess:
            wait_result = kernel32.WaitForSingleObject(process_info.hProcess, 0)
            if wait_result == 0x00000102:
                if job_handle and job_assigned:
                    stop_process = kernel32.TerminateJobObject
                    stop_process.argtypes = [wintypes.HANDLE, wintypes.UINT]
                    stop_process.restype = wintypes.BOOL
                    stopped = stop_process(job_handle, 1)
                else:
                    stop_process = kernel32.TerminateProcess
                    stop_process.argtypes = [wintypes.HANDLE, wintypes.UINT]
                    stop_process.restype = wintypes.BOOL
                    stopped = stop_process(process_info.hProcess, 1)
                if not stopped:
                    raise ctypes.WinError(ctypes.get_last_error())
                if kernel32.WaitForSingleObject(process_info.hProcess, 5000) != 0:
                    raise MCPAppContainerError(
                        "Windows could not stop the isolated MCP process tree."
                    )
            kernel32.CloseHandle(process_info.hProcess)
        if job_handle:
            kernel32.CloseHandle(job_handle)


def run_stdio_appcontainer_shim() -> int:
    """Read a bounded bootstrap record and replace this process with an isolated child."""
    try:
        first_line = sys.stdin.buffer.readline(MAX_BOOTSTRAP_BYTES + 1)
        if len(first_line) > MAX_BOOTSTRAP_BYTES or not first_line.startswith(BOOTSTRAP_PREFIX):
            raise MCPAppContainerError("The MCP network isolation bootstrap was invalid.")
        payload_raw = json.loads(first_line[len(BOOTSTRAP_PREFIX) :])
        if not isinstance(payload_raw, dict):
            raise MCPAppContainerError("The MCP network isolation bootstrap was invalid.")
        payload: dict[str, object] = payload_raw
        command = payload.get("command")
        args = payload.get("args")
        cwd = payload.get("cwd")
        profile_name = payload.get("profile_name")
        owner_pid = payload.get("owner_pid")
        owner_creation_time = payload.get("owner_creation_time")
        max_seconds = payload.get("max_seconds")
        command_limits = payload.get("command_limits", False)
        child_environment = payload.get("child_environment")
        if (
            not isinstance(command, str)
            or not isinstance(args, list)
            or any(not isinstance(item, str) for item in args)
            or (cwd is not None and not isinstance(cwd, str))
            or not isinstance(profile_name, str)
            or not profile_name.startswith("XAA-MCP-")
            or (
                owner_pid is not None
                and (not isinstance(owner_pid, int) or isinstance(owner_pid, bool))
            )
            or (
                owner_creation_time is not None
                and (
                    not isinstance(owner_creation_time, int)
                    or isinstance(owner_creation_time, bool)
                )
            )
            or (
                max_seconds is not None
                and (not isinstance(max_seconds, int) or isinstance(max_seconds, bool))
            )
            or not isinstance(command_limits, bool)
            or (
                child_environment is not None
                and (
                    not isinstance(child_environment, dict)
                    or len(child_environment) > 64
                    or any(
                        not isinstance(key, str)
                        or not key
                        or "=" in key
                        or "\0" in key
                        or not isinstance(value, str)
                        or "\0" in value
                        for key, value in child_environment.items()
                    )
                    or sum(len(key) + len(value) for key, value in child_environment.items())
                    > 16384
                )
            )
            or (
                command_limits
                and (owner_pid is None or owner_creation_time is None or max_seconds is None)
            )
        ):
            raise MCPAppContainerError("The MCP network isolation bootstrap was invalid.")
        return _create_no_network_child(
            command,
            args,
            cwd,
            profile_name,
            owner_pid=owner_pid,
            owner_creation_time=owner_creation_time,
            max_seconds=max_seconds,
            command_limits=command_limits,
            child_environment=child_environment,
        )
    except Exception as error:  # noqa: BLE001 - fail closed with a path-free startup code
        sys.stdout.buffer.write(startup_error_frame(error))
        sys.stdout.buffer.flush()
        notes = " ".join(getattr(error, "__notes__", []))
        message = (str(error) + " " + notes).replace("\r", " ").replace("\n", " ")[:512]
        sys.stderr.write(f"MCP AppContainer launch failed ({type(error).__name__}): {message}\n")
        sys.stderr.flush()
        return 126


def appcontainer_shim_fingerprint() -> str:
    """Return a stable digest for the sandbox launcher in source and frozen builds."""
    source = Path(__file__)
    if source.is_file():
        return hashlib.sha256(source.read_bytes()).hexdigest()
    code_objects = (
        _process_identity.__code__,
        _create_no_network_child.__code__,
        run_stdio_appcontainer_shim.__code__,
        *AppContainerAccess.fingerprint_code(),
    )
    return hashlib.sha256(b"".join(marshal.dumps(code) for code in code_objects)).hexdigest()


if __name__ == "__main__" and sys.argv[1:] == ["--shim"]:
    raise SystemExit(run_stdio_appcontainer_shim())

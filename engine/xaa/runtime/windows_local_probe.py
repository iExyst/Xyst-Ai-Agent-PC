"""Fixed child program used to verify the Windows AppContainer command path."""

from __future__ import annotations

import ctypes
import json
import os
import pathlib
import socket
import sys

PROBE_CODE = (
    "import ctypes,json,os,pathlib,socket,sys\n"
    "def mark(value): print('appcontainer-probe:'+value,file=sys.stderr,flush=True)\n"
    "mark('python-started')\n"
    "p=ctypes.c_int(0);n=ctypes.c_ulong(0);t=ctypes.c_void_p();k=ctypes.WinDLL('kernel32',use_last_error=True);a=ctypes.WinDLL('advapi32',use_last_error=True);"
    "k.GetCurrentProcess.restype=ctypes.c_void_p;"
    "k.GetTempPathW.argtypes=[ctypes.c_ulong,ctypes.c_wchar_p];k.GetTempPathW.restype=ctypes.c_ulong;"
    "a.OpenProcessToken.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_void_p)];a.OpenProcessToken.restype=ctypes.c_int;"
    "a.GetTokenInformation.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(ctypes.c_ulong)];a.GetTokenInformation.restype=ctypes.c_int;"
    "assert a.OpenProcessToken(k.GetCurrentProcess(),8,ctypes.byref(t));assert a.GetTokenInformation(t,29,ctypes.byref(p),ctypes.sizeof(p),ctypes.byref(n)) and p.value==1;mark('identity-verified')\n"
    "nonce=pathlib.Path('probe.txt').read_text();pathlib.Path('private-write.txt').write_text(nonce);mark('workspace-write-complete')\n"
    "nested=pathlib.Path('tests/nested-fixture.txt').read_text();nested_path=pathlib.Path('tests/private-write.txt');nested_path.write_text(nested);assert nested_path.read_text()==nested;mark('nested-write-complete')\n"
    "assert not any(os.environ.get(n) for n in ('XAA_API_TOKEN','LM_STUDIO_API_TOKEN','LM_API_TOKEN'));mark('credential-check-complete')\n"
    "temp=ctypes.create_unicode_buffer(32768);assert k.GetTempPathW(len(temp),temp)>0;temp_path=pathlib.Path(temp.value)/'probe-temp';temp_path.mkdir();(temp_path/'probe-temp.txt').write_text(nonce);mark('private-temp-complete')\n"
    "try:s=socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=1);s.close();network_denied=False\n"
    "except OSError:network_denied=True\n"
    "mark('network-check-complete')\n"
    "print(json.dumps({'nonce':nonce,'appcontainer':True,'network_denied':network_denied,'private_temp':True,'nested_snapshot_access':True,'checks':True}),flush=True);mark('output-complete')\n"
)


def command_argv(executable: str, port: int, *, frozen: bool) -> list[str]:
    if not 1 <= port <= 65535:
        raise ValueError("Windows AppContainer probe port is invalid.")
    if frozen:
        return [executable, "--windows-appcontainer-probe", str(port)]
    return [executable, "-I", "probe.py", str(port)]


def main(arguments: list[str] | None = None) -> int:
    values = sys.argv[1:] if arguments is None else arguments
    if len(values) != 1:
        raise SystemExit("Windows AppContainer probe expects one loopback port.")
    try:
        port = int(values[0])
    except ValueError as exc:
        raise SystemExit("Windows AppContainer probe port is invalid.") from exc
    if not 1 <= port <= 65535:
        raise SystemExit("Windows AppContainer probe port is invalid.")
    process = ctypes.c_int(0)
    size = ctypes.c_ulong(0)
    token = ctypes.c_void_p()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetTempPathW.argtypes = [ctypes.c_ulong, ctypes.c_wchar_p]
    kernel.GetTempPathW.restype = ctypes.c_ulong
    advapi.OpenProcessToken.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.OpenProcessToken.restype = ctypes.c_int
    advapi.GetTokenInformation.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    advapi.GetTokenInformation.restype = ctypes.c_int
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise OSError("Could not inspect the AppContainer process token.")
    if (
        not advapi.GetTokenInformation(
            token, 29, ctypes.byref(process), ctypes.sizeof(process), ctypes.byref(size)
        )
        or process.value != 1
    ):
        raise PermissionError("The Windows readiness process is not an AppContainer.")
    nonce = pathlib.Path("probe.txt").read_text(encoding="utf-8")
    pathlib.Path("private-write.txt").write_text(nonce, encoding="utf-8")
    nested = pathlib.Path("tests/nested-fixture.txt").read_text(encoding="utf-8")
    nested_path = pathlib.Path("tests/private-write.txt")
    nested_path.write_text(nested, encoding="utf-8")
    if nested_path.read_text(encoding="utf-8") != nested:
        raise OSError("The nested AppContainer snapshot write did not persist.")
    if any(
        os.environ.get(name) for name in ("XAA_API_TOKEN", "LM_STUDIO_API_TOKEN", "LM_API_TOKEN")
    ):
        raise PermissionError("A credential variable entered the AppContainer environment.")
    temporary = ctypes.create_unicode_buffer(32768)
    if not kernel.GetTempPathW(len(temporary), temporary):
        raise OSError("Could not identify the AppContainer temporary directory.")
    temporary_path = pathlib.Path(temporary.value) / "probe-temp"
    temporary_path.mkdir()
    (temporary_path / "probe-temp.txt").write_text(nonce, encoding="utf-8")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            network_denied = False
    except OSError:
        network_denied = True
    print(
        json.dumps(
            {
                "nonce": nonce,
                "appcontainer": True,
                "network_denied": network_denied,
                "private_temp": True,
                "nested_snapshot_access": True,
                "checks": True,
            }
        )
    )
    return 0

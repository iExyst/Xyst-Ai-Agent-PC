"""Actual restricted command/validation acceptance against an existing selected WSL host."""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.runtime.workspace_snapshot import WorkspaceSnapshot, capture_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    args = parser.parse_args()
    if os.name != "nt" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.distribution):
        raise ValueError("Select an existing WSL distribution on Windows.")
    root = Path(".acceptance") / str(uuid4())
    workspace = root / "project"
    workspace.mkdir(parents=True)
    (workspace / "calculation.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (workspace / "test_calculation.py").write_text(
        "import unittest\nfrom calculation import add\n"
        "class Addition(unittest.TestCase):\n    def test_sum(self):\n        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )
    wsl = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/wsl.exe")
    prefix = [wsl, "--distribution", args.distribution, "--exec"]
    modules = [
        Path("engine/xaa/runtime") / filename
        for filename in ("wsl_manager.py", "workspace_snapshot.py", "sandbox_profile.py")
    ]
    paths = [
        subprocess.run(
            [*prefix, "/usr/bin/wslpath", "-a", "-u", str(path.resolve())],
            check=True,
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,
        )
        .stdout.decode("utf-8")
        .strip()
        for path in modules
    ]
    invocation = [*prefix, "/usr/bin/python3", "-I", *paths]
    results: dict[str, Any] = {}

    def request(snapshot: WorkspaceSnapshot, argv: list[str], seconds: int = 10) -> dict[str, Any]:
        return {
            "action": "run",
            "execution_id": uuid4().hex,
            "argv": argv,
            "max_seconds": seconds,
            "snapshot_b64": base64.b64encode(snapshot.to_bytes()).decode("ascii"),
        }

    def execute(label: str, payload: dict[str, Any]) -> dict[str, Any]:
        (root / f"{label}.request.json").write_text(
            json.dumps(
                {"execution_id": payload["execution_id"], "argv": payload["argv"]}, indent=2
            ),
            encoding="utf-8",
        )
        response = subprocess.run(
            invocation,
            input=json.dumps(payload).encode("utf-8"),
            check=False,
            capture_output=True,
            timeout=45,
            creationflags=0x08000000,
        )
        (root / f"{label}.manager-stderr.log").write_bytes(response.stderr)
        if response.returncode != 0:
            raise RuntimeError(f"Manager failed for {label}; inspect {root}")
        result = json.loads(response.stdout)
        assert result["cleanup_confirmed"]
        for stream in ("stdout", "stderr"):
            data = base64.b64decode(result.pop(stream + "_b64"), validate=True)
            (root / f"{label}.{stream}.log").write_bytes(data)
            result[stream + "_bytes"] = len(data)
            result[stream + "_sha256"] = hashlib.sha256(data).hexdigest()
        (root / f"{label}.result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        results[label] = result
        return result

    validation = ["/usr/bin/python3", "-I", "-m", "unittest", "discover", "-s", "/workspace", "-v"]
    failed_snapshot = capture_workspace(workspace)
    failed = execute("defect", request(failed_snapshot, validation))
    assert failed["exit_code"] != 0
    assert b"AssertionError" in (root / "defect.stderr.log").read_bytes()
    (workspace / "calculation.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    snapshot = capture_workspace(workspace)
    passed = execute("fixed", request(snapshot, validation))
    assert passed["exit_code"] == 0
    assert b"OK" in (root / "fixed.stderr.log").read_bytes()
    code = (
        "import ctypes,errno,pathlib,subprocess,sys;"
        "assert sys.argv[1]=='literal ; $(no-host-shell) \\u00e9';"
        "pathlib.Path('calculation.py').write_text('private edit');"
        "assert 'return a + b' in pathlib.Path('/input/calculation.py').read_text();"
        "assert 'Seccomp:\\t2' in pathlib.Path('/proc/self/status').read_text();"
        "assert not pathlib.Path('/mnt/c').exists();"
        "assert not pathlib.Path('/init').exists();"
        "assert subprocess.run(['/usr/bin/unshare','-U','/bin/true']).returncode!=0;"
        "s=ctypes.CDLL('libseccomp.so.2');s.seccomp_syscall_resolve_name.restype=ctypes.c_int;"
        "n=s.seccomp_syscall_resolve_name(b'clone3');libc=ctypes.CDLL(None,use_errno=True);"
        "assert libc.syscall(n,0,0)==-1 and ctypes.get_errno()==errno.ENOSYS;"
        "print('private workspace and syscall filter verified')"
    )
    code += "\nimport socket\nfor family in (socket.AF_INET,socket.AF_UNIX,socket.AF_VSOCK):\n try: socket.socket(family)\n except OSError as e: assert e.errno==errno.EPERM\n else: raise AssertionError('Socket family was permitted')\n"
    private = execute(
        "private",
        request(
            snapshot, ["/usr/bin/python3", "-I", "-c", code, "literal ; $(no-host-shell) \u00e9"]
        ),
    )
    assert private["exit_code"] == 0
    flood = execute(
        "output",
        request(
            snapshot,
            ["/usr/bin/python3", "-I", "-c", "import os\nwhile True: os.write(1,b'x'*16384)"],
        ),
    )
    assert flood["termination_reason"] == "output_limit" and not flood["output_complete"]
    assert flood["exit_code"] is None and not flood["succeeded"]
    assert flood["stdout_bytes"] + flood["stderr_bytes"] == 1048576
    timeout = execute(
        "timeout",
        request(
            snapshot,
            [
                "/usr/bin/python3",
                "-I",
                "-c",
                "import subprocess,time;subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(30)']);time.sleep(30)",
            ],
            1,
        ),
    )
    assert timeout["exit_code"] is None and timeout["termination_reason"] == "timeout"
    assert timeout["elapsed_seconds"] < 7
    exited = execute(
        "exit7", request(snapshot, ["/usr/bin/python3", "-I", "-c", "import sys;sys.exit(7)"])
    )
    assert exited["exit_code"] == 7
    memory = execute(
        "memory",
        request(snapshot, ["/usr/bin/python3", "-I", "-c", "buffer=bytearray(256*1024*1024)"]),
    )
    assert memory["termination_reason"] == "oom-kill" and memory["exit_code"] is None
    disk_code = """
import errno,json
results={}
for directory in ('/tmp','/workspace'):
 written=0
 try:
  with open(directory+'/fill','wb',buffering=0) as f:
   for _ in range(64): written+=f.write(b'x'*1048576)
 except OSError as e:
  assert e.errno==errno.ENOSPC
  assert 16777216 <= written <= 33554432
  results[directory]=written
 else: raise AssertionError('Storage limit not enforced')
print(json.dumps(results))
"""
    disk = execute("storage", request(snapshot, ["/usr/bin/python3", "-I", "-c", disk_code]))
    assert disk["exit_code"] == 0
    process_code = """
import errno,json,subprocess
children=[]
try:
 for _ in range(64): children.append(subprocess.Popen(['/usr/bin/sleep','5']))
except OSError as e:
 assert e.errno==errno.EAGAIN
 print(json.dumps({'children_started':len(children),'process_limit_enforced':True}))
else: raise AssertionError('Process limit not enforced')
finally:
 for child in children: child.kill()
 for child in children: child.wait()
"""
    processes = execute(
        "processes", request(snapshot, ["/usr/bin/python3", "-I", "-c", process_code])
    )
    assert processes["exit_code"] == 0
    cancelled_payload = request(
        snapshot,
        [
            "/usr/bin/python3",
            "-I",
            "-c",
            "import subprocess,time;subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(30)']);time.sleep(30)",
        ],
        30,
    )
    process = subprocess.Popen(
        invocation,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=0x08000000,
    )
    assert process.stdin is not None
    process.stdin.write(json.dumps(cancelled_payload).encode("utf-8"))
    process.stdin.close()
    try:
        deadline = time.monotonic() + 10
        unit = f"xaa-exec-{cancelled_payload['execution_id']}.service"
        while time.monotonic() < deadline:
            observed = subprocess.run(
                [
                    *prefix,
                    "/usr/bin/python3",
                    "-c",
                    "import json,pathlib,subprocess,sys;g=subprocess.run(['/usr/bin/systemctl','show',sys.argv[1],'--property=ControlGroup','--value'],capture_output=True,text=True).stdout.strip();p=pathlib.Path('/sys/fs/cgroup')/g.lstrip('/')/'cgroup.procs';ids=p.read_text().split() if g and p.exists() else [];print(json.dumps([int(i) for i in ids if pathlib.Path('/proc',i,'comm').exists() and pathlib.Path('/proc',i,'comm').read_text().strip()=='python3']))",
                    unit,
                ],
                check=True,
                capture_output=True,
                timeout=3,
                creationflags=0x08000000,
            )
            observed_pids = json.loads(observed.stdout)
            if len(observed_pids) >= 2:
                (root / "cancel.observed-pids.json").write_text(
                    json.dumps(observed_pids), encoding="utf-8"
                )
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Owned cancellation fixture did not become active.")
        cleanup = subprocess.run(
            invocation,
            input=json.dumps(
                {"action": "stop", "execution_id": cancelled_payload["execution_id"]}
            ).encode(),
            check=True,
            capture_output=True,
            timeout=10,
            creationflags=0x08000000,
        )
        assert json.loads(cleanup.stdout)["unit_stopped"]
        process.wait(timeout=10)
        assert process.stdout is not None and process.stderr is not None
        cancelled = json.loads(process.stdout.read())
        (root / "cancel.result.json").write_text(json.dumps(cancelled, indent=2), encoding="utf-8")
        (root / "cancel.manager-stderr.log").write_bytes(process.stderr.read())
        assert (
            process.returncode == 0
            and cancelled["cleanup_confirmed"]
            and cancelled["termination_reason"] == "cancelled"
        )
        results["cancel"] = {
            "execution_id": cancelled_payload["execution_id"],
            "cleanup_confirmed": True,
            "manager_exit_code": process.returncode,
        }
    finally:
        if process.poll() is None:
            subprocess.run(
                invocation,
                input=json.dumps(
                    {"action": "stop", "execution_id": cancelled_payload["execution_id"]}
                ).encode(),
                check=False,
                capture_output=True,
                timeout=10,
                creationflags=0x08000000,
            )
            process.kill()
            process.wait(timeout=5)
    assert capture_workspace(workspace).sha256 == snapshot.sha256
    report = {
        "passed": True,
        "distribution": args.distribution,
        "original_project_unchanged_by_commands": True,
        "fixture_patch_actor": "automated_diagnostic_not_model",
        "source_hashes": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in modules
        },
        "results": results,
        "product_tool_enabled": False,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Restricted WSL executor acceptance passed: {root}")


if __name__ == "__main__":
    main()

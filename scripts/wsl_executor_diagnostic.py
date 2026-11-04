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
    prefix = [wsl, "--distribution", args.distribution, "--user", "root", "--exec"]
    modules = [
        Path("engine/xaa/runtime") / filename
        for filename in (
            "wsl_manager.py",
            "workspace_snapshot.py",
            "sandbox_profile.py",
            "execution_receipts.py",
            "cgroup_gate.py",
        )
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
    invocation = [*prefix, "/usr/bin/python3", "-I", *paths[:4]]
    results: dict[str, Any] = {}
    receipt_code = """
import json,pathlib,runpy,sys,tempfile
from unittest.mock import patch
R=runpy.run_path(sys.argv[1])['ExecutionReceipts']
checks={}
with tempfile.TemporaryDirectory(prefix='xaa-receipt-check-',dir='/tmp') as t:
 root=pathlib.Path(t);registry=R(root/'private')
 with patch.dict(R.locked.__wrapped__.__globals__,{'MAX_IDENTITIES':2}):
  for identity in ('a'*32,'b'*32):
   with registry.locked(identity): registry.marker(identity)
  try:
   with registry.locked('c'*32): raise AssertionError('Quota admitted another identity')
  except RuntimeError as e: assert 'retention is full' in str(e)
  with registry.locked('a'*32): assert registry.status('a'*32)['closed']
 checks['quota_refused_new_identity_existing_control_allowed']=True
 admission=R(root/'admission')
 for identity in ('d'*32,'e'*32):
  with admission.locked(identity): admission.claim(identity,{'fixture':True})
 with admission.locked('f'*32):
  try: admission.claim('f'*32,{'fixture':True})
  except RuntimeError as e: assert 'capacity is full' in str(e)
  else: raise AssertionError('Third admission was permitted')
 assert not admission.path('f'*32,'claim.json').exists()
 with admission.locked('d'*32): admission.release_active('d'*32)
 with admission.locked('f'*32): admission.claim('f'*32,{'fixture':True})
 checks['shared_active_slot_bound_and_release']=True
 slot=admission.path('f'*32,'active');slot.unlink()
 alias=root/'slot-target';alias.write_bytes(b'reserved');slot.symlink_to(alias)
 with admission.locked('1'*32):
  try: admission.claim('1'*32,{'fixture':True})
  except OSError: pass
  else: raise AssertionError('Symlink admission accepted')
 assert alias.read_bytes()==b'reserved'
 slot.unlink();slot.write_bytes(b'corrupt!');slot.chmod(0o600)
 with admission.locked('2'*32):
  try: admission.claim('2'*32,{'fixture':True})
  except PermissionError: pass
  else: raise AssertionError('Corrupt admission accepted')
 checks['admission_symlink_and_corruption_refused']=True
 outside=root/'outside';outside.write_bytes(b'outside-fixture')
 with registry.locked('c'*32):
  registry.path('c'*32,'closed').symlink_to(outside)
  try: registry.marker('c'*32)
  except PermissionError: pass
  else: raise AssertionError('Symlink marker accepted')
 assert outside.read_bytes()==b'outside-fixture'
 checks['marker_symlink_refused']=True
 exposed=root/'exposed';exposed.mkdir(mode=0o755)
 try: R(exposed)
 except PermissionError: pass
 else: raise AssertionError('Non-private receipt root accepted')
 checks['nonprivate_root_refused']=True
 future=root/'future';future.mkdir(mode=0o700);(future/'_schema.json').write_bytes(b'{"format":2}')
 try: R(future)
 except ValueError: pass
 else: raise AssertionError('Unknown receipt schema accepted')
 assert (future/'_schema.json').read_bytes()==b'{"format":2}'
 checks['future_schema_refused_unchanged']=True
print(json.dumps(checks))
"""
    receipt_check = subprocess.run(
        [*prefix, "/usr/bin/python3", "-I", "-c", receipt_code, paths[3]],
        check=True,
        capture_output=True,
        timeout=15,
        creationflags=0x08000000,
    )
    receipt_checks = json.loads(receipt_check.stdout)

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
    literal = "$HOME ${PATH} $LANG ${LANG} $$ %f %n %% \u96ea"
    literal_code = (
        "import sys;d=chr(36);p=chr(37);"
        "expected=d+'HOME '+d+'{PATH} '+d+'LANG '+d+'{LANG} '+d+d+' '+p+'f '+p+'n '+p+p+' '+chr(0x96ea);"
        "assert sys.argv[1]==expected;print(sys.argv[1])"
    )
    literal_result = execute(
        "literal_environment_arguments",
        request(snapshot, ["/usr/bin/python3", "-I", "-c", literal_code, literal]),
    )
    assert literal_result["succeeded"]
    assert (
        root / "literal_environment_arguments.stdout.log"
    ).read_bytes() == literal.encode() + b"\n"
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

    def control(action: str, identity: str) -> dict[str, Any]:
        response = subprocess.run(
            invocation,
            input=json.dumps({"action": action, "execution_id": identity}).encode(),
            check=True,
            capture_output=True,
            timeout=15,
            creationflags=0x08000000,
        )
        return json.loads(response.stdout)

    def refused(label: str, payload: dict[str, Any]) -> None:
        response = subprocess.run(
            invocation,
            input=json.dumps(payload).encode(),
            check=False,
            capture_output=True,
            timeout=15,
            creationflags=0x08000000,
        )
        (root / f"{label}.stderr.log").write_bytes(response.stderr)
        assert response.returncode != 0
        assert (
            b"identity is closed" in response.stderr
            or b"identity was already used" in response.stderr
        )

    once_payload = request(snapshot, ["/usr/bin/python3", "-I", "-c", "print('ONCE')"])
    once = execute("once", once_payload)
    assert once["succeeded"]
    before = control("status", once_payload["execution_id"])
    refused("replay", once_payload)
    assert control("status", once_payload["execution_id"]) == before
    results["replay"] = {"refused": True, "receipt_unchanged": True}
    preclosed = request(snapshot, ["/usr/bin/python3", "-I", "-c", "print('MUST-NOT-RUN')"])
    control("stop", preclosed["execution_id"])
    control("cleanup", preclosed["execution_id"])
    refused("prelaunch-stop", preclosed)
    condition = "/var/lib/xaa-execution-manager/" + preclosed["execution_id"] + ".closed"
    delayed = subprocess.run(
        [
            *prefix,
            "/usr/bin/systemd-run",
            "--unit",
            "xaa-exec-" + preclosed["execution_id"],
            "--wait",
            "--pipe",
            "--collect",
            "--property=ConditionPathExists=!" + condition,
            "/usr/bin/printf",
            "MUST-NOT-RUN",
        ],
        check=True,
        capture_output=True,
        timeout=10,
        creationflags=0x08000000,
    )
    assert delayed.stdout == b""
    results["prelaunch_stop"] = {
        "delayed_launch_refused": True,
        "condition_prevented_command": True,
        "closed": control("status", preclosed["execution_id"])["closed"],
    }
    concurrent = request(
        snapshot,
        ["/usr/bin/python3", "-I", "-c", "import time;time.sleep(0.5);print('ONE-WINNER')"],
    )
    contenders: list[subprocess.Popen[bytes]] = []
    try:
        for _ in range(2):
            child = subprocess.Popen(
                invocation,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=0x08000000,
            )
            assert child.stdin is not None
            child.stdin.write(json.dumps(concurrent).encode())
            child.stdin.close()
            contenders.append(child)
        codes: list[int] = []
        for index, child in enumerate(contenders):
            assert child.stdout is not None and child.stderr is not None
            out = child.stdout.read()
            err = child.stderr.read()
            child.wait(timeout=15)
            (root / f"concurrent-{index}.stdout.log").write_bytes(out)
            (root / f"concurrent-{index}.stderr.log").write_bytes(err)
            assert child.returncode is not None
            codes.append(child.returncode)
            if child.returncode == 0:
                assert json.loads(out)["succeeded"]
            else:
                assert b"identity was already used" in err or b"identity is closed" in err
        assert codes.count(0) == 1
        results["concurrent"] = {"one_winner": True, "return_codes": codes}
    finally:
        for child in contenders:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
        control("cleanup", concurrent["execution_id"])
    crash = request(
        snapshot,
        [
            "/usr/bin/python3",
            "-I",
            "-c",
            "import subprocess,time;subprocess.Popen(['/usr/bin/python3','-c','import time;time.sleep(30)']);time.sleep(30)",
        ],
        30,
    )
    child = subprocess.Popen(
        invocation,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=0x08000000,
    )
    assert child.stdin is not None
    child.stdin.write(json.dumps(crash).encode())
    child.stdin.close()
    try:
        unit = "xaa-exec-" + crash["execution_id"] + ".service"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            state = control("status", crash["execution_id"])
            query = subprocess.run(
                [
                    *prefix,
                    "/usr/bin/python3",
                    "-I",
                    "-c",
                    "import json,pathlib,subprocess,sys;g=subprocess.run(['/usr/bin/systemctl','show',sys.argv[1],'--property=ControlGroup','--value'],capture_output=True,text=True).stdout.strip();p=pathlib.Path('/sys/fs/cgroup')/g.lstrip('/')/'cgroup.procs';ids=p.read_text().split() if g and p.exists() else [];print(json.dumps([int(i) for i in ids if pathlib.Path('/proc',i,'comm').exists() and pathlib.Path('/proc',i,'comm').read_text().strip()=='python3']))",
                    unit,
                ],
                check=True,
                capture_output=True,
                timeout=3,
                creationflags=0x08000000,
            )
            workload = json.loads(query.stdout)
            if state["claim"] is not None and len(workload) >= 2:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Crash fixture workload did not start")
        claim = state["claim"]
        subprocess.run(
            [
                *prefix,
                "/usr/bin/python3",
                "-I",
                "-c",
                "import os,pathlib,signal,sys;pid=int(sys.argv[1]);actual=pathlib.Path('/proc',str(pid),'stat').read_text().rsplit(')',1)[1].split()[19];assert actual==sys.argv[2];assert pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()==sys.argv[3];os.kill(pid,signal.SIGKILL)",
                str(claim["manager_pid"]),
                claim["manager_start"],
                claim["manager_boot_id"],
            ],
            check=True,
            capture_output=True,
            timeout=5,
            creationflags=0x08000000,
        )
        child.wait(timeout=10)
        assert child.returncode != 0
        refused("crash-replay-before-cleanup", crash)
        control("cleanup", crash["execution_id"])
        refused("crash-replay-after-cleanup", crash)
        recovered = control("status", crash["execution_id"])
        assert recovered["claim"] == claim and recovered["closed"] and recovered["outcome"] is None
        assert not recovered["manager_alive"]
        results["manager_crash"] = {
            "observed_workload_pids": workload,
            "closed": True,
            "result_not_fabricated": True,
            "replay_refused": True,
        }
        (root / "crash.receipt.json").write_text(json.dumps(recovered, indent=2), encoding="utf-8")
    finally:
        control("cleanup", crash["execution_id"])
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
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
        "receipt_negative_checks": receipt_checks,
        "product_tool_enabled": False,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Restricted WSL executor acceptance passed: {root}")


if __name__ == "__main__":
    main()

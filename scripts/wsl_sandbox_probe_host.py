"""Trusted Linux-side environment probe; not a model command executor."""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


def main() -> None:
    if os.name != "posix" or os.getuid() != 0:
        raise SystemExit("This capability probe requires the explicitly selected root WSL host.")
    root = Path(tempfile.mkdtemp(prefix="xaa-sandbox-probe-", dir="/tmp"))
    root.chmod(0o755)
    source = root / "input"
    source.mkdir(mode=0o755)
    (source / "fixture.txt").write_text("workspace-fixture", encoding="utf-8")
    sentinel = root / "outside-sentinel.txt"
    sentinel.write_text("outside-fixture", encoding="utf-8")
    sentinel.chmod(0o644)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    results: dict[str, Any] = {}

    def run_probe(code: str, *, runtime: int = 10, inspect_limits: bool = False) -> dict[str, Any]:
        unit = "xaa-sandbox-probe-" + uuid4().hex + ".service"
        command = [
            "/usr/bin/systemd-run",
            "--unit",
            unit,
            "--wait",
            "--pipe",
            "--collect",
            "--property=MemoryMax=128M",
            "--property=MemorySwapMax=0",
            "--property=CPUQuota=50%",
            "--property=TasksMax=32",
            f"--property=RuntimeMaxSec={runtime}s",
            "--property=TimeoutStopSec=1s",
            "--property=KillMode=control-group",
            "--setenv=XAA_API_TOKEN=synthetic-probe-secret",
            "/usr/bin/bwrap",
            "--unshare-all",
            "--unshare-user",
            "--unshare-cgroup",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-net",
            "--unshare-uts",
            "--disable-userns",
            "--assert-userns-disabled",
            "--uid",
            "65534",
            "--gid",
            "65534",
            "--cap-drop",
            "ALL",
            "--new-session",
            "--die-with-parent",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin:/bin",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--ro-bind",
            str(source),
            "/input",
            "--size",
            "33554432",
            "--tmpfs",
            "/tmp",
            "--remount-ro",
            "/",
            "--remount-ro",
            "/dev",
            "--chdir",
            "/tmp",
            "--",
            "/usr/bin/python3",
            "-I",
            "-c",
            code,
        ]
        started = time.monotonic()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        limits: dict[str, str] = {}
        outcome: dict[str, Any] = {}
        try:
            if inspect_limits:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    query = subprocess.run(
                        ["/usr/bin/systemctl", "show", unit, "--property=ControlGroup", "--value"],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    group = query.stdout.strip()
                    if group.startswith("/") and ".." not in Path(group).parts:
                        cgroup = Path("/sys/fs/cgroup") / group.lstrip("/")
                        if (cgroup / "memory.max").exists():
                            limits = {
                                name: (cgroup / name).read_text(encoding="utf-8").strip()
                                for name in ("memory.max", "memory.swap.max", "pids.max", "cpu.max")
                            }
                            break
                    time.sleep(0.02)
            stdout, stderr = process.communicate(timeout=runtime + 5)
            outcome = {
                "unit": unit,
                "exit_code": process.returncode,
                "stdout": stdout.decode("utf-8"),
                "stderr": stderr.decode("utf-8"),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "limits": limits,
            }
            return outcome
        finally:
            # Stop only the unique unit this invocation created, including descendants.
            subprocess.run(
                ["/usr/bin/systemctl", "stop", unit], check=False, capture_output=True, timeout=5
            )
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            state = subprocess.run(
                ["/usr/bin/systemctl", "show", unit, "--property=ActiveState", "--value"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            if state != "inactive":
                raise RuntimeError(f"Owned probe unit did not become inactive: {unit}: {state}")
            outcome["cleanup_confirmed"] = True

    try:
        isolation_code = f"""
import errno,json,os,pathlib,socket,time
assert pathlib.Path('/input/fixture.txt').read_text() == 'workspace-fixture'
pathlib.Path('/tmp/positive.txt').write_text('writable-scratch')
assert pathlib.Path('/tmp/positive.txt').read_text() == 'writable-scratch'
assert not pathlib.Path({str(sentinel)!r}).exists()
assert not pathlib.Path('/mnt/c').exists()
assert not pathlib.Path('/init').exists()
assert not pathlib.Path('/proc/1/root/mnt/c').exists()
assert 'XAA_API_TOKEN' not in os.environ
assert os.getuid() == 65534
status = pathlib.Path('/proc/self/status').read_text()
fields = dict(line.split(':',1) for line in status.splitlines() if ':' in line)
assert fields['NoNewPrivs'].strip() == '1'
assert int(fields['CapEff'].strip(),16) == 0
for path in ('/forbidden', '/dev/forbidden', '/dev/shm/forbidden'):
 try:
  pathlib.Path(path).write_text('forbidden')
 except OSError as e:
  assert e.errno in (errno.EROFS,errno.EACCES,errno.ENOENT)
 else:
  raise AssertionError('Unbounded writable root')
try:
 pathlib.Path('/input/fixture.txt').write_text('forbidden')
except OSError as e:
 assert e.errno in (errno.EROFS,errno.EACCES)
else:
 raise AssertionError('Read-only input was writable')
s=socket.socket();s.settimeout(1)
try:
 s.connect(('127.0.0.1',{port}))
except OSError:
 pass
else:
 raise AssertionError('Host network listener was reachable')
finally:
 s.close()
print(json.dumps({{'uid':os.getuid(),'host_sentinel_hidden':True,'host_network_denied':True,'credential_hidden':True,'capabilities_zero':True,'no_new_privileges':True,'readonly_input':True,'writable_scratch':True}}),flush=True)
time.sleep(1)
"""
        results["isolation"] = run_probe(isolation_code, inspect_limits=True)
        isolation = results["isolation"]
        assert isolation["exit_code"] == 0, isolation
        assert isolation["limits"]["memory.max"] == str(128 * 1024 * 1024)
        assert isolation["limits"]["memory.swap.max"] == "0"
        assert isolation["limits"]["pids.max"] == "32"
        quota, period = map(int, isolation["limits"]["cpu.max"].split())
        assert quota / period == 0.5
        results["storage"] = run_probe("""
import errno,json
written=0
try:
 with open('/tmp/fill','wb',buffering=0) as f:
  for _ in range(64):
   written += f.write(b'x' * 1048576)
except OSError as e:
 assert e.errno == errno.ENOSPC
 print(json.dumps({'storage_limit_enforced':True,'bytes_written':written}))
else:
 raise AssertionError('Scratch storage limit not enforced')
""")
        assert results["storage"]["exit_code"] == 0, results["storage"]
        results["processes"] = run_probe("""
import errno,json,subprocess
children=[]
try:
 for _ in range(64):
  children.append(subprocess.Popen(['/usr/bin/python3','-I','-c','import time;time.sleep(5)']))
except OSError as e:
 assert e.errno == errno.EAGAIN
 print(json.dumps({'process_limit_enforced':True,'children_started':len(children)}))
else:
 raise AssertionError('Process limit not enforced')
finally:
 for child in children:
  child.kill()
 for child in children:
  child.wait()
""")
        assert results["processes"]["exit_code"] == 0, results["processes"]
        results["cpu"] = run_probe("""
import json,time
started=time.monotonic();cpu=time.process_time()
while time.monotonic()-started < 2:
 sum(range(10000))
wall=time.monotonic()-started;used=time.process_time()-cpu
assert used/wall < 0.75
print(json.dumps({'cpu_seconds':used,'wall_seconds':wall,'ratio':used/wall}))
""")
        assert results["cpu"]["exit_code"] == 0, results["cpu"]
        results["memory"] = run_probe("buffer=bytearray(256*1024*1024)")
        assert results["memory"]["exit_code"] != 0
        assert "oom-kill" in results["memory"]["stderr"], results["memory"]
        results["runtime"] = run_probe(
            "import subprocess,time;subprocess.Popen(['/usr/bin/python3','-I','-c','import time;time.sleep(30)']);time.sleep(30)",
            runtime=2,
        )
        assert results["runtime"]["exit_code"] != 0
        assert "timeout" in results["runtime"]["stderr"], results["runtime"]
        assert results["runtime"]["elapsed_seconds"] < 7
        assert sentinel.read_text(encoding="utf-8") == "outside-fixture"
        assert (source / "fixture.txt").read_text(encoding="utf-8") == "workspace-fixture"
        versions = {}
        for label, command in {
            "bubblewrap": ["/usr/bin/bwrap", "--version"],
            "systemd": ["/usr/bin/systemd-run", "--version"],
            "kernel": ["/usr/bin/uname", "-r"],
        }.items():
            versions[label] = subprocess.run(
                command, check=True, capture_output=True, text=True
            ).stdout.strip()
        print(
            json.dumps(
                {
                    "passed": True,
                    "versions": versions,
                    "probes": results,
                    "product_execution_enabled": False,
                }
            )
        )
    finally:
        listener.close()
        if root.parent != Path("/tmp") or not root.name.startswith("xaa-sandbox-probe-"):
            raise RuntimeError("Refusing unexpected probe cleanup target.")
        shutil.rmtree(root)


if __name__ == "__main__":
    main()

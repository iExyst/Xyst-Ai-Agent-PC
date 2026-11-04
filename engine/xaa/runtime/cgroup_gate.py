"""Trusted ExecStartPre check: refuse execution unless kernel controls are effective."""

from pathlib import Path


def main() -> None:
    groups = Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines()
    unified = [line.removeprefix("0::") for line in groups if line.startswith("0::")]
    if len(unified) != 1 or not unified[0].startswith("/") or ".." in Path(unified[0]).parts:
        raise RuntimeError("A verified unified cgroup is required.")
    root = Path("/sys/fs/cgroup") / unified[0].lstrip("/")
    expected = {"memory.max": "134217728", "memory.swap.max": "0", "pids.max": "32"}
    for name, value in expected.items():
        if (root / name).read_text(encoding="utf-8").strip() != value:
            raise RuntimeError(f"Required execution limit is not effective: {name}")
    cpu = (root / "cpu.max").read_text(encoding="utf-8").split()
    if len(cpu) != 2 or cpu[0] == "max" or int(cpu[0]) * 2 != int(cpu[1]) or int(cpu[1]) <= 0:
        raise RuntimeError("Required half-CPU quota is not effective.")


if __name__ == "__main__":
    main()

# WSL restricted execution environment probes

On 2026-11-04 the Windows host listed four existing WSL distributions. The explicitly selected `Ubuntu-24.04` distribution already contains Bubblewrap 0.9.0, systemd 255, Python 3 and a unified cgroup hierarchy on kernel 6.18.33.2-microsoft-standard-WSL2. No dependency was installed or distro configuration changed. Its configured management account is root; sandbox commands run as namespace UID/GID 65534 with all capabilities removed and no-new-privileges set. WSL itself is not treated as isolation.

Run `uv run python scripts/wsl_sandbox_probe.py --distribution Ubuntu-24.04` from the Windows repository root. This development diagnostic sends a trusted, fixed Linux-side probe over stdin using structured subprocess arguments. It creates only unique temporary fixtures and transient `xaa-sandbox-probe-<uuid>.service` units. It does not accept model commands or enable an application tool. A missing distro, manager privilege, controller or namespace capability must fail; there is no direct host-shell fallback.

The manager requests a 128 MiB aggregate memory limit with swap disabled, a 50% CPU quota, 32 total tasks and a fixed runtime. Bubblewrap creates separate user, mount, process, network, IPC, UTS and cgroup namespaces, disallows additional user namespaces and drops all capabilities. Only the distro `/usr` runtime, a read-only synthetic input directory, private proc/dev mounts and a 32 MiB writable scratch tmpfs are mapped. Root and device directory mounts are explicitly read-only. Windows drives, the WSL interop `/init`, host home directories, engine credentials, sockets and host root are not mapped. The environment is cleared and reconstructed with PATH/LANG only.

Evidence `.acceptance/0a0d097a-2a8a-4dc8-8c98-9fc86caf4fa2/` and `../releases/evidence/wsl-bubblewrap-probe.json` records:

- Positive input read and scratch write; denied input/root/device-directory writes.
- Hidden readable outside-host sentinel, Windows mount and host proc-root drive path; synthetic engine token absent.
- A live host loopback listener cannot be reached from the isolated network namespace.
- Actual cgroup files report memory.max=134217728, memory.swap.max=0, pids.max=32 and cpu.max=50000 100000. A CPU loop consumes about 1.031 CPU seconds in 2.040 wall seconds (ratio 0.505).
- Scratch writes reach ENOSPC at 33554432 bytes; process creation reaches EAGAIN after 29 children; a 256 MiB allocation ends with systemd oom-kill under the 128 MiB limit.
- A sleeping parent plus descendant hits the two-second runtime limit. All six unique probe units are stopped and confirmed inactive; synthetic host input/sentinel contents remain unchanged and the unique Linux fixture directory is removed.

The initial direct command failed because this installed Bubblewrap requires explicit `--unshare-user` alongside `--unshare-all` for `--disable-userns`. That was corrected before any pass claim. Initial passing probes preceded the final read-only-root/CPU/descendant checks; the final evidence above covers those additions.

## Remaining execution gate

This proves specific mechanisms in this installed environment, not a supported X.A.A. command backend, complete escape resistance or Hermes execution parity. The diagnostic has no syscall filter; a production execution profile and its syscall/security review remain open. Product command tools remain unavailable. Required integration includes explicit distro/profile readiness and selection, bounded regular-file snapshot transfer without host junction/symlink mounts, durable intent/container identity, resource/permission inheritance, bounded output/artifacts, caller cancellation and crash cleanup/reconciliation, command argument validation, result transport, and real model/native fixture-project defect/patch/validation acceptance. Writable sandbox copies must have bounded storage and cannot silently overwrite original project files; integration needs reviewed, hash-checked changes.

Primary behavior references: [Bubblewrap documentation](https://github.com/containers/bubblewrap/blob/main/bwrap.xml), [systemd-run documentation](https://github.com/systemd/systemd/blob/main/man/systemd-run.xml). The local installed probes, not documentation alone, establish the observations above. The external pinned Hermes WSL terminal and WSLC declarations retain their separate source/runtime audit requirements.

Snapshot transport follow-up: the same diagnostic now captures a Windows fixture through verified handles, decodes/materializes it with the standard-library codec on Linux and uses that read-only copy inside the restricted process. It rejects an injected traversal envelope before staging. See workspace-snapshots.md and ../releases/evidence/wsl-snapshot-transfer.json for the newer source hashes and evidence. This does not enable the product command tool or remove the remaining execution gate above.

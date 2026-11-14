"""Linux-only sandbox profile built without project or model supplied configuration."""

import ctypes
import ctypes.util
import errno
import tempfile
from pathlib import Path


def syscall_filter() -> bytes:
    """Export a native-architecture BPF filter for Bubblewrap's child, fail closed."""
    library = ctypes.util.find_library("seccomp")
    if library is None:
        raise RuntimeError("libseccomp is required for restricted execution.")
    seccomp = ctypes.CDLL(library, use_errno=True)
    seccomp.seccomp_init.argtypes = [ctypes.c_uint32]
    seccomp.seccomp_init.restype = ctypes.c_void_p
    seccomp.seccomp_release.argtypes = [ctypes.c_void_p]
    seccomp.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    seccomp.seccomp_syscall_resolve_name.restype = ctypes.c_int
    seccomp.seccomp_rule_add_array.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_void_p,
    ]
    seccomp.seccomp_rule_add_array.restype = ctypes.c_int
    seccomp.seccomp_export_bpf.argtypes = [ctypes.c_void_p, ctypes.c_int]
    seccomp.seccomp_export_bpf.restype = ctypes.c_int
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Cannot initialize syscall filter.")
    denied = (
        "socket",
        "socketpair",
        "connect",
        "bind",
        "listen",
        "accept",
        "accept4",
        "sendto",
        "sendmsg",
        "sendmmsg",
        "recvfrom",
        "recvmsg",
        "recvmmsg",
        "mount",
        "umount2",
        "pivot_root",
        "chroot",
        "unshare",
        "setns",
        "bpf",
        "ptrace",
        "process_vm_readv",
        "process_vm_writev",
        "perf_event_open",
        "kexec_load",
        "kexec_file_load",
        "reboot",
        "swapon",
        "swapoff",
        "keyctl",
        "add_key",
        "request_key",
        "open_by_handle_at",
        "name_to_handle_at",
        "open_tree",
        "move_mount",
        "fsopen",
        "fsconfig",
        "fsmount",
        "fspick",
        "mount_setattr",
        "userfaultfd",
        "io_uring_setup",
        "io_uring_register",
        "io_uring_enter",
    )
    try:
        for name in (*denied, "clone3"):
            number = seccomp.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number < 0:
                raise RuntimeError(f"Installed libseccomp does not recognize {name}.")
            error = errno.ENOSYS if name == "clone3" else errno.EPERM
            if seccomp.seccomp_rule_add_array(context, 0x00050000 | error, number, 0, None) != 0:
                raise RuntimeError(f"Cannot configure syscall restriction: {name}.")
        with tempfile.TemporaryFile() as target:
            if seccomp.seccomp_export_bpf(context, target.fileno()) != 0:
                raise RuntimeError("Cannot export syscall filter.")
            target.seek(0)
            result = target.read(65537)
            if not result or len(result) > 65536:
                raise RuntimeError("Unexpected syscall filter size.")
            return result
    finally:
        seccomp.seccomp_release(context)


def bubblewrap_arguments(
    source: Path,
    argv: list[str],
    procedure_environment: Path | None = None,
    node_modules: Path | None = None,
    node_modules_link: str | None = None,
) -> list[str]:
    """stdin is the BPF descriptor and is consumed before the command starts."""
    bootstrap = (
        "import json,os,pathlib,shutil,sys;"
        "status=pathlib.Path('/proc/self/status').read_text();"
        "fields=dict(line.split(':',1) for line in status.splitlines() if ':' in line);"
        "assert os.getuid()==65534 and os.getgid()==65534;"
        "assert fields['NoNewPrivs'].strip()=='1' and fields['Seccomp'].strip()=='2';"
        "assert int(fields['CapEff'].strip(),16)==0;"
        "assert all(os.statvfs(p).f_blocks*os.statvfs(p).f_frsize<=33554432 for p in ('/tmp','/workspace'));"
        "shutil.copytree('/input','/workspace',dirs_exist_ok=True);"
        "os.chdir('/workspace');"
        "request=json.loads(sys.argv[1]);args=request['argv'];"
        "link=request.get('node_modules_link');\n"
        "if link is not None:\n"
        " p=pathlib.Path(link);assert p.is_dir() and not p.is_symlink();"
        " n=p/'node_modules';assert not n.exists() and not n.is_symlink();"
        " os.symlink('/opt/xaa/procedure-node/node_modules',n,target_is_directory=True)\n"
        "os.execvpe(args[0],args,dict(os.environ))"
    )
    import json

    path_parts = ["/usr/bin", "/bin"]
    environment_mount: list[str] = []
    if (node_modules is None) != (node_modules_link is None):
        raise ValueError("Node module mount and procedure link must be paired.")
    if node_modules_link is not None:
        import re

        if re.fullmatch(
            r"__xaa_selected_skill__/[a-f0-9-]{36}/v[1-9][0-9]{0,2}",
            node_modules_link,
        ) is None:
            raise ValueError("Selected procedure Node module link is invalid.")
        path_parts.insert(0, "/opt/xaa/procedure-node/node_modules/.bin")
        environment_mount.extend(["--dir", "/opt", "--dir", "/opt/xaa"])
        environment_mount.extend(
            [
                "--dir",
                "/opt/xaa/procedure-node",
                "--ro-bind",
                str(node_modules),
                "/opt/xaa/procedure-node/node_modules",
            ]
        )
    if procedure_environment is not None:
        path_parts.insert(0, "/opt/xaa/procedure-python/bin")
        if not environment_mount:
            environment_mount.extend(["--dir", "/opt", "--dir", "/opt/xaa"])
        environment_mount.extend(
            [
                "--dir",
                "/opt/xaa/procedure-python",
                "--ro-bind",
                str(procedure_environment),
                "/opt/xaa/procedure-python",
            ]
        )
    path = ":".join(path_parts)
    return [
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
        path,
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "HOME",
        "/tmp",
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
        *environment_mount,
        "--size",
        "33554432",
        "--tmpfs",
        "/tmp",
        "--size",
        "33554432",
        "--tmpfs",
        "/workspace",
        "--remount-ro",
        "/",
        "--remount-ro",
        "/dev",
        "--chdir",
        "/workspace",
        "--seccomp",
        "0",
        "--",
        "/usr/bin/python3",
        "-I",
        "-c",
        bootstrap,
        json.dumps({"argv": argv, "node_modules_link": node_modules_link}),
    ]

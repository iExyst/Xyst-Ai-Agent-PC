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


def bubblewrap_arguments(source: Path, argv: list[str]) -> list[str]:
    """stdin is the BPF descriptor and is consumed before the command starts."""
    bootstrap = (
        "import json,os,shutil,sys;"
        "shutil.copytree('/input','/workspace',dirs_exist_ok=True);"
        "os.chdir('/workspace');"
        "args=json.loads(sys.argv[1]);"
        "os.execvpe(args[0],args,dict(os.environ))"
    )
    import json

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
        "/usr/bin:/bin",
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
        json.dumps(argv),
    ]

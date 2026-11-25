"""Application-owned Docker isolation probe; accepts only a nonce fixture."""

PROBE_CODE = r"""
import errno,json,os,pathlib,socket
nonce=pathlib.Path('/workspace/probe.txt').read_text(encoding='ascii')
assert os.getuid()==65534 and os.getgid()==65534
fields={}
for line in pathlib.Path('/proc/self/status').read_text().splitlines():
    if ':' in line:
        key,value=line.split(':',1)
        fields[key]=value.strip()
assert fields.get('NoNewPrivs')=='1'
assert fields.get('Seccomp')=='2'
assert int(fields['CapEff'],16)==0

def read_limit(paths):
    for candidate in paths:
        try:
            return pathlib.Path(candidate).read_text(encoding='ascii').strip()
        except OSError:
            continue
    raise AssertionError('required cgroup resource limit is unavailable')

cgroup=pathlib.Path('/sys/fs/cgroup')
if (cgroup/'cgroup.controllers').exists():
    controllers=set((cgroup/'cgroup.controllers').read_text(encoding='ascii').split())
    assert {'cpu','memory','pids'} <= controllers
    assert read_limit((cgroup/'memory.max',))=='134217728'
    assert read_limit((cgroup/'memory.swap.max',))=='0'
    quota,period=read_limit((cgroup/'cpu.max',)).split()
    assert (int(quota),int(period))==(50000,100000)
    assert read_limit((cgroup/'pids.max',))=='32'
else:
    memory='/sys/fs/cgroup/memory/'
    assert read_limit((memory+'memory.limit_in_bytes',))=='134217728'
    assert read_limit((memory+'memory.memsw.limit_in_bytes',))=='134217728'
    quota=read_limit(('/sys/fs/cgroup/cpu/cpu.cfs_quota_us','/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_quota_us'))
    period=read_limit(('/sys/fs/cgroup/cpu/cpu.cfs_period_us','/sys/fs/cgroup/cpu,cpuacct/cpu.cfs_period_us'))
    assert (int(quota),int(period))==(50000,100000)
    assert read_limit(('/sys/fs/cgroup/pids/pids.max',))=='32'

assert os.statvfs('/').f_flag & os.ST_RDONLY
mounts={}
for line in pathlib.Path('/proc/self/mountinfo').read_text(encoding='ascii').splitlines():
    fields=line.split()
    if '-' not in fields:
        continue
    separator=fields.index('-')
    if separator<6 or len(fields)<separator+4:
        continue
    mounts[fields[4]]={'fstype':fields[separator+1],'options':set(fields[5].split(','))}
for path in ('/tmp','/workspace'):
    mount=mounts.get(path)
    assert mount is not None and mount['fstype']=='tmpfs'
    assert {'rw','nosuid','nodev','noexec'} <= mount['options']
    stats=os.statvfs(path)
    assert not stats.f_flag & os.ST_RDONLY
    assert 0 < stats.f_blocks*stats.f_frsize <= 33554432
shm_mount=mounts.get('/dev/shm')
assert shm_mount is not None and shm_mount['fstype']=='tmpfs'
shm=os.statvfs('/dev/shm')
assert not shm.f_flag & os.ST_RDONLY
assert 0 < shm.f_blocks*shm.f_frsize <= 33554432
assert not pathlib.Path('/var/run/docker.sock').exists()
pathlib.Path('/workspace/probe-write.txt').write_text(nonce,encoding='ascii')
assert pathlib.Path('/workspace/probe-write.txt').read_text(encoding='ascii')==nonce
network_denied=False
try:
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as connection:
        connection.settimeout(0.25)
        connection.connect(('192.0.2.1',443))
except OSError as error:
    assert error.errno in (errno.EPERM,errno.ENETUNREACH,errno.EHOSTUNREACH)
    network_denied=True
assert network_denied
print(json.dumps({'nonce':nonce,'checks':True,'network_denied':network_denied},separators=(',',':')))
"""

DOCKER_LOCAL_CHECKS = (
    "local_daemon",
    "linux_container_runtime",
    "digest_pinned_image",
    "unprivileged_identity",
    "seccomp_and_capabilities",
    "effective_cpu_memory_and_process_limits",
    "readonly_root",
    "bounded_private_tmpfs",
    "host_mounts_absent",
    "network_isolated",
    "cleanup_confirmed",
)

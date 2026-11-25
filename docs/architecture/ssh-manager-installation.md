# Install the SSH execution manager

X.A.A.'s desktop and bundled engine run on Windows. `ssh-wsl-bubblewrap-v1` is an optional execution backend for Linux hosts reachable with Windows OpenSSH; it is not required to launch or use the Windows desktop. A Linux target needs Ubuntu 24.04 or a compatible systemd Linux distribution with Python 3, OpenSSH server, `flock`, Bubblewrap, cgroup v2, and the X.A.A. sandbox prerequisites.

## Build and transfer a manager release

From the X.A.A. source checkout on Windows, create a new bundle and its matching standalone installer:

```powershell
uv run python scripts/package_ssh_manager.py `
  --output .acceptance/xaa-ssh-manager.zip `
  --installer-output .acceptance/install_ssh_manager.py
```

The output paths must not already exist. Transfer both files to the Linux target using an authenticated, trusted administrative channel. The standalone installer must be the one emitted alongside that exact bundle; it checks its own hash against the bundle manifest.

On the target, install the release as root:

```sh
sudo /usr/bin/python3 -I ./install_ssh_manager.py ./xaa-ssh-manager.zip
```

The installer verifies the complete archive inventory, sizes, resource hashes, manager digest, and installer hash. It stores immutable root-owned releases under `/opt/xaa-ssh-manager/releases/`, installs a fixed root-owned wrapper at `/usr/local/sbin/xaa-ssh-execution-manager`, and atomically switches `/opt/xaa-ssh-manager/current`. It waits for active manager invocations and refuses a switch while execution receipt leases exist. Previous releases are retained for operator-managed rollback.

## Configure the restricted SSH principal

Use a dedicated unprivileged account. The example below assumes Ubuntu and an administrator-managed public key:

```sh
sudo useradd --create-home --shell /bin/sh xaa-manager
sudo usermod --password "$(openssl passwd -6 "$(openssl rand -base64 36)")" xaa-manager
printf '%s\n' 'xaa-manager ALL=(root) NOPASSWD: /usr/local/sbin/xaa-ssh-execution-manager' \
  | sudo tee /etc/sudoers.d/xaa-ssh-manager >/dev/null
sudo chmod 0440 /etc/sudoers.d/xaa-ssh-manager
sudo visudo -cf /etc/sudoers.d/xaa-ssh-manager
sudo install -d -o xaa-manager -g xaa-manager -m 0700 /home/xaa-manager/.ssh
```

Put the provisioned public key in `/home/xaa-manager/.ssh/authorized_keys` with this forced-command form, replacing the key text:

```text
command="sudo -n /usr/local/sbin/xaa-ssh-execution-manager",restrict ssh-ed25519 AAAA... operator-key
```

Keep the file owned by `xaa-manager` with mode `0600`. Configure sshd to permit public-key authentication for this account, disable password authentication, and deny root login. X.A.A. separately pins the target host key from the Windows user's `.ssh/known_hosts`, uses strict host-key checking, and disables forwarding and TTY allocation for every invocation. Do not give this account an unrestricted sudo rule or reuse this key for interactive administration.

In the Windows user's OpenSSH config, define a host alias pointing to the Linux target and account. Add the target's verified host key to that user's `known_hosts`; select the matching private key in X.A.A.'s execution setup. The readiness check sends a nonce challenge and verifies that every installed manager resource has the same digest as the Windows client before it grants execution readiness.

For an update, build and transfer a new matching pair, run the installer again, and wait for it to report success. The manager refuses to update during an active execution or lease. The next readiness check verifies the newly installed manager against the updated Windows engine. If the application and target manager are on different releases, readiness fails closed.

## Verification boundary

Local Windows OpenSSH to Ubuntu 24.04 WSL acceptance exercises package installation, a manager upgrade, remote profile mismatch rejection, restoration to the matching release, sandbox readiness, and cleanup. This does not yet establish acceptance on an independently administered Linux host, reconnect after a Windows app restart, signed manager artifacts, or Hermes SSH adapter parity. See [`progress.md`](../progress.md) and the [Hermes parity matrix](../parity/hermes-matrix.md).

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

## Verify command recovery after an engine restart

With the restricted host configured and the SSH grant ready in X.A.A., run the deterministic recovery acceptance from the source checkout:

```powershell
uv run python scripts/ssh_command_recovery_acceptance.py `
  --alias xaa-e2e `
  --identity "$env:USERPROFILE\.ssh\id_ed25519" `
  --report .acceptance/ssh-command-recovery/report.json
```

The harness starts a separate Windows worker process. It executes one bounded command, persists the matching stdout and stderr artifacts, then exits at the journal-completion boundary. A fresh process reopens the SQLite store, obtains a replacement run lease, rediscovers the pinned SSH target and manager, and reconciles the saved receipt. The command cannot be relaunched by recovery. The report should show one unresolved call before reconciliation, a successful result with `result_reconciled=true`, and zero unresolved calls afterward.

To retain the unresolved profile for the bundled desktop engine to recover, pass an empty data directory and `--defer-to-desktop`:

```powershell
uv run python scripts/ssh_command_recovery_acceptance.py `
  --alias xaa-e2e `
  --identity "$env:USERPROFILE\.ssh\id_ed25519" `
  --data-root .acceptance/ssh-native-recovery/data `
  --defer-to-desktop `
  --report .acceptance/ssh-native-recovery/engine-recovery.json
```

Then launch the Tauri desktop and verify that the saved remote output appears in the run view:

```powershell
npm run test:native:ssh-recovery -- `
  build/native-dev/xaa-desktop.exe `
  .acceptance/ssh-native-recovery/data `
  <run-id-from-engine-recovery-report> `
  ssh-recovery-verified
```

The worker exits after the remote command and its output artifacts are durable, before local journal completion. With `--defer-to-desktop`, the acceptance releases the old lease but leaves the command unresolved. The native check launches the real Tauri app and its bundled engine against that same profile. It requires the app engine to reconcile the remote receipt, exactly one command dispatch/completion and matching output event, and visible recovered stdout in the run view. No model is configured and the scenario makes no inference request. The retained data directory must be empty when the engine acceptance starts.

## Verification boundary

Local Windows OpenSSH to Ubuntu 24.04 WSL acceptance exercises package installation, manager upgrade, remote profile mismatch rejection, restoration, sandbox readiness, and cleanup. The native check starts the bundled Tauri engine with an unresolved SSH receipt from a worker that exited at the journal commit boundary; it verifies engine reconciliation and visible UI output without re-executing the command or requesting a model. With no model selected, the recovered run pauses after reconciliation. This does not simulate the Tauri process itself dying while the SSH transport call is active before the remote result is saved. Independent-host acceptance, signed manager artifacts, and Hermes SSH adapter parity also remain separate gaps. See [`progress.md`](../progress.md) and the [Hermes parity matrix](../parity/hermes-matrix.md).

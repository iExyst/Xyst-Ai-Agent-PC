# Execution backend acceptance requirements

Reference: Hermes commit `1e208b280deb18f01d015b0acdf3ef3552d08039`, audited 2026-11-03. `execution-inventory.json` records source hashes, class line numbers, factory names, external repository pins and discovery limits. Run `uv run python scripts/audit_execution_inventory.py --check` to detect drift against the pinned checkout. The script parses source syntax and safe YAML; it does not import upstream modules, install plugins or execute their code.

The audit checkout is a development-only input, excluded from the X.A.A. package. To reproduce from a checkout without it, run the following from the repository root; an existing `.hermes-audit` directory must be inspected rather than overwritten:

```powershell
git clone --filter=blob:none --no-checkout https://github.com/NousResearch/hermes-agent .hermes-audit
git -C .hermes-audit checkout --detach 1e208b280deb18f01d015b0acdf3ef3552d08039
uv run python scripts/audit_execution_inventory.py --check
```

The factory defines six built-in families: local, Docker, SSH, Modal, Daytona and Vercel Sandbox. The registry also reserves `managed_modal`; the Modal builder selects that internal mode through gateway configuration. It is not a seventh independent factory backend. The inspected base contract describes per-call shell processes, session-state snapshots, persisted working directories and process cleanup. Source inspection here covers declarations and selected requirement/lifecycle paths, not every implementation branch. X.A.A. must implement its own execution code and pass observable acceptance tests; a compatible interface does not establish parity.

No arbitrary-command backend is implemented in X.A.A. The current fixed Git inspector and filesystem workers are supporting application operations. Their process lifecycle controls do not establish a security boundary for generated code.

## Requirements shared by every claimed backend

| ID | Observable acceptance requirement | Required evidence |
|---|---|---|
| EXEC-COMMON-1 | An explicitly configured backend runs a fixture command in the declared workspace. UI/API identifies the actual environment, working directory, execution ID and exit status. Unavailable selection produces an actionable error without changing backend. | Real environment identity, captured command/argument boundaries, stdout/stderr, exit code and durable event records. |
| EXEC-COMMON-2 | Generated code cannot read or modify sentinel files outside its granted workspace, access the engine API credential, or expand its own permissions. Network access follows its explicit grant. | Positive workspace operations plus denied host/credential/network probes on the actual backend. A timeout or separate process alone cannot pass this requirement. |
| EXEC-COMMON-3 | Required wall-time, output, memory, CPU, process and storage limits are enforced or the operation is refused before execution. Unsupported controls are reported accurately. | Actual limit probes and measured outcomes. Upstream Docker gates some resource flags on a capability probe; that behavior is not evidence that every host enforces them. |
| EXEC-COMMON-4 | Pause/cancel/emergency stop terminate the owned command tree or report unresolved remote execution. A crash after a possible write requires destination reconciliation before repetition. | Real long-running child/descendant interruption, service restart and write-boundary kill tests with unchanged unrelated work. |
| EXEC-COMMON-5 | Unicode stdout/stderr and exit status survive transport; excerpts stay bounded and complete outputs become inspectable hashed artifacts. Model text is never promoted to an executable command outside a validated tool proposal. | Large/fragmented output, malformed responses, argument-boundary probes, artifact hashes and transcript inspection. |
| EXEC-COMMON-6 | Independent coding tasks have isolated workspaces. Persistence/restart retains only the intended task's files; integration resolves conflicts and runs project validation. Nonpersistent cleanup does not delete another task's workspace. | Two concurrent task fixtures, restart/reconnect, conflicting edits, successful merged validation and teardown evidence. |
| EXEC-COMMON-7 | Credentials stay in the OS store and narrowly scoped transport. Host homes, skill caches and credential files are not uploaded or mounted implicitly. Cloud execution requires explicit configuration and destination authorization. | Credential/environment sentinel probes, redacted exports/logs, inspected mount/upload manifests and permission decisions. |
| EXEC-COMMON-8 | Health checks distinguish missing executable/SDK, unavailable service, rejected authentication and unsupported configuration. Setup cannot silently provision a billable resource or fall back to unrestricted host execution. | Real dependency/authentication failures, unreachable service, cleanup failures and selected-backend persistence tests. |

These are X.A.A. release requirements. Catalog descriptions sometimes declare credential uploads or reduced approval behavior; that does not authorize X.A.A. to copy those policies.

## Built-in families and internal mode

Every row remains **planned** in X.A.A. Phase 3 requires a real restricted execution/validation slice; remaining backend parity is Phase 9. Dependencies listed here describe the pinned reference and the future acceptance environment, not configured accounts on this machine.

| ID | Phase | Pinned source | Specific acceptance scenario |
|---|---:|---|---|
| EXEC-LOCAL | 3 | `tools/environments/local.py`, `base.py` | Run a fixture test in a restricted local environment; preserve intended CWD/session behavior; deny host sentinels and terminate descendants. Plain full-access shell execution does not satisfy X.A.A.'s generated-code restriction. |
| EXEC-DOCKER | 3 | `tools/environments/docker.py`, `docker_egress.py`, `terminal_tool_backends.py` | With a real daemon/image, inspect mounts, effective capabilities and limits; deny unintended host/socket/credential access; verify configured egress, task-specific persistence, cancellation and cleanup. Missing required limits must not be described as enforced. |
| EXEC-SSH | 9 | `tools/environments/ssh.py` | Use a configured test host with verified identity and a restricted account/workspace. Check host-CWD mapping, stdin/exit status, reconnect, remote cancellation and permitted artifact transfer. SSH transport alone does not establish isolation. |
| EXEC-MODAL | 9 | `tools/environments/modal.py` | Explicitly authorize a real account and sandbox image; verify task snapshot restoration, bounded execution, output/artifact transfer, cancellation and resource cleanup. Inspect any proposed file synchronization before granting it. |
| EXEC-MANAGED_MODAL | 9 | `tools/environments/managed_modal.py` | Use a configured gateway and authorized user token; exercise create/exec/poll/cancel, idempotency and reconnect. The pinned mode rejects host credential-file passthrough; test that restriction and distinguish it from direct Modal mode. |
| EXEC-DAYTONA | 9 | `tools/environments/daytona.py` | With configured SDK/account credentials, create an isolated fixture sandbox, verify stop/resume persistence, actual resource behavior, interrupted execution and cleanup. |
| EXEC-VERCEL_SANDBOX | 9 | `tools/environments/vercel_sandbox.py`, `terminal_tool_backends.py` | Validate token/project/team or supported development OIDC setup, supported runtime/image and disk constraints. Verify sandbox identity, snapshot restore, stdin transport, cancellation and stop/cleanup with real service evidence. |

## External catalog declarations

Eight declarations were identified after inspecting 25 keyword candidates across the pinned catalog. Seventeen related-feature matches were excluded with recorded reasons. Search uses description text containing `terminal` and either `backend` or `environment`; this is a discovery aid, not proof that every external capability has been found. The first narrower phrase search missed both Windows entries, demonstrating that limitation.

The inventory records each catalog's external repository SHA and subdirectory. Those external sources have not yet been fetched or executed. Empty platform lists do not establish Windows support. Each row remains **planned**, Phase 9, and requires external source inspection followed by the shared contract and its scenario below.

| Declaration | Catalog-described behavior/constraint | Acceptance scenario still required |
|---|---|---|
| bubblewrap | Linux bwrap/prlimit profiles; writable workspace with profile-dependent network behavior. | Probe real mount/user/process/network policy for each profile, credential hiding and effective resource controls. |
| cua-spaces | Remote Space execution; catalog describes terminal/file/code and optional interactive desktop routing. | Explicit remote authorization; prove command and file operations use the same selected Space, with cancellation and scoped desktop routing where claimed. |
| deepinfra-sandbox | Billable remote sandbox; catalog discloses host skill/cache/credential-file upload behavior. | Audit pinned implementation; require an explicit minimal transfer manifest and verify remote isolation, spending authorization and cleanup. |
| singularity | Linux Apptainer/Singularity container and optional persistent overlay. | Inspect real containment/home/mount settings, enforce limits, verify overlay isolation and teardown. |
| smolmachines | Local microVM on supported macOS/Linux hosts; catalog describes no account requirement. | Prove local VM identity, supported host constraints, host-file restrictions, persistence and descendant/VM cleanup. |
| upstash-box | Named/persistent or ephemeral cloud containers; catalog discloses account credentials and uploaded files accessible inside the box. | Audit all implicit credentials/transfers; verify explicit policy, per-task identity, pause/resume/delete and remote cancellation. |
| hermes-wsl-terminal | Existing WSL distro, translated host paths, full access as the configured user; explicitly not a sandbox. | Inspect pinned source and verify routing/CWD/exit behavior. It cannot enable X.A.A. generated-code execution until an additional restricted boundary passes the sentinel tests. |
| hermes-wslc-sandbox | WSLC container; catalog discloses unsupported capability/security/PID flags and unenforced disk quota. | On a real Windows WSLC host, measure isolation and every required limit. Refuse requirements the runtime cannot enforce rather than borrowing Docker's security claims. |

## Current acceptance availability

The local check found `wsl.exe` and OpenSSH, but no `docker`, `podman`, `wslc`, `bwrap` or `apptainer` executable on PATH. WSL distro availability was not established, and a WSL executable would not itself provide generated-code isolation. No restricted SSH destination or cloud execution account is configured for these acceptance tests. These are test-environment gaps; none of the backend rows is claimed implemented or verified.

Phase 0 remains open for external pinned-source inspection, remaining catalog discovery, channel-specific behavior and the other capability families. Phase 3 remains open for actual restricted commands and project validation.

Local availability follow-up (2026-11-04): existing Ubuntu-24.04 WSL contains Bubblewrap 0.9.0/systemd 255 with working namespaces and cgroup v2 controllers. Actual isolated probes pass filesystem/network/credential denial, memory/process/scratch/CPU limits and runtime cleanup. See ../architecture/wsl-sandbox-probes.md and ../releases/evidence/wsl-bubblewrap-probe.json. This supersedes the earlier unknown distro availability; it does not enable model commands or establish any backend parity. Snapshot transport, durable executor integration, production syscall policy and real project acceptance remain open.

Executor follow-up: a development WSL/Bubblewrap manager now executes structured fixture commands with a native syscall filter, private writable snapshot copies, bounded output/resources and observed-workload cancellation. See ../architecture/wsl-executor.md. The application does not expose a command tool: durable run/lease integration, startup/crash/replay recovery, packaged resources and model/native acceptance are still open. Existing backend parity rows remain planned.

Persistent-manager follow-up: actual Linux receipt/start-condition tests now cover delayed/replayed/concurrent launches and manager-process SIGKILL recovery without assuming a missing result was successful. See ../architecture/wsl-executor.md and ../releases/evidence/wsl-execution-receipts.json. This narrows the backend mechanism gap; Windows run/lease/permission/recovery integration and full backend acceptance remain incomplete.

Windows journal follow-up (2026-11-04): internal explicit grants, original-lease launch binding, registered snapshots/output artifacts, two-command per-database admission and known/missing-result reconciliation now pass SQLite plus actual WSL diagnostics. Stop/resume revokes stale worker authority. See ../architecture/wsl-executor.md and ../releases/evidence/windows-command-journal.json. Product controller, UI/model permission flow, protected runtime/state policy, packaging and real model/native command acceptance remain incomplete.

Shared-admission follow-up: current manager processes now share two persistent execution slots per selected distro across independent Windows databases, including retention after manager SIGKILL and release only after verified unit/staging cleanup. Literal environment-like argv and packaged special-path ExecStartPre also pass actual tests. See ../architecture/wsl-executor.md and ../releases/evidence/wsl-shared-admission.json. Windows-wide cross-distro/backends budgeting, profile upgrades, public grants and native/model command workflows remain incomplete; this is not completed backend parity.

Model-dispatch follow-up (2026-11-04): source coordinator command discovery/dispatch now consumes persisted local-user grants and the actual owned controller. A real local Qwen model proposed one command, received its real WSL result and reported the exact stdout after one bounded answer-only clarification. Original project unchanged; no unresolved execution; private output remains untrusted. Evidence: ../releases/evidence/model-command-execution.json. HTTP/native grant workflows, realistic project defect/validation acceptance and the remaining backend parity gates are still open.

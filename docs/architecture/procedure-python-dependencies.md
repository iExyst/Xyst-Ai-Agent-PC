# Procedure Python dependencies

## Scope

Python dependency declarations in imported Agent Plugin and Hermes procedures are untrusted metadata. The existing check reads installed distribution metadata without importing packages. Installation must remain a separate, owner-initiated action. A requirement declaration, model response, procedure activation, or command grant never installs a package by itself.

This design supports Python wheels only. Other managers, requested extras, direct URLs, and source distributions stay explicitly unsupported until they have their own bounded installer and provenance rules.

## Immutable environment identity

An environment belongs to one exact procedure identity and version, source-content digest, target WSL distribution, Python implementation/version/ABI, and normalized PEP 508 requirement set. The authenticated WSL readiness probe now captures the target interpreter's marker environment and ordered compatible wheel tags from a temporary private venv. Its canonical manifest and all downloaded wheel bytes receive SHA-256 digests. The environment key is the digest of that complete input; updates create a new environment and never mutate an existing one.

`PyPIWheelResolver` now turns the complete imported requirement set and server-probed target into a pending candidate lock. It uses PEP 691 wheel records and PEP 658 `core-metadata`, filters by ordered target tags and `Requires-Python`, evaluates markers against the WSL target, and performs bounded backtracking over transitive `Requires-Dist` constraints. It accepts only binary wheels with SHA-256 and size records and rejects unsupported extras, direct references, yanked files, and candidates without published metadata. Resolution is bounded by time, projects, files, candidate versions, metadata requests, package count, and total wheel size. The desktop exposes this as an owner-initiated action after a successful target probe. Metadata from PyPI remains an untrusted proposal until `PyPIWheelAcquirer` downloads the exact wheel files and `verify_locked_wheel_bytes` confirms the recorded hashes, `METADATA`, and `WHEEL` tags. A mismatch blocks approval.

This resolver and exact-lock fetcher currently run in the engine process. Their HTTP clients are bounded, TLS-verified, restricted to PyPI's fixed hosts, and ignore ambient proxies, but they do not yet have a separate OS process/resource boundary. This does not satisfy the production isolation requirement below. `ProcedureWheelhouseStore` atomically persists verified bytes under the canonical environment digest and rechecks its manifest and wheel bytes on reads. `SkillStore` allows approval only after that exact wheelhouse is present and passes fresh verification. No installation path consumes approved locks.

## Preparation and installation boundaries

1. The production resolver must obtain binary wheels from public PyPI only, using normal TLS verification. The current metadata resolver and exact-lock fetch core are not isolated: they run in the engine process without a separate process resource boundary. The production resolver must receive only normalized requirements and target ABI; it must receive no project workspace, procedure code, user credentials, or application secrets. It must run unprivileged with bounded time, memory, process count, disk, and output. Source distributions are refused so package build hooks never run during resolution.
2. The resolver records every wheel's normalized project name, version, compatible tag, size, SHA-256, and normalized `Requires-Dist` entries parsed from its wheel metadata. The owner reviews this exact lock before installation. The installer must independently compare recorded metadata to the bytes whose hash matches the lock.
3. Installation consumes only the verified wheelhouse with networking disabled, `--no-index`, and binary-only rules. It runs as the unprivileged sandbox identity with system files read-only and a single bounded writable target. No project files or procedure scripts are present during installation.
4. The manager validates the completed target contains only bounded regular files/directories, rejects links and special files, computes a content digest, and seals the immutable target read-only. The manifest and status are committed only after installation and cleanup are confirmed. Failed or interrupted preparations remain unavailable and can be safely removed or reconciled by their exact environment ID.

The persistent cache is application-owned WSL data, outside `/usr`, `/opt` system packages, user site-packages, and project workspaces. The offline installed environment is mounted read-only only for a command whose saved authorization names the matching procedure version and environment digest. The matching virtual-environment interpreter is selected for that command; the host interpreter and system Python remain unchanged. The command profile continues denying networking and exposing only the selected private workspace, runtime, and explicitly authorized dependency environment.

## Lifecycle and authority

- Discovery and dependency readiness remain advisory and do not install, import, or grant authority.
- The desktop requires an explicit owner action after showing the complete resolved lock. Approval is tied to one immutable procedure version and target environment.
- Revision creates a separate candidate and cannot silently reuse a prior environment. A previously approved environment may be selected again only when its full identity matches.
- Rollback selects an older immutable environment. It does not rewrite package files in place.
- Removal is a separate owner action, addresses only one exact environment digest, and refuses while a command still holds that environment.
- Logs and API responses expose package identifiers and versions, not credentials, environment values, or local installation paths.

## Acceptance gates

Use a local package index fixture with a small binary wheel and a transitive dependency. Verify resolver backtracking, exact and ranged constraints, tag preference, `Requires-Python`, target markers, yanked-file refusal, metadata-hash checks, and bounded project/candidate/search limits. Then verify lock normalization and hashes, `Requires-Dist` extraction against wheel bytes, dependency graph closure, owner review before installation, offline installation, and successful import only from the exact selected environment inside the network-denied WSL sandbox. Verify the same package is unavailable to other skills and ordinary commands. Exercise interrupted download/install reconciliation, immutable revision and rollback, refusal of source distributions/direct URLs/path escape/symlink targets, per-environment size/time/process limits, active-use removal refusal, uninstall, and preservation of the system interpreter, project bytes, credentials, and unrelated environments. No live model or public package service is required for acceptance.

Until these behaviors pass, imported Python dependencies remain visible as declarations but unsupported for installation and execution.

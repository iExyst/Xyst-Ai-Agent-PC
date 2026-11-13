# Procedure Python dependencies

## Scope

Python dependency declarations in imported Agent Plugin and Hermes procedures are untrusted metadata. The existing check reads installed distribution metadata without importing packages. Installation must remain a separate, owner-initiated action. A requirement declaration, model response, procedure activation, or command grant never installs a package by itself.

This design supports Python wheels only. Other managers, requested extras, direct URLs, and source distributions stay explicitly unsupported until they have their own bounded installer and provenance rules.

## Immutable environment identity

An environment belongs to one exact procedure identity and version, source-content digest, target WSL distribution, Python implementation/version/ABI, and normalized PEP 508 requirement set. Its canonical manifest and all downloaded wheel bytes receive SHA-256 digests. The environment key is the digest of that complete input; updates create a new environment and never mutate an existing one.

The owner reviews the target distribution, Python ABI, normalized direct requirements, resolved package names/versions, wheel filenames, hashes, and each wheel's `Requires-Dist` declarations before installation. Direct references, VCS requirements, editable installs, constraints files, alternate indexes, extras, and unparsed declarations are rejected. Environment markers are evaluated against the selected target interpreter. The lock schema checks that the declared dependency graph is complete, version-compatible, and contains no unreachable wheels. A reusable verifier now checks lock-matched wheel bytes, SHA-256, `METADATA`, and `WHEEL` tags (`engine/xaa/extensions/procedure_dependencies.py`). It is not yet connected to acquisition or installation, so declarations submitted through the current lock API still lack byte evidence.

## Preparation and installation boundaries

1. A fixed resolver obtains binary wheels from public PyPI only, using normal TLS verification. The acquisition process receives only the normalized requirements and target ABI; it receives no project workspace, procedure code, user credentials, or application secrets. It runs unprivileged with bounded time, memory, process count, disk, and output. Source distributions are refused so package build hooks never run during resolution.
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

Use a local package index fixture with a small binary wheel and a transitive dependency. Verify lock normalization and hashes, `Requires-Dist` extraction against hashed wheel bytes, dependency graph closure, target-ABI and marker handling, owner review before installation, offline installation, and successful import only from the exact selected environment inside the network-denied WSL sandbox. Verify the same package is unavailable to other skills and ordinary commands. Exercise interrupted download/install reconciliation, immutable revision and rollback, refusal of source distributions/direct URLs/path escape/symlink targets, per-environment size/time/process limits, active-use removal refusal, uninstall, and preservation of the system interpreter, project bytes, credentials, and unrelated environments. No live model or public package service is required for acceptance.

Until these behaviors pass, imported Python dependencies remain visible as declarations but unsupported for installation and execution.

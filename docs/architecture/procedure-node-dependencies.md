# Procedure Node.js dependencies

## Current support

X.A.A. can stage linked `.js`, `.mjs`, and `.cjs` resources into restricted WSL command snapshots and can report the selected WSL Node/npm versions. Imported npm declarations are displayed as unverified metadata. An authenticated API action and desktop preview parse linked `package-lock.json` v3 and `package.json` resources against the exact imported procedure identity and a fresh WSL runtime check. The parser validates their stored hashes, root dependency agreement, package names/paths, registry URLs, and SHA-512 integrity syntax.

The parser does not fetch or hash tarballs, prove that package versions satisfy the declared semver ranges, close the complete transitive dependency graph, persist an owner-reviewed lock, install packages, or expose a Node environment to procedure execution. A parsed proposal must not be treated as a trusted or installable lock.

## Planned immutable identity

A Node environment should be identified by the imported procedure ID/version and source hash, the exact `package.json` and lockfile hashes, target WSL distribution, Node/npm versions, normalized declared requirements, and every package path/version/tarball digest. Any change creates a separate environment digest; installed trees stay immutable and are removed by exact digest only.

The current parser accepts lockfile format version 3, registry tarballs on `https://registry.npmjs.org`, and SHA-512 SRI values. It refuses links, bundled packages, alternate registries, URL/Git dependency declarations, and other lockfile formats. These are validation boundaries for the first implementation slice, not a claim that every npm project can be installed.

## Required lifecycle before execution support

1. Read `package.json` and `package-lock.json` only from integrity-checked linked resources of the selected imported procedure version. Require exact manifest/lock consistency and verify npm ranges and the complete dependency graph with a pinned semantic-version implementation.
2. Resolve only supported public registry records. Run untrusted metadata parsing in an isolated, network-denied worker behind a fixed-host broker. Fetch only exact lockfile tarballs, with redirects disabled and byte, count, size, time, and output limits. Verify every SRI digest and package identity against the tarball contents.
3. Persist the exact proposal and acquired tarballs under a content-addressed identity. Display the complete package set, versions, tarball sizes, integrity values, and install-script presence. Require a separate owner approval for that exact digest.
4. Install only the approved lock into a new, unprivileged, network-denied WSL environment. Use a fixed npm command with scripts and extensions disabled; do not invoke package scripts during preparation. Validate the resulting tree, seal it read-only, and publish its manifest only after success.
5. Bind a selected installed environment digest and tree hash into an immutable procedure-selection event. Mount only that environment read-only for commands authorized to run that exact procedure version. Hold an active-use lease through cleanup and refuse removal while in use.
6. Accept interrupted-install recovery, idempotent reinstall, rollback to an older digest, removal, system/runtime preservation, project isolation, and hostile package path/tarball cases before claiming support. The model-mediated imported procedure run remains deferred until the end per the user's preference.

npm's package lock and `npm ci` semantics are documented at [package-lock.json](https://docs.npmjs.com/files/package-lock.json) and [npm ci](https://docs.npmjs.com/cli/commands/npm-ci/). In particular, `npm ci` requires a lockfile and checks that the lock and manifest agree; `ignore-scripts` prevents install-time lifecycle scripts but does not prohibit deliberately invoking a script command. X.A.A. must keep installation commands fixed and execute no package script as part of setup.

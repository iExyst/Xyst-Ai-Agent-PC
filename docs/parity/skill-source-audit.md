# Pinned Hermes skill source audit

Reviewed 2026-11-11 against Hermes commit `1e208b280deb18f01d015b0acdf3ef3552d08039`. This review covers the core skill lookup and management paths only. It does not exhaust every bundled/optional skill, plugin-catalog package, or execution backend, and it is not runtime acceptance.

## Source findings

| Pinned source | SHA-256 | Findings |
|---|---|---|
| `tools/skills_tool.py` | `0b6fbd304a6fff74c3e1afee3e959985b2205dc1926c7474ca5cc7a8bd8ea107` | Discovers skill directories across configured search roots, resolves name collisions by root tier/rank, rejects traversal and absolute lookup names, honors hidden/disabled and platform rules, resolves linked files, and checks declared skill readiness. `skill_view` loads the chosen skill for the active context and applies preprocessing. |
| `tools/skill_manager_tool.py` | `67df9575e2c22f7ac269e01404a2d6df6a5070d3633288dd00b479405c5bb853` | Provides create/edit/patch/delete and supporting-file operations. It validates identifiers, frontmatter and content size; uses per-skill mutation locks; performs security scanning; gates or stages writes; records successful mutations and keeps exact rollback data for guarded writes. |

## Behavioral comparison

- Hermes skills are filesystem directories with a `SKILL.md` plus optional linked files. Discovery is ordered across several roots and includes collision handling, hidden/disabled state, platform compatibility, and dependency-readiness checks. A skill can carry toolset/tool requirements and fallback declarations that affect visibility against the current runtime schema.
- Hermes' manager allows an agent to draft and revise files, but mutations pass validation, locking, security scanning and the configured write-approval gate. A successful tool response does not itself establish that a new procedure works; the separate runtime and user workflow determine whether the skill is subsequently useful.
- X.A.A. imports package skills as inactive candidates, records immutable versions and evaluation evidence, binds requirements to explicit X.A.A. permissions, and requires owner activation. It supports version rollback, lifecycle history, owner pinning, package provenance and bounded supporting assets. Package-declared requirements are not grants; unknown tool names fail closed. These behaviors are documented in the skill matrix row and covered by their cited focused tests/hosted runs.
- The two systems have different catalogs and execution semantics. X.A.A. does not execute arbitrary Hermes skill shell snippets or treat upstream toolset names as equivalent to X.A.A. grants. For a selected immutable procedure, linked `.py` and `.sh` assets can be included in a private restricted command snapshot only when the parent has a saved command grant; specialist tasks also require explicit `workspace.command` permission. This narrow path does not install dependencies or execute other script types. The remaining gaps are exhaustive upstream skill/package inventory, compatibility over the full pinned catalog, broader cross-platform readiness, and runtime acceptance across representative real packages.

## Review boundary

Only the two listed core source files were hash-reviewed here. No skill package was installed or executed during this audit, no external repository was contacted, and no claim is made that all discovery roots, package assets, permission declarations, or platform-specific behaviors are covered. See `hermes-matrix.md` and `catalog-inventory.json` for the separate capability and catalog boundaries.

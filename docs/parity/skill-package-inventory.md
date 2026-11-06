# Pinned skill package inventory

Reference: Hermes commit `1e208b280deb18f01d015b0acdf3ef3552d08039`, audited 2026-11-06. The generated [`skill-package-inventory.json`](skill-package-inventory.json) fingerprints tracked files under `skills/` and `optional-skills/`, parses bounded YAML frontmatter, records package-relative files and local Markdown destinations, and does not import or execute package content.

Regenerate or check from the repository root with:

```powershell
uv run python scripts/audit_skill_packages.py
uv run python scripts/audit_skill_packages.py --check
```

The script requires the clean pinned `.hermes-audit` checkout used by the other source audits. It fetches blob contents in one Git batch and stores hashes, modes, and bounded metadata rather than skill bodies.

## Inventory findings

- The pin contains 44 default bundled skills and 142 optional skills in 23 categories, covering 1,127 tracked files. Optional skills are shipped but not active by default. Thirty-six files have the executable Git mode; many more package files are scripts or code examples without that mode.
- All 186 skills declare `platforms`; 148 include Windows, 182 Linux, and 184 macOS. All declare `name`, `description`, `version`, `author`, and `license`. Twenty-five use `prerequisites`, 30 use `dependencies`, and 181 use the Hermes-specific nested `metadata.hermes` extension. Nine packages declare Hermes `requires_toolsets`, using `terminal`, `files`, or `kanban`; none declares Agent Skills `allowed-tools` at top level. Package dependency, credential, and tool requirements therefore cannot be inferred from a tool manifest alone.
- X.A.A. now accepts the pinned `metadata.hermes` mapping as untrusted package metadata and uses its `category` only when the standard `metadata.category` is absent. Conflicting categories are rejected. Discovery shows a bounded summary of declared platforms, Hermes toolsets, prerequisite field and executable names, required environment-variable names, and dependency counts. A per-discovery check compares platform declarations, command-name presence on `PATH`, and required environment-name presence with the current engine process. At most 256 distinct command names are checked in one scan; further names are explicitly reported as unchecked. The check reveals neither environment values nor command paths and is not persisted. The declaration summary is retained in immutable candidate source provenance; dependency values and environment values are omitted. This check does not establish command semantics, package installation, Hermes toolset support, execution readiness, or permissions. The original bounded skill text, including its frontmatter, remains part of the inactive candidate.
- Static preflight against the current candidate reader found 158 packages under the 16 KiB skill-text and linked-text resource limits, 23 over the skill-text limit, and five under-limit skills with unresolved local destinations. Those destinations include generated-output placeholders and package references that are not present. This preflight does not mean the source tree is directly discoverable: Hermes skill folders are not Agent Plugins packages and do not contain the required `plugin.json` wrapper.
- Supporting scripts and non-text resources remain inert. X.A.A. does not run imported scripts, and its linked-resource reader accepts bounded UTF-8 text only. The current-machine declaration check is advisory and grants no permissions; dependency installation, toolset support and full execution readiness remain unresolved.

The package metadata regression tests pass with nested Hermes tags, related-skill lists, category fallback, and conflict refusal. This closes one frontmatter compatibility mismatch. It does not establish full Hermes skill parity, automatic installation, dependency setup, toolset/permission mapping, broad package acceptance, or safe script execution. External catalog skills and the broader package/hub lifecycle remain separate audits.

from pathlib import Path
from runpy import run_path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_release_notice_coverage.py"
read_notice_references = run_path(str(_SCRIPT))["read_notice_references"]
count_unknown_additional_notice_placeholders = run_path(str(_SCRIPT))[
    "count_unknown_additional_notice_placeholders"
]
project_license_inventory_errors = run_path(str(_SCRIPT))["project_license_inventory_errors"]
inventory_metadata_errors = run_path(str(_SCRIPT))["inventory_metadata_errors"]
inventory_set_mismatches = run_path(str(_SCRIPT))["inventory_set_mismatches"]


def project_fixture(root: Path, *, cargo_license: str = "MIT") -> Path:
    (root / "apps/desktop/src-tauri").mkdir(parents=True)
    (root / "docs/releases").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname="xaa-engine"\nversion="0.1.1"\nlicense="MIT"\n',
        encoding="utf-8",
    )
    (root / "apps/desktop/src-tauri/Cargo.toml").write_text(
        f'[package]\nname="xaa-desktop"\nversion="0.1.1"\nlicense="{cargo_license}"\n',
        encoding="utf-8",
    )
    (root / "package.json").write_text(
        '{"name":"xaa-workspace","version":"0.1.1","license":"MIT"}',
        encoding="utf-8",
    )
    (root / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    (root / "docs/releases/dependency-inventory.md").write_text(
        "| Python | xaa-engine | 0.1.1 | MIT (project metadata) | — |\n"
        "| npm | xaa-workspace | 0.1.1 | MIT | — |\n"
        "| Rust/Cargo | xaa-desktop | 0.1.1 | MIT | — |\n",
        encoding="utf-8",
    )
    return root


def test_project_license_inventory_matches_first_party_metadata(workspace_tmp_path: Path):
    root = project_fixture(workspace_tmp_path / "project-license")

    assert project_license_inventory_errors(root) == []


def test_project_license_inventory_reports_manifest_inventory_mismatch(workspace_tmp_path: Path):
    root = project_fixture(
        workspace_tmp_path / "project-license-mismatch", cargo_license="Apache-2.0"
    )

    assert project_license_inventory_errors(root) == [
        "Rust/Cargo: xaa-desktop@0.1.1 declares Apache-2.0, but inventory records MIT."
    ]


def test_inventory_metadata_rejects_unknown_license_expression(workspace_tmp_path: Path):
    inventory = workspace_tmp_path / "dependency-inventory.md"
    inventory.write_text(
        "| Python | conditional-package | 1.2.3 | UNKNOWN | https://pypi.org/project/conditional-package/ |\n",
        encoding="utf-8",
    )

    assert inventory_metadata_errors(inventory) == [
        "Python: conditional-package@1.2.3 has no declared license expression (line 1)."
    ]


def test_inventory_metadata_rejects_missing_source_reference(workspace_tmp_path: Path):
    inventory = workspace_tmp_path / "dependency-inventory.md"
    inventory.write_text("| Python | conditional-package | 1.2.3 | MIT | — |\n", encoding="utf-8")

    assert inventory_metadata_errors(inventory) == [
        "Python: conditional-package@1.2.3 has no source reference (line 1)."
    ]


def test_inventory_set_mismatches_reports_missing_and_stale_rows():
    expected = {("current-package", "1.0"), ("another-package", "2.0")}
    inventory = {
        "Python": {("current-package", "1.0"), ("removed-package", "0.9")},
        "npm": set(),
        "Rust/Cargo": set(),
    }

    missing, stale = inventory_set_mismatches("Python", expected, inventory)

    assert missing == {("another-package", "2.0")}
    assert stale == {("removed-package", "0.9")}


def test_notice_coverage_reads_only_explicit_package_attributions(workspace_tmp_path: Path):
    notice = workspace_tmp_path / "notices.md"
    notice.write_text(
        """License text mentions a decoy@9.9 package that is not attributed here.

**Packages:** `python-lib@1.2.3`, `@scope/npm-lib@2.3.4`, `rust_lib@3.4.5`

## Conditional source archive
**Verified source files:**
- `setuptools@84.2.1` — `setuptools-84.2.1/LICENSE` (archive SHA-256 `abc`)
""",
        encoding="utf-8",
    )

    result = read_notice_references(notice)

    common = {("python-lib", "1.2.3"), ("@scope/npm-lib", "2.3.4"), ("setuptools", "84.2.1")}
    assert result["Python"] == common | {("rust-lib", "3.4.5")}
    assert result["npm"] == common | {("rust_lib", "3.4.5")}
    assert result["Rust/Cargo"] == common | {("rust-lib", "3.4.5")}


def test_notice_coverage_keeps_alternate_license_notes_out_of_version(workspace_tmp_path: Path):
    notice = workspace_tmp_path / "alternate-notices.md"
    notice.write_text(
        "**Packages:** `multi-license@4.5.6` (Apache-2.0 alternative; MIT text follows)\n",
        encoding="utf-8",
    )

    result = read_notice_references(notice)

    assert result["Python"] == {("multi-license", "4.5.6")}


def test_notice_coverage_detects_only_scanner_unknown_placeholders(workspace_tmp_path: Path):
    notice = workspace_tmp_path / "notices.md"
    notice.write_text(
        """An unrelated license contains UNKNOWN text.

```text
Additional notice:
UNKNOWN
```

```text
Additional notice:
Apache-2.0
```
""",
        encoding="utf-8",
    )

    assert count_unknown_additional_notice_placeholders(notice) == 1

from pathlib import Path
from runpy import run_path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "verify_release_notice_coverage.py"
read_notice_references = run_path(str(_SCRIPT))["read_notice_references"]
count_unknown_additional_notice_placeholders = run_path(str(_SCRIPT))[
    "count_unknown_additional_notice_placeholders"
]


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

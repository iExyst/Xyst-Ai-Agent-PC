"""Inventory all pinned Hermes plugin-catalog declarations without importing plugins."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, cast

import yaml
from audit_execution_inventory import PIN, ROOT, check_reference, digest

OUTPUT = Path("docs/parity/catalog-inventory.json")
REVIEWED_SOURCE_CLASSIFICATIONS = {
    "claude-subscription-directsdk": (
        "model-provider; pinned source review at commit "
        "83f0a896b3518c7131181838e340c9a80c79524d; see model-provider-source-audit.md"
    ),
    "groq-provider": (
        "model-provider; pinned source review at commit "
        "b5be0388c2b4cae8ed6a30c7371aaa1330731ee8; "
        "see model-provider-source-audit.md#groq-provider"
    ),
    "github-copilot": (
        "model-provider; pinned source review at commit "
        "346cfe972f72b6a966e84c1004cf8742554b193d; "
        "see model-provider-source-audit.md#github-copilot"
    ),
}


def inventory() -> dict[str, Any]:
    check_reference()
    candidates: list[dict[str, Any]] = []
    manifests = sorted((ROOT / "plugin-catalog").rglob("*.yaml"))
    for path in manifests:
        relative = path.relative_to(ROOT).as_posix()
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"Catalog manifest is not a mapping: {relative}")
        manifest = cast(dict[str, Any], data)
        capabilities = manifest.get("capabilities")
        capabilities = cast(dict[str, Any], capabilities) if isinstance(capabilities, dict) else {}
        category = manifest.get("category")
        candidates.append(
            {
                "id": path.stem,
                "manifest": relative,
                "sha256": digest(path),
                "name": manifest.get("name"),
                "category": category if isinstance(category, str) else "unspecified",
                "repository": manifest.get("repo"),
                "reference_commit": manifest.get("sha"),
                "subdirectory": manifest.get("subdir") or "",
                "version": str(manifest.get("version", "")),
                "required_environment_names": capabilities.get("requires_env", []),
                "discovery_classification": (
                    "platform category; see channel-inventory.json"
                    if category == "platform"
                    else REVIEWED_SOURCE_CLASSIFICATIONS.get(
                        path.stem,
                        "non-platform catalog candidate; source classification pending",
                    )
                ),
                "status": "planned",
                "phase": 9,
            }
        )
    categories = Counter(item["category"] for item in candidates)
    return {
        "reference_commit": PIN,
        "audit_date": "2026-11-14",
        "manifest_count": len(candidates),
        "category_counts": dict(sorted(categories.items())),
        "catalog_manifests": candidates,
        "limitations": [
            "Catalog category and manifest metadata are discovery declarations, not verified runtime behavior.",
            "The platform category has a pinned source classification sweep; selected non-platform candidates have individual source reviews, while the rest remain pending.",
            "Repository commits, subdirectories, versions and environment names are copied from catalog metadata; each remains subject to source and runtime review.",
            "Catalogs outside plugin-catalog and capabilities not represented by catalog manifests are outside this inventory.",
            "No external plugin was imported, installed or executed; source contact was limited to pinned read-only review.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = inventory()
    encoded = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != encoded:
            raise SystemExit(
                "Catalog inventory drifted; inspect pinned declarations before regenerating."
            )
    else:
        OUTPUT.write_text(encoded, encoding="utf-8")
    print(
        f"Catalog audit: {data['manifest_count']} manifests; categories: {data['category_counts']}."
    )


if __name__ == "__main__":
    main()

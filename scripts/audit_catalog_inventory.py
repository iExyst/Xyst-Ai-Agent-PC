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
                    else "non-platform catalog candidate; source classification pending"
                ),
                "status": "planned",
                "phase": 9,
            }
        )
    categories = Counter(item["category"] for item in candidates)
    return {
        "reference_commit": PIN,
        "audit_date": "2026-11-09",
        "manifest_count": len(candidates),
        "category_counts": dict(sorted(categories.items())),
        "catalog_manifests": candidates,
        "limitations": [
            "Catalog category and manifest metadata are discovery declarations, not verified runtime behavior.",
            "Only the platform category has the separate pinned source classification sweep recorded in channel-inventory.json.",
            "Repository commits, subdirectories, versions and environment names are copied from catalog metadata; each remains subject to source and runtime review.",
            "Catalogs outside plugin-catalog and capabilities not represented by catalog manifests are outside this inventory.",
            "No external plugin was imported, installed, executed or contacted.",
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

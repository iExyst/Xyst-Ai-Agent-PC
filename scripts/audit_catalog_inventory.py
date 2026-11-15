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
    "abstract-provider": (
        "model-provider; pinned source review at commit "
        "e322e42578586af1c376b7e836dfce437308682a; "
        "see model-provider-source-audit.md#abstract-provider"
    ),
    "antigravity-agy": (
        "model-provider via external agy CLI; pinned source review at commit "
        "f85003d3ce0de3844e2d5c23174d630ff3586e5d; "
        "see model-provider-source-audit.md#antigravity-agy"
    ),
    "antigravity-oauth": (
        "model-provider via external agy CLI; pinned source review at commit "
        "466fafb9b6e8099735d79ed32f97005a477ba144; "
        "see model-provider-source-audit.md#antigravity-oauth"
    ),
    "antigravity-subscription-directsdk": (
        "model-provider via external agy CLI; pinned source review at commit "
        "0d5a3160f69f0c3302e3f49d9d2820093a544116; "
        "see model-provider-source-audit.md#antigravity-subscription-directsdk"
    ),
    "acedatacloud": (
        "model-provider; pinned source review at commit "
        "568763ee21fe081562bb9bcd91e7323f8f81d6ff; "
        "see model-provider-source-audit.md#ace-data-cloud-provider"
    ),
    "aihubmix": (
        "model-provider; pinned source review at commit "
        "b55e916362ebd0528c5d8981d12f3b8f7268b847; "
        "see model-provider-source-audit.md#aihubmix-provider"
    ),
    "aistupidlevel": (
        "model-provider; pinned source review at commit "
        "ba908770f2e97b0a85cc1f5a83240cbe94c8dfa9; "
        "see model-provider-source-audit.md#ai-stupid-level-provider"
    ),
    "anyapi": (
        "model-provider; pinned source review at commit "
        "e5b7b985b5aa56bc585ef1924858978696eb0c3a; "
        "see model-provider-source-audit.md#anyapi-provider"
    ),
    "anyrouter": (
        "model-provider; pinned source review at commit "
        "86bc17dd61ade9baa6f2fb7d440979b55359a907; "
        "see model-provider-source-audit.md#anyrouter"
    ),
    "adaptive-inference-routing": (
        "model-category middleware; pinned source review at commit "
        "d6f4e201ba28602189872746922e527c58ed0cbd; "
        "see model-provider-source-audit.md#adaptive-inference-routing"
    ),
    "aixy": (
        "standalone inference/MCP setup CLI; pinned source review at commit "
        "38422435bb065bd6649f9d1288506b57790ab0eb; "
        "see model-provider-source-audit.md#aixy-setup"
    ),
    "cloudflare-workers-ai-provider": (
        "model-provider; pinned source review at commit "
        "f6821d058d504ee6324b0d3217564b93124c09f9; "
        "see model-provider-source-audit.md#cloudflare-workers-ai"
    ),
    "chutes-provider": (
        "model-provider; pinned source review at commit "
        "d1165cdd5c65a1179c559e9169a428adb9a36b42; "
        "see model-provider-source-audit.md#chutes"
    ),
    "coreweave": (
        "model-provider; pinned source review at commit "
        "799027698ca0171c8912df076649a67a25266c20; "
        "see model-provider-source-audit.md#coreweave"
    ),
    "ngrok-ai-gateway": (
        "model-provider; pinned source review at commit "
        "27baaa0e85e09581f05084fc318a0755774162e1; "
        "see model-provider-source-audit.md#ngrok-ai-gateway"
    ),
    "together": (
        "model-provider; pinned source review at commit "
        "d5267d5d1e1bf98097b13546a739bd8dab8db7ec; "
        "see model-provider-source-audit.md#together"
    ),
    "telnyx-provider": (
        "model-provider; pinned source review at commit "
        "f53c20e80f84b4a880a7f2b4f1ed2f8b3fa1d11c; "
        "see model-provider-source-audit.md#telnyx"
    ),
    "runcrate-provider": (
        "model-provider; pinned source review at commit "
        "c0023f37820e05147f1dcd1cb8ebd712e6b122e2; "
        "see model-provider-source-audit.md#runcrate"
    ),
    "ruapi": (
        "dual-protocol model-provider plugin; pinned source review at commit "
        "fbefc615d447729ccf85aec05e0ea27ecc6f3bd9; "
        "see model-provider-source-audit.md#ruapi"
    ),
    "safeailabs-provider": (
        "model-provider; pinned source review at commit "
        "3fa82ed3b8fc2aa52e96afeabdfbe19fa6a043fc; "
        "see model-provider-source-audit.md#safe-ai-labs"
    ),
    "fortytwo-provider": (
        "model-provider; pinned source review at commit "
        "91c8f521ab357530c91a9660bd8f2db37571c1c8; "
        "see model-provider-source-audit.md#fortytwo"
    ),
    "requesty": (
        "model-provider; pinned source review at commit "
        "aa4e37d7f09dfc1e98b915d740d09e8b1b44c98d; "
        "see model-provider-source-audit.md#requesty"
    ),
    "bharatcode": (
        "model-provider; pinned source review at commit "
        "f4cc1d1f2977963173b23b9ac6a19c3ca8851050; "
        "see model-provider-source-audit.md#bharatcode"
    ),
    "bharatcode-provider": (
        "model-provider; duplicate pinned BharatCode source review at commit "
        "f4cc1d1f2977963173b23b9ac6a19c3ca8851050; "
        "see model-provider-source-audit.md#bharatcode"
    ),
    "hashference-provider": (
        "model-provider; pinned source review at commit "
        "82700f5377a46f0e7bd482c907ed04090b6305be; "
        "see model-provider-source-audit.md#hashference"
    ),
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
    "fusion": (
        "automation; pinned plugin.yaml and README review at commit "
        "edab6a6d17838d4d9a1525a2a7d3500c62694e17; "
        "multi-harness model-fusion planner and executor"
    ),
    "hermes-memory-wiki": (
        "memory; pinned plugin.yaml and README review at commit "
        "54afaf5014db23346148436c4883eb64095e8060; "
        "read-only session-history and persistent-memory dashboard"
    ),
}


def inventory() -> dict[str, Any]:
    check_reference()
    candidates: list[dict[str, Any]] = []
    manifests = sorted((ROOT / "plugin-catalog").rglob("*.yaml"))
    catalog_controls: list[dict[str, Any]] = []
    for path in manifests:
        relative = path.relative_to(ROOT).as_posix()
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise TypeError(f"Catalog manifest is not a mapping: {relative}")
        manifest = cast(dict[str, Any], data)
        # This file is a catalog blocklist, not a plugin manifest. Keep its
        # removed entries in the audit output without inflating candidate counts.
        if relative == "plugin-catalog/removed.yaml":
            removed = manifest.get("removed", [])
            if not isinstance(removed, list):
                raise TypeError(f"Catalog removal list is not a list: {relative}")
            catalog_controls.append(
                {
                    "manifest": relative,
                    "sha256": digest(path),
                    "removed_entries": removed,
                }
            )
            continue
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
        "audit_date": "2026-11-15",
        "catalog_yaml_count": len(manifests),
        "manifest_count": len(candidates),
        "category_counts": dict(sorted(categories.items())),
        "catalog_manifests": candidates,
        "catalog_controls": catalog_controls,
        "limitations": [
            "Catalog category and manifest metadata are discovery declarations, not verified runtime behavior.",
            "The platform category has a pinned source classification sweep; twenty-five selected non-platform candidates have individual source reviews, while the rest remain pending.",
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
        f"Catalog audit: {data['manifest_count']} plugin manifests across "
        f"{data['catalog_yaml_count']} YAML files; "
        f"{len(data['catalog_controls'])} catalog controls; "
        f"categories: {data['category_counts']}."
    )


if __name__ == "__main__":
    main()

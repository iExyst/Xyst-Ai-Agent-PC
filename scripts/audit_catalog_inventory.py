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
    "clinepass": (
        "model-provider; pinned source review at commit "
        "3853e895482d3171ff3a8144344b74cfa3a7dd67; "
        "see model-provider-source-audit.md#clinepass-provider"
    ),
    "cliproxyapi-provider": (
        "multi-instance provider bridge for a local or remote CLIProxyAPI gateway; pinned source review "
        "at commit 42240b168488c820883b0d989456acf0a8a06ba8; "
        "see model-provider-source-audit.md#cliproxyapi-provider"
    ),
    "codex-truly": (
        "Codex subscription context-window override, quota-consuming live probe, and image-generation controller; "
        "pinned source review at commit 1913d2a0a4b2dd3e4283af5c78241de4d3a2995e; "
        "see model-provider-source-audit.md#codex-truly"
    ),
    "commandcode-oauth": (
        "CLI-grant/browser-handoff model provider with account usage and custom Alpha transport; pinned source review "
        "at commit e51eb61613604aefbcf3203c8fcce531706232ee; "
        "see model-provider-source-audit.md#commandcode-oauth"
    ),
    "cursor-acp": (
        "external-process Cursor subscription model provider via agent ACP; pinned source review "
        "at commit d5b6146c4e6dcfca110fbcf736d8b51823bc8e64; "
        "see model-provider-source-audit.md#cursor-acp"
    ),
    "cursor-provider": (
        "Cursor subscription provider via the official proprietary SDK; pinned source review "
        "at commit bf7d61fb1e37fa78ec58de7683ec436dff5b71bf; "
        "see model-provider-source-audit.md#cursor-provider"
    ),
    "cursor-sdk": (
        "experimental Cursor Agent SDK provider; pinned source review "
        "at commit 0e546bc079ab23451a52a12ca02e8a55af34e217; "
        "see model-provider-source-audit.md#cursor-sdk"
    ),
    "hermes-jev-routing": (
        "per-turn external model-routing middleware; pinned source review "
        "at commit 06a53646587c33174a0fbfbb3713eca90bbb8586; "
        "see model-provider-source-audit.md#hermes-jev-routing"
    ),
    "hermes-kame-api-rotation": (
        "API-key rotation and rate-limit recovery plugin; pinned source review "
        "at commit e8c01131c3ef771173fc40b54f6608b36f2afc42; "
        "see model-provider-source-audit.md#hermes-kame-api-rotation"
    ),
    "hermes-kame-provider": (
        "companion model-provider registration for KAME API rotation; pinned source review "
        "at commit 5e4a12f249f679ae5dcbf0d22e4ffbfeba4e5e2b; "
        "see model-provider-source-audit.md#hermes-kame-provider"
    ),
    "hermes-model-switcher": (
        "Dashboard model-route configuration manager, not an inference provider; pinned source review "
        "at commit d62530d906307154e16ed98bb9b4be10ea6fef56; "
        "see model-provider-source-audit.md#hermes-model-switcher"
    ),
    "hermes-muse-code": (
        "Meta Muse Code subscription model provider using a Meta device-code login and cached account key; "
        "pinned source review at commit e33501185e328b5597fd98c6de916495922ec727; "
        "see model-provider-source-audit.md#hermes-muse-code"
    ),
    "hermes-opper": (
        "OpenAI-compatible Opper gateway provider profile with live catalog and auxiliary model defaults; "
        "pinned source review at commit 2e15f465b4c2a107fd0230611ebc1e5a7586a196; "
        "see model-provider-source-audit.md#hermes-opper"
    ),
    "hermes-session-warmer": (
        "opt-in bounded idle-prompt-warming LLM middleware, not a provider; pinned source review "
        "at commit eba7be111c44e4ac6b702cdcb341c8d71da1c57e; "
        "see model-provider-source-audit.md#hermes-session-warmer"
    ),
    "hermes-structured-aux-models": (
        "structured Jev/OpenRouter auxiliary decision provider for approval, MCP sampling, and compression; "
        "pinned source review at commit e5c49b081ad04adca2ee3edc601b09ede6cee4c5; "
        "see model-provider-source-audit.md#hermes-structured-aux-models"
    ),
    "hopper": (
        "OpenRouter provider profile with a user-managed local model-ID list; pinned source review "
        "at commit 68427cf7770b393b6b4b694722d24431058fbd3f; "
        "see model-provider-source-audit.md#hopper"
    ),
    "jev-approvals": (
        "smart-approval-only Jev decision provider with a deterministic policy layer; pinned source review "
        "at commit 28be98a19539b29768398fb400f4c62f41337c26; "
        "see model-provider-source-audit.md#jev-approvals"
    ),
    "jev-effort-router": (
        "Ollama:Cloud-only model/effort routing middleware; pinned source review "
        "at commit 6fd9f2ec1c53cb99b5e6c7043c25bbd37938d4a5; "
        "see model-provider-source-audit.md#jev-effort-router"
    ),
    "jev-model-router": (
        "model-selection service with MCP stdio and HTTP surfaces; pinned source review "
        "at commit b977dc3ef07738aab005c433d08c1da3fa949e8d; "
        "see model-provider-source-audit.md#jev-model-router-mcp-and-http-service"
    ),
    "kfchow-llm-value-router": (
        "per-turn model-routing middleware with external classifier; pinned source review "
        "at commit 39a6944280c2156f816df8641d3d01d743aec564; "
        "see model-provider-source-audit.md#kfchow-llm-value-router-middleware"
    ),
    "cloudgpu-provider": (
        "model-provider; pinned source review at commit "
        "8094603d776e185cfe362618d353841e6b453189; "
        "see model-provider-source-audit.md#cloudgpu-provider"
    ),
    "empiriolabs": (
        "model-provider; pinned source review at commit "
        "20f049d5913ec850e98bef3d289f6cb43645869e; "
        "see model-provider-source-audit.md#empiriolabs-provider"
    ),
    "gondola-provider": (
        "model-provider; pinned source review at commit "
        "ad3f352b6a9814f1b550c819bee5ef2fb92a1851; "
        "see model-provider-source-audit.md#gondola-provider"
    ),
    "hermes-adaptive-effort": (
        "standalone plugin; pinned source review of opt-in reasoning-effort middleware "
        "at commit c84f1389670cdd5ec4607b13bd32c4809878c415; "
        "see model-provider-source-audit.md#hermes-adaptive-effort"
    ),
    "hermes-council": (
        "standalone multi-model deliberation plugin and virtual provider; pinned source review "
        "at commit 18a66633acc045c251063721a1cf26f9a46d29de; "
        "see model-provider-source-audit.md#hermes-council"
    ),
    "hermes-impossibl": (
        "OpenAI-compatible multi-model gateway provider; pinned source review "
        "at commit 146805e4eabab33687fa2e34bbe959f0057f9497; "
        "see model-provider-source-audit.md#hermes-impossibl"
    ),
    "hermes-experientiallabs": (
        "OpenAI-compatible multi-model provider with account-usage hook; pinned source review "
        "at commit 0d26c3eac2965105a507f6ed90e046bf6f981056; "
        "see model-provider-source-audit.md#experiential-labs-provider"
    ),
    "grok-acp": (
        "external-process model provider; pinned source review at commit "
        "2851d6667be7f1a9044fee5006a382699d942c6f; "
        "see model-provider-source-audit.md#grok-acp"
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
            "The platform category has a pinned source classification sweep; thirty-eight selected non-platform candidates have individual source reviews, while the rest remain pending.",
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

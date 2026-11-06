"""Inventory pinned platform registrations and source declarations without importing adapters."""

import argparse
import ast
import json
import subprocess
from pathlib import Path
from typing import Any

import yaml
from audit_execution_inventory import PIN, ROOT, check_reference, digest

OUTPUT = Path("docs/parity/channel-inventory.json")
HOMEASSISTANT_SOURCE_AUDIT = {
    "repository": "NousResearch/hermes-official-plugins",
    "reference_commit": "4d492b1dc5326af3e38bb2cf3e4b9ad6d65f0072",
    "paths": [
        "homeassistant/__init__.py",
        "homeassistant/adapter.py",
        "homeassistant/tools.py",
        "homeassistant/README.md",
    ],
    "evidence_level": "pinned source reviewed; external plugin tests and live Home Assistant runtime not independently verified",
}
EXTERNAL_CHANNEL_SOURCE_AUDITS = {
    "agentchat": ("AgentChat peer-to-peer WebSocket messaging; inbound events wake the agent and outbound sends are explicit tool calls.", ["plugin.yaml", "agentchatme_hermes/__init__.py"]),
    "aichipmunk": ("Hermes Desktop pairing connector for the AI Chipmunk mobile app; reviewed plugin manifest and pairing implementation.", ["plugins/aichipmunk/plugin.yaml", "plugins/aichipmunk/pairing.py"]),
    "ando-platform": ("Persistent gateway adapter receiving Ando messages and replying through the invited agent connection.", ["plugin/plugin.yaml", "plugin/adapter.py"]),
    "artil": ("Email, phone, and workspace account connector; catalog source describes direct customer/team messaging.", ["plugins/hermes/plugin.yaml", "plugins/hermes/README.md"]),
    "bridge-adapter": ("Generic filesystem bridge: polls inbox JSON and writes outbound JSON through registered bridge wrappers.", ["plugin.yaml", "adapter.py"]),
    "bunny_space-platform": ("Bunny Space gateway adapter joining private rooms, receiving messages, and replying over outbound pull transport.", ["plugin.yaml", "adapter.py"]),
    "call-me": ("Phone messaging gateway with verified-device allowlisting, inbound texts/voice answers, and agent replies/calls.", ["plugin.yaml", "adapter.py"]),
    "carbonvoice-platform": ("Carbon Voice gateway adapter using Socket.IO with REST polling fallback and outbound replies.", ["plugin.yaml", "adapter.py"]),
    "chatto-platform": ("Chatto gateway adapter with realtime WebSocket inbound events and ConnectRPC outbound messages.", ["plugin.yaml", "adapter.py"]),
    "fluxer-platform": ("Fluxer gateway adapter using REST outbound and Gateway WebSocket inbound events.", ["plugin.yaml", "adapter.py"]),
}
# Transport observations and channel-specific acceptance, not runtime verification.
REQUIREMENTS = {
    "a2a": (
        "HTTP JSON-RPC and SSE; stdlib server",
        "Agent Card discovery, authenticated task submission, streamed result/cancel and restart deduplication; outbound peers need separate authorization.",
    ),
    "bluebubbles": (
        "macOS BlueBubbles REST and inbound webhook",
        "Authorize the server/callback, retain iMessage chat identity and attachment boundaries, and prove exactly one reply after callback replay.",
    ),
    "buzz": (
        "Nostr relay WebSocket with CLI polling/send path",
        "Authenticate the relay identity, preserve channel/thread routing, bound and supervise CLI calls, and deduplicate WebSocket/poll overlap.",
    ),
    "dingtalk": (
        "DingTalk Stream SDK; session webhook/AI-card replies",
        "Validate application identity, session reply lifetime and reconnect behavior; retain group/user scope and reject forged callbacks.",
    ),
    "discord": (
        "discord.py gateway; guild/DM/thread routing",
        "Verify bot permissions, allowed users/roles, guild/DM/thread separation and reconnect deduplication; voice/media need separate evidence.",
    ),
    "email": (
        "Polled IMAP receive and SMTP send",
        "Verify TLS/authentication, mailbox UID/message-ID deduplication, sender authorization, thread headers and automated-mail loop prevention.",
    ),
    "feishu": (
        "Feishu/Lark WebSocket or HTTP webhook",
        "Test each configured transport, authenticated callbacks, DM/group mention gates, user allowlists, media and thread/session isolation.",
    ),
    "google_chat": (
        "Authenticated HTTP callbacks or Pub/Sub pull; Chat REST outbound",
        "Verify audience/account authentication, space/thread identity, Pub/Sub acknowledgement/replay and one authorized reply.",
    ),
    "irc": (
        "asyncio IRC connection, configurable TLS",
        "Verify server/TLS identity, user authorization beyond spoofable display text, channel/DM scope, line splitting, reconnect and loop suppression.",
    ),
    "line": (
        "Signed LINE webhook; reply token or push send",
        "Reject invalid signatures and duplicate event IDs; handle single-use/expired reply tokens without duplicate or unauthorized billable push.",
    ),
    "matrix": (
        "mautrix homeserver adapter; optional encryption",
        "Verify account/room/device scope, event replay and thread routing; encrypted-room claims require real E2EE key/device tests.",
    ),
    "mattermost": (
        "REST v4 plus WebSocket via aiohttp",
        "Verify server/account/team/channel/thread identities, token scope, replay after reconnect and bounded media delivery.",
    ),
    "ntfy": (
        "HTTP streaming topic subscription and POST publish",
        "Treat the protected topic as the trust boundary: publisher-controlled title is not authenticated identity. Test read-token protection and topic isolation.",
    ),
    "photon": (
        "Supervised Node Spectrum sidecar; NDJSON and gRPC SDK stream",
        "Validate sidecar protocol, startup/crash/cancellation, chat identity and attachment scope; no sidecar command may gain coordinator permissions.",
    ),
    "qqbot": (
        "Official QQ Bot v2 WebSocket receive and REST send",
        "Verify application credentials, channel/group/DM identity, token refresh, event replay, send sequence and media upload authorization.",
    ),
    "raft": (
        "Local wake endpoint plus raft agent bridge process",
        "Authenticate wake hints, fetch actual bounded messages through the bridge, isolate session identity and stop/join the owned bridge on disconnect.",
    ),
    "signal": (
        "signal-cli HTTP daemon SSE receive and JSON-RPC send",
        "Verify configured account and daemon boundary, source/group identity, SSE reconnect, attachment scoping and no duplicate reply.",
    ),
    "simplex": (
        "Persistent WebSocket to simplex-chat daemon",
        "Verify daemon ownership, contact/group identifiers, reconnect event replay, command/result correlation and scoped media transfer.",
    ),
    "slack": (
        "slack-bolt Socket Mode; messages/slash commands/threads",
        "Verify bot/app-token scopes, workspace/channel/thread/user isolation, event retries and slash acknowledgements; no cross-workspace memory leak.",
    ),
    "sms": (
        "Twilio REST send and inbound HTTP webhook",
        "Verify webhook signature and configured number/account, E.164 sender policy, duplicate message SID handling and authorized SMS spending.",
    ),
    "teams": (
        "Teams SDK auth/activity processing and HTTP webhook",
        "Verify tenant/application/activity identity, conversation references, replay and proactive-send authorization; never route across tenants by display name.",
    ),
    "telegram": (
        "python-telegram-bot inbound updates and outbound replies",
        "Verify bot identity, sender allowlist, DM/group/topic separation, update replay after restart and one authorized text reply; test media independently.",
    ),
    "wecom": (
        "Smart Robot WebSocket with streaming/media mixins",
        "Verify robot credentials, user/group authorization, reconnect deduplication and scoped native streaming/send behavior.",
    ),
    "wecom_callback": (
        "Encrypted HTTP/XML callback; proactive message/send replies",
        "Verify callback crypto and receiver identity, reject malformed XML, isolate corp_id:user_id, acknowledge promptly and deliver one authorized reply.",
    ),
    "weixin": (
        "Tencent iLink getupdates long poll; encrypted CDN media",
        "Verify account/peer identity and latest peer context_token routing, restart cursor handling and media encryption/size boundaries.",
    ),
    "whatsapp": (
        "Owned Baileys Node bridge with local HTTP polling/send",
        "Verify linked account, bridge ownership, normalized user/group identifiers, replay/loop suppression and process-tree shutdown.",
    ),
    "whatsapp_cloud": (
        "Meta Graph API plus webhook",
        "Verify challenge/signature/account identifiers, duplicate delivery IDs, token scope, approved recipient and outbound policy.",
    ),
    "yuanbao": (
        "WebSocket AUTH_BIND/heartbeat and text/media sender",
        "Verify application/bot identity, authenticated reconnect, inbound/outbound sequence mapping, replay and chat/user isolation.",
    ),
    "api_server": (
        "aiohttp OpenAI-compatible API and session/job routes",
        "Verify bearer authentication, allowed origins, profile/session isolation, streamed runs and cancellation across every claimed compatibility route.",
    ),
    "webhook": (
        "HMAC-authenticated generic/provider HTTP ingress",
        "Verify signatures/timestamps, body limits, route allowlists, duplicate IDs and explicit delivery targets; payload text cannot grant tool permissions.",
    ),
    "msgraph_webhook": (
        "Microsoft Graph change-notification HTTP ingress",
        "Verify subscription validation, client_state, tenant/resource identity, notification replay and authorization before fetching protected content.",
    ),
}
METHODS = {
    "connect",
    "disconnect",
    "send",
    "send_image",
    "send_document",
    "send_voice",
    "send_video",
    "send_draft",
    "edit_message",
}


def tracked(prefix: str) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", "-z", "HEAD", "--", prefix],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return sorted(ROOT / path for path in result.stdout.split("\0") if path)


def classes(path: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.ClassDef) or not any(
            "Adapter" in ast.unparse(base) for base in node.bases
        ):
            continue
        result.append(
            {
                "name": node.name,
                "line": node.lineno,
                "bases": [ast.unparse(base) for base in node.bases],
                "declared_direct_methods": [
                    {"name": method.name, "line": method.lineno}
                    for method in node.body
                    if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and method.name in METHODS
                ],
            }
        )
    return result


def literal_or_expression(node: ast.expr) -> dict[str, Any]:
    try:
        return {"literal": ast.literal_eval(node)}
    except (ValueError, TypeError):
        return {"expression": ast.unparse(node)}


def inventory() -> dict[str, Any]:
    check_reference()
    files = tracked("plugins/platforms")
    manifests = [path for path in files if path.name == "plugin.yaml"]
    baseline = json.loads(Path("docs/parity/pinned-inventory.json").read_text(encoding="utf-8"))
    if baseline["reference_commit"] != PIN or {
        row["manifest"] for row in baseline["platform_manifests"]
    } != {path.relative_to(ROOT).as_posix() for path in manifests}:
        raise ValueError(
            "The earlier manifest inventory needs reconciliation with the pinned source."
        )
    registrations: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}
    for path in files:
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "register_platform"
        ]
        if not calls:
            continue
        relative = path.relative_to(ROOT).as_posix()
        sources[relative] = {"sha256": digest(path), "registrations": len(calls)}
        for call in calls:
            keywords = {
                keyword.arg: keyword.value for keyword in call.keywords if keyword.arg is not None
            }
            if "name" not in keywords or not isinstance(keywords["name"], ast.Constant):
                raise ValueError(
                    f"Dynamic platform registration needs review: {relative}:{call.lineno}"
                )
            name = ast.literal_eval(keywords["name"])
            if name not in REQUIREMENTS:
                raise ValueError(f"Unreviewed channel: {name}")
            registrations.append(
                {
                    "name": name,
                    "manifest": path.parent.relative_to(ROOT).as_posix() + "/plugin.yaml",
                    "source": relative,
                    "line": call.lineno,
                    "factory_expression": ast.unparse(keywords["adapter_factory"]),
                    "declared_registration_settings": {
                        key: literal_or_expression(value)
                        for key, value in keywords.items()
                        if key
                        in {
                            "required_env",
                            "max_message_length",
                            "allowed_users_env",
                            "allow_all_env",
                            "display_overrides",
                            "env_enable_credentials",
                        }
                    },
                }
            )
    names = {row["name"] for row in registrations}
    if len(names) != len(registrations) or names != set(REQUIREMENTS) - {
        "api_server",
        "webhook",
        "msgraph_webhook",
    }:
        raise ValueError("Platform registrations differ from the reviewed channel set.")
    manifest_rows: list[dict[str, Any]] = []
    for path in manifests:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        relative = path.relative_to(ROOT).as_posix()
        adapter_sources = [
            source
            for source in files
            if source.parent == path.parent and source.suffix == ".py" and classes(source)
        ]
        manifest_rows.append(
            {
                "manifest": relative,
                "sha256": digest(path),
                "name": data["name"],
                "label": data.get("label"),
                "version": str(data["version"]),
                "declared_required_environment": [
                    {"name": item["name"], "secret": bool(item.get("password"))}
                    for item in data.get("requires_env", [])
                ],
                "declared_optional_environment": [
                    {"name": item["name"], "secret": bool(item.get("password"))}
                    for item in data.get("optional_env", [])
                ],
                "registered_names": [
                    row["name"] for row in registrations if row["manifest"] == relative
                ],
                "adapter_declarations": [
                    {
                        "source": source.relative_to(ROOT).as_posix(),
                        "sha256": digest(source),
                        "classes": classes(source),
                    }
                    for source in adapter_sources
                ],
            }
        )
    channels: list[dict[str, Any]] = []
    for name, (transport, acceptance) in REQUIREMENTS.items():
        core = name in {"api_server", "webhook", "msgraph_webhook"}
        source = ROOT / "gateway/platforms" / f"{name}.py" if core else None
        channels.append(
            {
                "id": name,
                "kind": "core_transport" if core else "registered_platform",
                "phase": 8 if name in {"telegram", "webhook"} else 9,
                "status": "planned",
                "full_parity_phase": 9,
                "phase_scope": "initial authenticated text path"
                if name in {"telegram", "webhook"}
                else "parity expansion",
                "xaa_module": f"engine/xaa/channels/{name} (not implemented)",
                "transport_observation": transport,
                "acceptance_id": f"CHANNEL-{name.upper()}",
                "acceptance": acceptance,
                "common_requirements": "CHANNEL-COMMON-1 through CHANNEL-COMMON-8",
                **(
                    {
                        "source": source.relative_to(ROOT).as_posix(),
                        "sha256": digest(source),
                        "adapter_declarations": classes(source),
                    }
                    if source is not None
                    else {}
                ),
            }
        )
    supporting = [
        "gateway/config.py",
        "gateway/platforms/base.py",
        "gateway/platforms/event.py",
        "gateway/session.py",
        "gateway/platform_registry.py",
    ]
    external: list[dict[str, Any]] = []
    for path in tracked("plugin-catalog"):
        if path.suffix != ".yaml":
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if data.get("category") != "platform":
            continue
        external.append(
            {
                "id": path.stem,
                "manifest": path.relative_to(ROOT).as_posix(),
                "sha256": digest(path),
                "repository": data.get("repo"),
                "reference_commit": data.get("sha"),
                "subdirectory": data.get("subdir") or "",
                "version": str(data.get("version", "")),
                "required_environment_names": data.get("capabilities", {}).get("requires_env", []),
                "classification": "catalog-declared gateway and toolset"
                if path.stem == "homeassistant"
                else "not a messaging adapter (terminal backend)"
                if path.stem == "deepinfra-sandbox"
                else "not a messaging adapter (presence hook)"
                if path.stem == "discord-tps"
                else "not a messaging adapter (deployment MCP/skills)"
                if path.stem == "render-platform"
                else "source-reviewed messaging or gateway adapter"
                if path.stem in EXTERNAL_CHANNEL_SOURCE_AUDITS
                else "unclassified platform-category discovery candidate",
                "external_source_inspected": path.stem == "homeassistant"
                or path.stem in EXTERNAL_CHANNEL_SOURCE_AUDITS,
                "external_source_evidence": (
                    HOMEASSISTANT_SOURCE_AUDIT
                    if path.stem == "homeassistant"
                    else {
                        "repository": data.get("repo"),
                        "reference_commit": data.get("sha"),
                        "subdirectory": data.get("subdir") or "",
                        "paths": EXTERNAL_CHANNEL_SOURCE_AUDITS[path.stem][1],
                        "finding": EXTERNAL_CHANNEL_SOURCE_AUDITS[path.stem][0],
                        "evidence_level": "pinned source reviewed statically; plugin/runtime behavior not independently verified",
                    }
                    if path.stem in EXTERNAL_CHANNEL_SOURCE_AUDITS
                    else None
                ),
                "status": "planned",
                "phase": 9,
            }
        )
    homeassistant = next(row for row in external if row["id"] == "homeassistant")
    homeassistant["acceptance"] = (
        "Source review complete. Verify configured service identity, authorized entity/state-change ingress, durable event deduplication and exactly-once notification delivery; require explicit per-run write grants and entity allowlists for service actions. Runtime/protocol acceptance remains open."
    )
    return {
        "reference_commit": PIN,
        "audit_date": "2026-11-03",
        "manifest_count": len(manifests),
        "registered_platform_count": len(registrations),
        "core_transport_count": 3,
        "manifests": manifest_rows,
        "registrations": registrations,
        "channels": channels,
        "registration_sources": sources,
        "supporting_sources": [
            {"path": path, "sha256": digest(ROOT / path)} for path in supporting
        ],
        "external_platform_category_candidates": external,
        "limitations": [
            "Method declarations and mixin names do not prove supported behavior; inherited methods may be stubs or fallbacks.",
            "Transport observations come from source declarations and selected paths; adapters have not been executed.",
            "Home Assistant is an external catalog-declared gateway/toolset, not an in-tree registration; pinned source was reviewed but plugin tests and live runtime were not executed.",
            "Catalog category platform includes non-channel features and can omit other categories that register channels; candidates are not an exhaustive channel count.",
            "No X.A.A. messaging adapter or authorized external delivery is claimed implemented.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    data = inventory()
    encoded = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
    if args.check:
        if OUTPUT.read_text(encoding="utf-8") != encoded:
            raise SystemExit(
                "Channel inventory drifted; inspect pinned declarations before regenerating."
            )
    else:
        OUTPUT.write_text(encoded, encoding="utf-8")
    print(
        f"Channel audit: {data['manifest_count']} manifests, {data['registered_platform_count']} platform names, {data['core_transport_count']} core transports."
    )


if __name__ == "__main__":
    main()

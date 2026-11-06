"""Inventory pinned backend declarations without importing or executing upstream code."""

import argparse
import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

PIN = "1e208b280deb18f01d015b0acdf3ef3552d08039"
ROOT = Path(".hermes-audit")
OUTPUT = Path("docs/parity/execution-inventory.json")
BUILTINS = {
    "local": (
        "local.py",
        "LocalEnvironment",
        3,
        "Host runtime plus an implemented restricted execution boundary; the current fixed filesystem worker is not that boundary.",
    ),
    "docker": (
        "docker.py",
        "DockerEnvironment",
        3,
        "Docker executable, live daemon, explicitly selected image and verified host mount/resource policy.",
    ),
    "ssh": (
        "ssh.py",
        "SSHEnvironment",
        9,
        "SSH client, explicit host/user/host-key policy, OS-vault authentication and a restricted remote workspace.",
    ),
    "modal": (
        "modal.py",
        "ModalEnvironment",
        9,
        "Explicit Modal account/SDK authorization and selected image; remote use must be configured.",
    ),
    "managed_modal": (
        "managed_modal.py",
        "ManagedModalEnvironment",
        9,
        "Configured tool gateway and user token; upstream refuses host credential-file passthrough in this mode.",
    ),
    "daytona": (
        "daytona.py",
        "DaytonaEnvironment",
        9,
        "Daytona SDK/account/API credential and explicit remote image/resource settings.",
    ),
    "vercel_sandbox": (
        "vercel_sandbox.py",
        "VercelSandboxEnvironment",
        9,
        "Vercel SDK plus configured token/project/team, or supported development OIDC authentication; explicit runtime/image.",
    ),
}
EXTERNAL = {
    "bubblewrap": "Linux bwrap/prlimit; profile-specific network and filesystem policy must be verified.",
    "cua-spaces": "Configured Cua SDK/Space and explicit authorization for remote commands/files/screens.",
    "deepinfra-sandbox": "DeepInfra credential and explicit billable sandbox/credential-upload policy.",
    "singularity": "Linux Apptainer/Singularity executable, selected image and overlay/resource policy.",
    "smolmachines": "Supported Apple Silicon macOS or Linux/KVM host and local microVM SDK.",
    "upstash-box": "Upstash Box credential and explicit cloud, persistence, upload and credential-exposure policy.",
    "hermes-wsl-terminal": "Windows WSL distro/user. Manifest explicitly describes full access, not a restricted sandbox.",
    "hermes-wslc-sandbox": "Windows wslc runtime; manifest discloses no capability dropping and no enforced disk quota.",
}
EXTERNAL_SOURCE_AUDITS = {
    "bubblewrap": {
        "repository": "NousResearch/hermes-official-plugins",
        "reference_commit": "a50b0fea59d10f9a51b488206f587d9cb6ca6163",
        "paths": [
            "bubblewrap/README.md",
            "bubblewrap/__init__.py",
            "bubblewrap/binds.py",
            "bubblewrap/sandbox.py",
        ],
        "evidence_level": "pinned source reviewed; upstream plugin runtime not independently verified",
        "acceptance": "Review complete; verify the intended Bubblewrap profile on a supported host and satisfy EXEC-COMMON-1 through EXEC-COMMON-8",
    },
    "hermes-wslc-sandbox": {
        "repository": "Yicheng-Mark/hermes-windows-plugins",
        "reference_commit": "5bad3fb2baf04ce6befecfefce6d14f4c9c98934",
        "paths": [
            "wslc-sandbox/hermes_wslc_sandbox/provider.py",
            "wslc-sandbox/hermes_wslc_sandbox/environment.py",
            "wslc-sandbox/hermes_wslc_sandbox/wslc.py",
            "wslc-sandbox/README.md",
            "wslc-sandbox/VALIDATION.md",
        ],
        "evidence_level": "pinned source reviewed; WSL Containers runtime and isolation not independently verified",
        "acceptance": "Review complete; runtime host probes, effective resource/isolation checks and EXEC-COMMON-1 through EXEC-COMMON-8 remain required",
    },
    "singularity": {
        "repository": "NousResearch/hermes-official-plugins",
        "reference_commit": "d46aced3031a8f533fdbf0806bc4cf0d0b28be07",
        "paths": [
            "singularity/provider.py",
            "singularity/singularity_env.py",
            "singularity/README.md",
        ],
        "evidence_level": "pinned source reviewed; runtime and security properties not independently verified",
        "acceptance": "Review complete; EXEC-COMMON-1 through EXEC-COMMON-8 and live Apptainer isolation/resource/cleanup probes remain required",
    },
}
EXCLUDED = {
    "account-pool": "Provider-account scheduling/usage, not a terminal environment provider.",
    "advanced-delegation": "Model/profile routing for delegation, not terminal execution routing.",
    "alans-way": "Gateway proactivity and desktop workflow integration.",
    "cli-session-tabs": "TUI/tmux session presentation, not a terminal execution environment.",
    "crew": "Task coordination and verification workflow.",
    "hermes-cloud-file-manager": "Desktop file transport into an existing agent environment.",
    "hermes-gemini-live": "Realtime voice and task delegation.",
    "hermes-seo": "Domain tool subprocess runtime, not a registered terminal environment.",
    "hermes-talk": "Realtime voice integration.",
    "jev-judge": "Pre-tool judgment/approval policy integration.",
    "lightkeeper": "Codebase knowledge/vault workflow; matched narrower search only.",
    "meshy": "Media-generation MCP integration.",
    "office-3d": "Desktop profile activity visualization.",
    "omh": "Delegation/memory/evidence workflow with host subprocess probes.",
    "opencode-free-acp": "Inference provider transport; Hermes retains tool execution.",
    "progress-squash": "Terminal output transform hook.",
    "rtk-rewrite-docker": "Command rewrite hook applied to existing backends.",
    "telegram_dashboard_probe": "Gateway status and monitoring integration.",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assignment(path: Path, name: str) -> ast.expr:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
    raise ValueError(f"Missing pinned assignment {name}")


def check_reference() -> None:
    actual = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if actual != PIN:
        raise ValueError("Audit checkout does not match the release reference pin.")
    subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "-c",
            "core.fsmonitor=false",
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--exit-code",
            "HEAD",
            "--",
        ],
        check=True,
        capture_output=True,
    )


def inventory() -> dict[str, Any]:
    check_reference()
    registry = assignment(ROOT / "agent/terminal_env_registry.py", "BUILTIN_BACKEND_NAMES")
    if not isinstance(registry, ast.Call):
        raise TypeError("Registry declaration changed shape.")
    reserved = set(ast.literal_eval(registry.args[0]))
    factory = assignment(ROOT / "tools/terminal_tool_backends.py", "_ENV_BUILDERS")
    if not isinstance(factory, ast.Dict):
        raise TypeError("Factory declaration changed shape.")
    factories = {ast.literal_eval(key) for key in factory.keys if key is not None}
    if reserved != set(BUILTINS) or factories != reserved - {"managed_modal"}:
        raise ValueError("Built-in backend declarations need a new audit.")
    backends: list[dict[str, Any]] = []
    for name, (filename, classname, phase, dependency) in BUILTINS.items():
        source = ROOT / "tools/environments" / filename
        node = next(
            node
            for node in ast.parse(source.read_text(encoding="utf-8")).body
            if isinstance(node, ast.ClassDef) and node.name == classname
        )
        backends.append(
            {
                "id": name,
                "family": "modal" if name == "managed_modal" else name,
                "kind": "internal_mode" if name == "managed_modal" else "built_in",
                "source": source.relative_to(ROOT).as_posix(),
                "line": node.lineno,
                "sha256": digest(source),
                "phase": phase,
                "status": "planned",
                "evidence_level": "registry/factory/class declarations and selected lifecycle code inspected; runtime not verified",
                "xaa_module": "engine/xaa/runtime/execution (not implemented)",
                "dependencies": dependency,
                "acceptance": f"EXEC-{name.upper()} plus EXEC-COMMON-1 through EXEC-COMMON-8",
            }
        )
    tracked = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "ls-tree",
            "-r",
            "--name-only",
            "-z",
            "HEAD",
            "--",
            "plugin-catalog",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.split("\0")
    manifests = sorted(ROOT / path for path in tracked if path.endswith(".yaml"))
    candidates: set[str] = set()
    external: list[dict[str, Any]] = []
    for path in manifests:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        description = str(data.get("description", ""))
        lower = description.lower()
        if "terminal" in lower and ("backend" in lower or "environment" in lower):
            candidates.add(path.stem)
        if path.stem not in EXTERNAL:
            continue
        source_audit = EXTERNAL_SOURCE_AUDITS.get(path.stem)
        selector = re.search(r"terminal\.backend\s+([a-z][a-z0-9_-]*)", description)
        external.append(
            {
                "id": path.stem,
                "catalog_manifest": path.relative_to(ROOT).as_posix(),
                "manifest_sha256": digest(path),
                "repository": data["repo"],
                "reference_commit": data["sha"],
                "subdirectory": data.get("subdir") or "",
                "version": str(data.get("version", "")),
                "declared_platforms": data.get("platforms"),
                "declared_selector": selector[1] if selector else None,
                "required_environment_names": data.get("capabilities", {}).get("requires_env", []),
                "dependencies_and_limits": EXTERNAL[path.stem],
                "phase": 9,
                "status": "planned",
                "xaa_module": "engine/xaa/runtime/execution and integration adapters (not implemented)",
                "evidence_level": source_audit["evidence_level"]
                if source_audit
                else "catalog declaration only; external source/runtime not inspected",
                "source_audit": source_audit,
                "acceptance": source_audit["acceptance"]
                if source_audit
                else "External pinned source audit, then EXEC-COMMON-1 through EXEC-COMMON-8 and backend-specific requirements",
            }
        )
    unknown = candidates - EXTERNAL.keys() - EXCLUDED.keys()
    if unknown or set(EXTERNAL) - candidates:
        raise ValueError(
            f"Catalog candidates need review: {sorted(unknown | (set(EXTERNAL) - candidates))}"
        )
    supporting = [
        "agent/terminal_env_registry.py",
        "agent/terminal_env_provider.py",
        "tools/terminal_tool_backends.py",
        "tools/environments/base.py",
        "tools/environments/docker_egress.py",
    ]
    return {
        "reference_commit": PIN,
        "audit_date": "2026-11-03",
        "built_in_families": len(factories),
        "reserved_names": sorted(reserved),
        "built_ins": backends,
        "catalog_manifests_scanned": len(manifests),
        "catalog_candidate_rule": "Description contains terminal and either backend or environment",
        "catalog_candidates": sorted(candidates),
        "external_declarations": external,
        "excluded_candidates": [
            {"id": name, "reason": EXCLUDED[name]} for name in sorted(candidates - set(EXTERNAL))
        ],
        "supporting_sources": [
            {"path": path, "sha256": digest(ROOT / path)} for path in supporting
        ],
        "limitations": [
            "Keyword candidate discovery is not proof of exhaustive external capability coverage.",
            "Selected external repository pins were source-reviewed without execution; remaining external pins have not been fetched or executed.",
            "No execution backend parity is established; X.A.A. fixed filesystem workers are lifecycle containment, not generated-code isolation.",
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
                "Execution inventory drifted; inspect pinned sources before regenerating."
            )
    else:
        OUTPUT.write_text(encoded, encoding="utf-8")
    print(
        f"Execution audit: {data['built_in_families']} built-in families, {len(data['external_declarations'])} external declarations, {len(data['catalog_candidates'])} reviewed candidates."
    )


if __name__ == "__main__":
    main()

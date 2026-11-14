"""Exercise imported npm dependencies through the authenticated owner API and WSL."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.extensions.npm_dependencies import ProcedureNpmDependencyLock
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.runs import RunStore

TOKEN = "xaa-live-npm-api-acceptance-token-2026"
ROOT_PACKAGE = {
    "name": "is-odd",
    "version": "3.0.1",
    "url": "https://registry.npmjs.org/is-odd/-/is-odd-3.0.1.tgz",
    "integrity": "sha512-CQpnWPrDwmP1+SMHXZhtLtJv90yiyVfluGsX5iNCVkrhQtU3TQHsUWPG9wkdk9Lgd5yNpAg9jQEo90CBaXgWMA==",
}
TRANSITIVE_PACKAGE = {
    "name": "is-number",
    "version": "6.0.0",
    "url": "https://registry.npmjs.org/is-number/-/is-number-6.0.0.tgz",
    "integrity": "sha512-Wu1VHeILBK8KAWJUAiSZQX94GmOE45Rg6/538fKwiloUu21KncEkYGPqob2oSZ5mUT73vLGrHQjKw3KMPwfDzg==",
}


class ApiResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def text(self) -> str: ...

    @property
    def content(self) -> bytes: ...

    def json(self) -> Any: ...


class NoInferenceProvider(LMStudioProvider):
    async def models(self) -> list[ModelInfo]:
        return []

    async def close(self) -> None:
        return None


def _make_import_package(root: Path, *, lockfile_version: int = 3) -> Path:
    package = root / "fixture-plugin"
    skill = package / "skills" / "live-npm-check"
    skill.mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: live-npm-check\n"
        "description: Verify an approved imported npm environment.\n"
        "dependencies:\n"
        "  npm: [is-odd@^3.0.1]\n"
        "allowed-tools: shell.exec\n"
        "---\n"
        "Run the linked Node.js check.\n\n"
        "[Manifest](package.json) [Lock](package-lock.json) "
        "[Check](scripts/check.cjs)\n",
        encoding="utf-8",
    )
    manifest = {
        "name": "xaa-live-registry-acceptance",
        "version": "1.0.0",
        "dependencies": {ROOT_PACKAGE["name"]: "^3.0.1"},
    }
    lock: dict[str, Any] = {
        "name": manifest["name"],
        "version": manifest["version"],
        "lockfileVersion": lockfile_version,
        "requires": True,
        "packages": {
            "": {
                "name": manifest["name"],
                "version": manifest["version"],
                "dependencies": manifest["dependencies"],
            },
            "node_modules/is-odd": {
                "version": ROOT_PACKAGE["version"],
                "resolved": ROOT_PACKAGE["url"],
                "integrity": ROOT_PACKAGE["integrity"],
                "dependencies": {TRANSITIVE_PACKAGE["name"]: "^6.0.0"},
            },
            "node_modules/is-number": {
                "version": TRANSITIVE_PACKAGE["version"],
                "resolved": TRANSITIVE_PACKAGE["url"],
                "integrity": TRANSITIVE_PACKAGE["integrity"],
            },
        },
    }
    if lockfile_version == 2:
        # npm v2 carries the v1-compatible dependency tree alongside `packages`.
        lock["dependencies"] = {
            ROOT_PACKAGE["name"]: {
                "version": ROOT_PACKAGE["version"],
                "resolved": ROOT_PACKAGE["url"],
                "integrity": ROOT_PACKAGE["integrity"],
                "requires": {TRANSITIVE_PACKAGE["name"]: "^6.0.0"},
                "dependencies": {
                    TRANSITIVE_PACKAGE["name"]: {
                        "version": TRANSITIVE_PACKAGE["version"],
                        "resolved": TRANSITIVE_PACKAGE["url"],
                        "integrity": TRANSITIVE_PACKAGE["integrity"],
                    }
                },
            }
        }
    (skill / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
    (skill / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
    (skill / "scripts" / "check.cjs").write_text(
        "const fs=require('node:fs'),path=require('node:path'),isOdd=require('is-odd');\n"
        "if(isOdd(7)!==true||isOdd(2)!==false||isOdd(-3)!==true)process.exit(10);\n"
        "if(fs.realpathSync(path.resolve(__dirname,'..','node_modules'))!=='/opt/xaa/procedure-node/node_modules')process.exit(11);\n"
        "if(fs.existsSync('node_modules'))process.exit(12);\n"
        "process.stdout.write('authenticated-api-transitive-check-passed');\n",
        encoding="utf-8",
    )
    (package / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "xaa-live-registry-acceptance",
                "version": "1.0.0",
                "description": "Live registry API acceptance fixture.",
            }
        ),
        encoding="utf-8",
    )
    return package


def _checked(response: ApiResponse, expected_status: int, label: str) -> dict[str, Any]:
    if response.status_code != expected_status:
        raise RuntimeError(
            f"{label}: expected HTTP {expected_status}, got {response.status_code}: {response.text}"
        )
    if not response.content:
        return {}
    return response.json()


async def run(distribution: str, report_path: Path, *, lockfile_version: int = 3) -> dict[str, Any]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    work_root = report_path.parent / f"{report_path.stem}-work"
    package = _make_import_package(work_root / "import-source", lockfile_version=lockfile_version)
    store = RunStore(work_root / "api-state" / "runs.sqlite3")
    app = create_app(NoInferenceProvider(), SecretStr(TOKEN), store=store)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    transport: WSLTransport | None = None
    digest: str | None = None
    installed = False
    evidence: dict[str, Any] = {
        "distribution": distribution,
        "lockfile_version": lockfile_version,
        "model_inference": "not used",
        "registry_packages": [ROOT_PACKAGE["name"], TRANSITIVE_PACKAGE["name"]],
        "owner_api_steps": [],
    }

    try:
        with TestClient(app) as client:
            import_payload = {
                "root": str(package.parent),
                "package_name": "xaa-live-registry-acceptance",
                "skill_name": "live-npm-check",
            }
            _checked(
                client.post("/extensions/packages/import-skill", json=import_payload),
                401,
                "Unauthenticated import",
            )
            imported = _checked(
                client.post("/extensions/packages/import-skill", headers=auth, json=import_payload),
                201,
                "Authenticated package import",
            )
            skill_id = imported["id"]
            endpoint = f"/skills/{skill_id}/versions/1/npm-dependency-locks"
            inspected_response = client.post(
                f"{endpoint}/inspect", headers=auth, json={"distribution": distribution}
            )
            inspected = _checked(inspected_response, 200, "Linked lock inspection")
            saved = _checked(
                client.put(endpoint, headers=auth, json=inspected), 200, "Save reviewed lock"
            )
            digest = saved["environment_digest"]
            review_endpoint = f"{endpoint}/{digest}/review"
            _checked(
                client.post(review_endpoint, headers=auth, json={"decision": "approved"}),
                409,
                "Approval before acquisition",
            )
            _checked(
                client.post(f"{endpoint}/{digest}/install", headers=auth),
                409,
                "Install before approval",
            )
            acquired = _checked(
                client.post(f"{endpoint}/{digest}/acquire", headers=auth),
                200,
                "Live AppContainer registry acquisition",
            )
            if (
                acquired.get("tarballs_present") is not True
                or len(acquired.get("verified_tarballs", [])) != 2
            ):
                raise RuntimeError("The API did not persist the complete verified package graph.")
            approved = _checked(
                client.post(review_endpoint, headers=auth, json={"decision": "approved"}),
                200,
                "Approve acquired lock",
            )
            if approved.get("decision") != "approved":
                raise RuntimeError("The API did not persist owner approval.")
            installed_record = _checked(
                client.post(f"{endpoint}/{digest}/install", headers=auth),
                200,
                "Offline WSL install through API",
            )
            installed = installed_record.get("environment_installed") is True
            if not installed:
                raise RuntimeError("The API did not persist the installed environment receipt.")
            lock = ProcedureNpmDependencyLock.model_validate(installed_record["lock"], strict=True)
            if lock.environment_digest() != digest or lock.skill_id != skill_id:
                raise RuntimeError(
                    "The installed lock does not match the imported procedure identity."
                )
            tree_hash = installed_record["installed_tree_sha256"]
            transport = await WSLTransport.discover(distribution, deadline=time.monotonic() + 30)
            procedure_root = f"__xaa_selected_skill__/{skill_id}/v1"
            script_path = f"{procedure_root}/scripts/check.cjs"
            script_source = (
                package / "skills" / "live-npm-check" / "scripts" / "check.cjs"
            ).read_bytes()
            snapshot = WorkspaceSnapshot(
                files=(SnapshotFile(path=script_path, content=script_source),),
                directories=(
                    "__xaa_selected_skill__",
                    f"__xaa_selected_skill__/{skill_id}",
                    procedure_root,
                    f"{procedure_root}/scripts",
                ),
            )
            intent = CommandIntent(
                argv=["node", script_path],
                max_seconds=15,
                execution_id=uuid4().hex,
                backend="wsl-bubblewrap-v1",
                distribution=distribution,
                profile_sha256=transport.profile_sha256,
                workspace="/workspace/npm-live-registry-api-acceptance",
                procedure_node_environment_digest=digest,
                procedure_node_environment_tree_sha256=tree_hash,
                procedure_skill_id=skill_id,
                procedure_version=1,
            )
            execution = await transport.run(intent, snapshot, deadline=time.monotonic() + 30)
            stdout = base64.b64decode(execution.get("stdout_b64", ""), validate=True).decode(
                "utf-8"
            )
            if (
                execution.get("succeeded") is not True
                or execution.get("cleanup_confirmed") is not True
                or execution.get("procedure_node_environment_digest") != digest
                or stdout != "authenticated-api-transitive-check-passed"
            ):
                raise RuntimeError("The imported package failed its bound sandbox run.")
            listing = _checked(client.get(endpoint, headers=auth), 200, "Read lock history")
            record = next(row for row in listing["locks"] if row["environment_digest"] == digest)
            if record["environment_installed"] is not True:
                raise RuntimeError("The API readback lost the installed receipt.")
            removed = _checked(
                client.delete(f"{endpoint}/{digest}/install", headers=auth),
                200,
                "Remove exact approved environment through API",
            )
            installed = removed.get("environment_installed") is True
            if installed:
                raise RuntimeError("The API did not record exact environment removal.")
            evidence.update(
                {
                    "skill_id": skill_id,
                    "environment_digest": digest,
                    "verified_tarballs": acquired["verified_tarballs"],
                    "installed_tree_sha256": tree_hash,
                    "procedure_output": stdout,
                    "network_during_install_and_run": "none",
                    "workspace_node_modules_absent": True,
                    "execution_cleanup_confirmed": True,
                    "exact_environment_removed": True,
                    "owner_api_steps": [
                        "unauthenticated import rejected",
                        "authenticated package import",
                        "linked manifest/lock inspection",
                        "source-bound lock persistence",
                        "approval refused before tarball acquisition",
                        "installation refused before approval",
                        "live registry acquisition through AppContainer API path",
                        "explicit approval of exact acquired lock",
                        "offline managed WSL installation through API",
                        "selected-procedure sandbox execution with exact environment binding",
                        "digest-scoped API removal and receipt readback",
                    ],
                }
            )
        return evidence
    finally:
        if installed and digest is not None:
            if transport is None:
                transport = await WSLTransport.discover(
                    distribution, deadline=time.monotonic() + 30
                )
            await transport.remove_node_environment(digest, deadline=time.monotonic() + 45)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    parser.add_argument("--lockfile-version", type=int, choices=(2, 3), default=3)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    result = asyncio.run(
        run(
            arguments.distribution,
            arguments.report,
            lockfile_version=arguments.lockfile_version,
        )
    )
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

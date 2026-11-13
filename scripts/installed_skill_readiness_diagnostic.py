"""Query skill dependency readiness through the packaged local API."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

SKILL_TEXT = """---
name: readiness-check
description: Verify dependency status in the packaged runtime.
dependencies:
  python: [\"packaging>=26.3,<27\", \"packaging==0.0\", \"requests>=2.0\"]
  python3: [\"xaa-readiness-missing-fixture-20261113==99.0\"]
  npm: [\"left-pad>=1.0\", \"https://example.test/private-token\"]
---
# Readiness check
"""


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument(
        "--distribution",
        help="Also check this imported skill's Python requirements in the restricted WSL runtime.",
    )
    args = parser.parse_args()
    engine = args.engine.resolve(strict=True)
    acceptance = Path(".acceptance") / f"installed-skill-readiness-{uuid4().hex}"
    acceptance.mkdir(parents=True, exist_ok=False)
    plugin_root = acceptance / "plugins"
    skill_dir = plugin_root / "readiness-check"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(SKILL_TEXT, encoding="utf-8")
    token = secrets.token_urlsafe(48)
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = os.environ.copy()
    environment.update(
        XAA_API_TOKEN=token,
        XAA_API_PORT=str(port),
        LOCALAPPDATA=str(acceptance / "localappdata"),
        APPDATA=str(acceptance / "appdata"),
    )
    Path(environment["LOCALAPPDATA"]).mkdir()
    Path(environment["APPDATA"]).mkdir()
    process_log = (acceptance / "engine.log").open("wb")
    process = subprocess.Popen(
        [str(engine)],
        env=environment,
        stdout=process_log,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    client = httpx.Client(
        base_url=base_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    shutdown_requested = False
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Installed engine exited early with code {process.returncode}.")
            try:
                # Installed-package discovery is authenticated and avoids model/provider checks.
                response = client.get("/extensions/packages/installed")
                if response.status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        else:
            raise TimeoutError("Installed API did not become ready within 45 seconds.")

        discovery = client.post(
            "/extensions/packages/discover",
            json={"root": str(plugin_root.resolve())},
        )
        discovery.raise_for_status()
        payload = discovery.json()
        if len(payload.get("packages", [])) != 1:
            raise RuntimeError(f"Expected one discovered fixture package; got {payload!r}.")
        skills = payload["packages"][0].get("skills", [])
        if len(skills) != 1:
            raise RuntimeError(f"Expected one discovered fixture skill; got {skills!r}.")
        readiness = skills[0].get("requirement_readiness", {})
        expected = {
            "available_python_dependency_specs": "packaging>=26.3,<27",
            "missing_python_dependency_specs": "xaa-readiness-missing-fixture-20261113==99.0",
            "incompatible_python_dependency_specs": "packaging==0.0",
        }
        for field, spec in expected.items():
            if spec not in readiness.get(field, []):
                raise RuntimeError(f"Expected {spec!r} in {field}: {readiness!r}.")
        if readiness.get("dependencies_unresolved") is not True:
            raise RuntimeError(
                f"Declared missing dependencies were not marked unresolved: {readiness!r}."
            )
        if readiness.get("unverified_dependency_count", 0) < 1:
            raise RuntimeError(
                f"Unsupported npm dependency was not marked unverified: {readiness!r}."
            )
        unverified_packages = readiness.get("unverified_dependency_packages")
        if unverified_packages != [{"manager": "npm", "name": "left-pad"}]:
            raise RuntimeError(
                f"Unsupported dependency identity was not safely retained: {readiness!r}."
            )
        readiness_json = json.dumps(readiness, sort_keys=True)
        if any(value in readiness_json for value in ("left-pad>=1.0", "https://", "private-token")):
            raise RuntimeError("Readiness exposed a dependency constraint or URL.")

        imported: dict[str, Any] | None = None
        wsl_readiness: dict[str, Any] | None = None
        if args.distribution:
            imported_response = client.post(
                "/extensions/packages/import-skill",
                json={
                    "root": str(plugin_root.resolve()),
                    "package_name": "readiness-check",
                    "skill_name": "readiness-check",
                },
            )
            imported_response.raise_for_status()
            imported_payload: dict[str, Any] = imported_response.json()
            skill_id = imported_payload.get("id")
            if not isinstance(skill_id, str):
                raise RuntimeError(
                    "Installed API did not return the imported skill identity: "
                    f"{imported_payload!r}."
                )
            wsl_response = client.post(
                f"/skills/{skill_id}/versions/1/python-readiness",
                json={"distribution": args.distribution},
                timeout=60,
            )
            wsl_response.raise_for_status()
            wsl_payload: dict[str, Any] = wsl_response.json()
            wsl_by_requirement = {
                item["requirement"]: item["status"] for item in wsl_payload.get("dependencies", [])
            }
            if (
                wsl_payload.get("state") != "ready"
                or wsl_by_requirement.get("requests>=2.0") != "available"
                or wsl_by_requirement.get("xaa-readiness-missing-fixture-20261113==99.0")
                != "missing"
                or wsl_payload.get("unverified_dependency_count") != 2
            ):
                raise RuntimeError(
                    "Installed imported-skill WSL readiness did not match the fixture: "
                    f"{wsl_payload!r}."
                )
            imported = imported_payload
            wsl_readiness = wsl_payload

        report: dict[str, Any] = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "engine_path": str(engine),
            "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest(),
            "endpoint": "/extensions/packages/discover",
            "available_python_dependency_specs": readiness["available_python_dependency_specs"],
            "missing_python_dependency_specs": readiness["missing_python_dependency_specs"],
            "incompatible_python_dependency_specs": readiness[
                "incompatible_python_dependency_specs"
            ],
            "unverified_dependency_count": readiness["unverified_dependency_count"],
            "unverified_dependency_packages": unverified_packages,
            "dependencies_unresolved": readiness["dependencies_unresolved"],
            "imported_skill": imported,
            "restricted_wsl_readiness": wsl_readiness,
            "model_inference_requested": False,
        }
        response = client.post("/shutdown")
        response.raise_for_status()
        shutdown_requested = True
        process.wait(timeout=10)
        if process.returncode != 0:
            raise RuntimeError(f"Installed engine shutdown returned code {process.returncode}.")
        report["graceful_shutdown"] = True
        report_path = acceptance / "report.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({**report, "report": str(report_path)}, indent=2))
    finally:
        if process.poll() is None:
            if not shutdown_requested:
                try:
                    client.post("/shutdown")
                except httpx.HTTPError:
                    pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        client.close()
        process_log.close()


if __name__ == "__main__":
    main()

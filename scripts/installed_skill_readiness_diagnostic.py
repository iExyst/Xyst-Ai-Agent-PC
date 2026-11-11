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
  python: [\"packaging>=26.3,<27\", \"packaging==0.0\"]
  python3: [\"xaa-readiness-missing-fixture==99.0\"]
  npm: [\"left-pad>=1.0\"]
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
    args = parser.parse_args()
    engine = args.engine.resolve(strict=True)
    acceptance = Path(".acceptance") / f"installed-skill-readiness-{uuid4().hex}"
    acceptance.mkdir(parents=True, exist_ok=False)
    plugin_root = acceptance / "plugins"
    skill_dir = plugin_root / "readiness-fixture"
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
            "missing_python_dependency_specs": "xaa-readiness-missing-fixture==99.0",
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
            "dependencies_unresolved": readiness["dependencies_unresolved"],
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

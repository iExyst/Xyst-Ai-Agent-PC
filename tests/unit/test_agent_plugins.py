from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.extensions.agent_plugins import discover_agent_plugins
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.storage.runs import RunStore

TOKEN = "a-test-token-that-is-long-enough-to-pass-validation"


class FakeProvider(LMStudioProvider):
    async def models(self) -> list[ModelInfo]:
        return []

    async def close(self) -> None:
        return None


def make_package(root: Path) -> Path:
    package = root / "sample"
    (package / "skills" / "summarize").mkdir(parents=True)
    (package / "skills" / "summarize" / "SKILL.md").write_text(
        "ignored instruction text", encoding="utf-8"
    )
    (package / "plugin.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
                "name": "sample-package",
                "version": "1.2.3",
                "description": "Fixture",
                "extensions": {
                    "com.nousresearch.hermes": {"servers": {"local-helper": {"trust": "local"}}}
                },
            }
        ),
        encoding="utf-8",
    )
    (package / "mcp.json").write_text(
        json.dumps(
            {
                "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
                "mcpServers": {
                    "local-helper": {
                        "type": "stdio",
                        "command": "python.exe",
                        "args": ["server.py", "secret-argument"],
                        "env": {"API_TOKEN": "secret-value"},
                    },
                    "remote-helper": {
                        "type": "streamable-http",
                        "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer hidden"},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return package


def test_discovery_summarizes_package_without_returning_secrets_or_skill_text(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path)
    result = discover_agent_plugins(str(tmp_path))
    assert len(result.packages) == 1
    summary = result.packages[0]
    assert summary.name == "sample-package"
    assert summary.version == "1.2.3"
    assert [skill.name for skill in summary.skills] == ["summarize"]
    assert summary.servers[0].command_name == "python.exe"
    assert summary.servers[0].argument_count == 2
    assert summary.servers[0].environment_names == ["API_TOKEN"]
    assert summary.servers[1].endpoint == "https://example.test/…"
    encoded = summary.model_dump_json()
    for forbidden in (
        "secret-value",
        "secret-argument",
        "Bearer hidden",
        "ignored instruction text",
    ):
        assert forbidden not in encoded
    assert str(package) == summary.path


def test_discovery_rejects_manifest_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "plugins"
    root.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text('{"name":"outside"}', encoding="utf-8")
    (root / "escape").mkdir()
    try:
        (root / "escape" / "plugin.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    result = discover_agent_plugins(str(root))
    assert result.packages == []
    assert result.diagnostics
    assert "escapes" in result.diagnostics[0].message


def test_discovery_reports_duplicate_manifest_keys(tmp_path: Path) -> None:
    package = tmp_path / "broken"
    package.mkdir()
    (package / "plugin.json").write_text('{"name":"first","name":"second"}', encoding="utf-8")
    result = discover_agent_plugins(str(tmp_path))
    assert result.packages == []
    assert "duplicate" in result.diagnostics[0].message


def test_discovery_api_requires_auth_and_returns_static_summaries(tmp_path: Path) -> None:
    make_package(tmp_path)
    app = create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(tmp_path / "api.sqlite3"))
    with TestClient(app) as client:
        assert (
            client.post("/extensions/packages/discover", json={"root": str(tmp_path)}).status_code
            == 401
        )
        response = client.post(
            "/extensions/packages/discover",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"root": str(tmp_path)},
        )
    assert response.status_code == 200
    assert response.json()["packages"][0]["version"] == "1.2.3"
    assert "secret-value" not in response.text

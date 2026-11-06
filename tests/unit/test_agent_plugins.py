from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.extensions.agent_plugins import discover_agent_plugins, read_plugin_skill
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
        "---\nname: summarize\ndescription: Summarize a bounded file.\n"
        "allowed-tools: workspace.read_file shell.exec\n---\n"
        "ignored instruction text\n\n[Reference](references/guide.md)",
        encoding="utf-8",
    )
    (package / "skills" / "summarize" / "references").mkdir()
    (package / "skills" / "summarize" / "references" / "guide.md").write_text(
        "Reference text", encoding="utf-8"
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
    assert summary.skills[0].description == "Summarize a bounded file."
    assert summary.skills[0].allowed_tools == ["workspace.read_file", "shell.exec"]
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


def test_discovery_rejects_yaml_aliases_in_skill_metadata(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    (package / "skills" / "summarize" / "SKILL.md").write_text(
        "---\nname: &skill summarize\ndescription: *skill\n---\ntext", encoding="utf-8"
    )
    result = discover_agent_plugins(str(tmp_path))
    assert result.packages == []
    assert "aliases" in result.diagnostics[0].message


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


def test_plugin_skill_import_creates_inactive_candidate_with_provenance(tmp_path: Path) -> None:
    make_package(tmp_path)
    app = create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(tmp_path / "import.sqlite3"))
    payload = {"root": str(tmp_path), "package_name": "sample-package", "skill_name": "summarize"}
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        assert client.post("/extensions/packages/import-skill", json=payload).status_code == 401
        created = client.post("/extensions/packages/import-skill", headers=headers, json=payload)
        assert created.status_code == 201
        assert created.json()["lifecycle_state"] == "candidate"
        version = client.get(f"/skills/{created.json()['id']}/versions/1", headers=headers)
        assert client.get(f"/skills/{created.json()['id']}/versions/1/export").status_code == 401
        exported = client.get(f"/skills/{created.json()['id']}/versions/1/export", headers=headers)
        revised = client.post(
            f"/skills/{created.json()['id']}/versions",
            headers=headers,
            json={
                "name": "summarize",
                "content": "reviewed revision",
                "required_tools": [],
                "required_permissions": [],
                "expected_version": 1,
            },
        )
        revised_version = client.get(f"/skills/{created.json()['id']}/versions/2", headers=headers)
    assert version.status_code == 200
    evidence = version.json()["evaluation_evidence"]
    assert evidence["provenance"] == "agent_plugin"
    assert evidence["source"]["package_version"] == "1.2.3"
    assert evidence["source"]["skill_name"] == "summarize"
    assert evidence["source"]["content_sha256"]
    assert evidence["state"] == "candidate"
    assert version.json()["required_permissions"] == ["workspace.read"]
    assert [tool["name"] for tool in version.json()["required_tools"]] == ["workspace.read_file"]
    assert evidence["source"]["unmapped_allowed_tools"] == "shell.exec"
    assert evidence["source"]["description"] == "Summarize a bounded file."
    resource = version.json()["assets"][0]
    assert resource["path"] == "references/guide.md"
    assert resource["sha256"]
    assert resource["content_base64"]
    assert revised.status_code == 201
    assert revised_version.json()["evaluation_evidence"]["provenance"] == "agent_plugin"
    assert revised_version.json()["evaluation_evidence"]["source"] == evidence["source"]
    assert revised_version.json()["assets"] == version.json()["assets"]
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("application/zip")
    unpacked = tmp_path / "exported"
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert "plugin.json" in archive.namelist()
        archive.extractall(unpacked)
    exported_packages = discover_agent_plugins(str(tmp_path / "exported"))
    assert len(exported_packages.packages) == 1
    assert exported_packages.packages[0].skills[0].name == "summarize"
    exported_skill = read_plugin_skill(str(unpacked), "sample-package", "summarize")
    assert exported_skill.content == version.json()["content"]
    assert exported_skill.assets[0]["path"] == "references/guide.md"
    assert exported_skill.assets[0]["content_base64"] == resource["content_base64"]


@pytest.mark.parametrize("link", ["../outside.txt", "./guide.md", "%2e%2e/outside.txt"])
def test_plugin_skill_import_rejects_unsafe_resource_paths(tmp_path: Path, link: str) -> None:
    package = make_package(tmp_path)
    skill_file = package / "skills" / "summarize" / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8").replace("references/guide.md", link)
    skill_file.write_text(content, encoding="utf-8")
    app = create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(tmp_path / "unsafe.sqlite3"))
    with TestClient(app) as client:
        response = client.post(
            "/extensions/packages/import-skill",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "root": str(tmp_path),
                "package_name": "sample-package",
                "skill_name": "summarize",
            },
        )
    assert response.status_code == 400


def test_plugin_skill_import_refuses_oversized_skill_without_creating_candidate(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path)
    frontmatter = "---\nname: summarize\ndescription: Oversized fixture.\n---\n"
    (package / "skills" / "summarize" / "SKILL.md").write_text(
        frontmatter + "x" * 16_385, encoding="utf-8"
    )
    app = create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(tmp_path / "large.sqlite3"))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = {"root": str(tmp_path), "package_name": "sample-package", "skill_name": "summarize"}
    with TestClient(app) as client:
        response = client.post("/extensions/packages/import-skill", headers=headers, json=payload)
        procedures = client.get("/skills", headers=headers)
    assert response.status_code == 400
    assert "16 KiB" in response.json()["detail"]
    assert procedures.json() == []

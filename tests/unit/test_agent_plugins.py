from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.extensions import agent_plugins
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
        "platforms: [linux, macos]\n"
        "prerequisites:\n  commands: [git, python.exe --version]\n"
        'dependencies:\n  python: [requests>=2.0, "git+https://example.test/repo.git"]\n'
        "required_environment_variables: [API_TOKEN]\n"
        "metadata:\n  category: writing\n"
        "  hermes:\n    category: writing\n    tags: [writing, utility]\n"
        "    requires_toolsets: [terminal, files]\n"
        "    related_skills: [review]\n"
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
    assert summary.skills[0].category == "writing"
    assert summary.skills[0].allowed_tools == ["workspace.read_file", "shell.exec"]
    requirements = summary.skills[0].declared_requirements
    assert requirements["platforms"] == ["linux", "macos"]
    assert requirements["hermes_toolsets"] == ["files", "terminal"]
    assert requirements["required_command_names"] == ["git", "python.exe"]
    assert requirements["required_environment_names"] == ["API_TOKEN"]
    assert requirements["prerequisite_fields"] == ["commands"]
    assert requirements["dependencies_declared"] is True
    assert requirements["dependency_names"] == ["requests"]
    assert requirements["dependency_managers"] == ["python"]
    assert requirements["unparsed_dependency_count"] == 1
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


def test_requirement_readiness_checks_names_without_exposing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_plugins.sys, "platform", "win32")
    monkeypatch.setattr(
        agent_plugins.shutil,
        "which",
        lambda name: "C:/tools/git.exe" if name == "git" else None,
    )
    monkeypatch.setenv("XAA_FIXTURE_TOKEN", "must-not-appear")
    monkeypatch.delenv("MISSING_FIXTURE_TOKEN", raising=False)

    result = agent_plugins._assess_requirement_readiness(
        {
            "platforms": ["windows"],
            "required_command_names": ["git", "not-installed"],
            "required_environment_names": ["XAA_FIXTURE_TOKEN", "MISSING_FIXTURE_TOKEN"],
            "dependencies_declared": True,
            "dependency_count": 2,
            "hermes_toolsets": ["terminal"],
        }
    )

    assert result["current_platform"] == "windows"
    assert result["platform_status"] == "compatible"
    assert result["available_command_names"] == ["git"]
    assert result["missing_command_names"] == ["not-installed"]
    assert result["present_environment_names"] == ["XAA_FIXTURE_TOKEN"]
    assert result["missing_environment_names"] == ["MISSING_FIXTURE_TOKEN"]
    assert result["dependencies_unresolved"] is True
    assert result["dependency_count_unresolved"] == 2
    assert result["hermes_toolsets_unresolved"] == ["terminal"]
    assert "command_semantics" in result["not_assessed"]
    assert "must-not-appear" not in json.dumps(result)


def test_requirement_readiness_marks_platform_mismatch_and_unknown_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_plugins.sys, "platform", "win32")
    assert (
        agent_plugins._assess_requirement_readiness({"platforms": ["linux"]})["platform_status"]
        == "incompatible"
    )
    unknown = agent_plugins._assess_requirement_readiness({"platforms": ["plan9"]})
    assert unknown["platform_status"] == "unrecognized"
    assert unknown["unknown_platform_names"] == ["plan9"]
    assert agent_plugins._assess_requirement_readiness({})["platform_status"] == "not_declared"


def test_requirement_readiness_caps_distinct_command_lookups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_plugins, "MAX_READINESS_COMMAND_CHECKS", 1)
    monkeypatch.setattr(agent_plugins.shutil, "which", lambda name: f"/bin/{name}")
    cache: dict[str, bool] = {}

    result = agent_plugins._assess_requirement_readiness(
        {"required_command_names": ["first", "second"]}, cache
    )

    assert result["available_command_names"] == ["first"]
    assert result["missing_command_names"] == []
    assert result["unchecked_command_names"] == ["second"]
    assert "command_presence_budget" in result["not_assessed"]


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


def test_discovery_rejects_invalid_skill_category_metadata(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    skill_file = package / "skills" / "summarize" / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace(
            "category: writing", "category: Bad Category"
        ),
        encoding="utf-8",
    )
    result = discover_agent_plugins(str(tmp_path))
    assert result.packages == []
    assert "category" in result.diagnostics[0].message


def test_discovery_accepts_hermes_metadata_and_uses_its_category(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    skill_file = package / "skills" / "summarize" / "SKILL.md"
    content = skill_file.read_text(encoding="utf-8")
    content = content.replace("metadata:\n  category: writing\n", "metadata:\n")
    skill_file.write_text(content, encoding="utf-8")

    result = discover_agent_plugins(str(tmp_path))

    assert len(result.packages) == 1
    assert result.packages[0].skills[0].category == "writing"


def test_discovery_rejects_conflicting_standard_and_hermes_categories(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    skill_file = package / "skills" / "summarize" / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8").replace(
            "    category: writing", "    category: other"
        ),
        encoding="utf-8",
    )

    result = discover_agent_plugins(str(tmp_path))

    assert result.packages == []
    assert "disagree" in result.diagnostics[0].message


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
    readiness = response.json()["packages"][0]["skills"][0]["requirement_readiness"]
    assert readiness["platform_status"] in {"compatible", "incompatible", "unrecognized"}
    assert "must-not-appear" not in response.text
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
                "description": "Updated bounded file summaries.",
                "category": "technical-writing",
                "required_tools": [],
                "required_permissions": [],
                "expected_version": 1,
            },
        )
        revised_version = client.get(f"/skills/{created.json()['id']}/versions/2", headers=headers)
        revised_export = client.get(
            f"/skills/{created.json()['id']}/versions/2/export", headers=headers
        )
    assert version.status_code == 200
    evidence = version.json()["evaluation_evidence"]
    assert evidence["provenance"] == "agent_plugin"
    assert evidence["source"]["package_version"] == "1.2.3"
    assert evidence["source"]["skill_name"] == "summarize"
    assert evidence["source"]["content_sha256"]
    assert evidence["state"] == "candidate"
    assert version.json()["required_permissions"] == ["workspace.read"]
    assert version.json()["description"] == "Summarize a bounded file."
    assert version.json()["category"] == "writing"
    assert [tool["name"] for tool in version.json()["required_tools"]] == ["workspace.read_file"]
    assert evidence["source"]["unmapped_allowed_tools"] == "shell.exec"
    assert evidence["source"]["description"] == "Summarize a bounded file."
    declared = json.loads(evidence["source"]["declared_requirements"])
    assert declared["hermes_toolsets"] == ["files", "terminal"]
    assert declared["required_environment_names"] == ["API_TOKEN"]
    assert declared["dependency_names"] == ["requests"]
    assert "2.0" not in json.dumps(declared)
    assert "example.test" not in json.dumps(declared)
    assert "tags: [writing, utility]" in version.json()["content"]
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
    assert exported_skill.category == "writing"
    assert exported_skill.description == "Summarize a bounded file."
    revised_package_root = tmp_path / "revised-export"
    with zipfile.ZipFile(io.BytesIO(revised_export.content)) as archive:
        archive.extractall(revised_package_root)
    revised_package = discover_agent_plugins(str(revised_package_root)).packages[0]
    revised_skill = read_plugin_skill(str(revised_package_root), revised_package.name, "summarize")
    assert revised_skill.description == "Updated bounded file summaries."
    assert revised_skill.category == "technical-writing"


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

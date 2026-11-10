from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.extensions import agent_plugins
from xaa.extensions.agent_plugins import (
    discover_agent_plugins,
    plugin_source_sha256,
    read_plugin_skill,
)
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.storage.mcp import MCPRegistry, MCPServerSpec
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
        "    requires_tools: [workspace.read_file, shell.exec]\n"
        "    fallback_for_toolsets: [browser]\n"
        "    fallback_for_tools: [web.search]\n"
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
                        "env": {"MCP_TOKEN": "http-secret-value"},
                        "headers": {"Authorization": "Bearer ${MCP_TOKEN}"},
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
    assert requirements["hermes_required_tools"] == ["shell.exec", "workspace.read_file"]
    assert requirements["hermes_fallback_toolsets"] == ["browser"]
    assert requirements["hermes_fallback_tools"] == ["web.search"]
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
    assert summary.servers[1].environment_names == ["MCP_TOKEN"]
    encoded = summary.model_dump_json()
    for forbidden in (
        "secret-value",
        "secret-argument",
        "http-secret-value",
        "ignored instruction text",
    ):
        assert forbidden not in encoded
    assert str(package) == summary.path


def test_plugin_manifest_accepts_optional_version_and_reports_ignored_fields(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path)
    manifest_path = package / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("version")
    manifest["author"] = {"name": "Xyst", "email": "owner@example.test"}
    manifest["homepage"] = "not required to be a URL by the portable schema"
    manifest["keywords"] = ["tools", "local"]
    manifest["futureClientField"] = {"ignored": True}
    manifest["extensions"] = "unsupported shape is ignored by v1 clients"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = discover_agent_plugins(str(tmp_path))
    assert len(result.packages) == 1
    assert result.packages[0].version is None
    assert [diagnostic.message for diagnostic in result.diagnostics] == [
        "plugin.json has unsupported field 'futureClientField'; ignored",
        "plugin.json extensions is not an object; ignored",
    ]


@pytest.mark.parametrize(
    "field_value",
    [
        ("version", 1),
        ("author", {"name": 3}),
        ("author", {"name": "Xyst", "website": "https://example.test"}),
        ("keywords", ["valid", 3]),
    ],
)
def test_plugin_manifest_rejects_invalid_standard_metadata(
    tmp_path: Path, field_value: tuple[str, object]
) -> None:
    package = make_package(tmp_path)
    manifest_path = package / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field_value[0]] = field_value[1]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = discover_agent_plugins(str(tmp_path))
    assert result.packages == []
    assert len(result.diagnostics) == 1


def test_mcp_package_discovery_keeps_valid_servers_and_skills_when_entries_fail(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path)
    mcp_path = package / "mcp.json"
    config = json.loads(mcp_path.read_text(encoding="utf-8"))
    config["mcpServers"]["bad-cwd"] = {
        "type": "stdio",
        "command": "python.exe",
        "cwd": "./../../outside",
    }
    config["mcpServers"]["legacy-sse"] = {
        "type": "sse",
        "url": "https://example.test/events",
    }
    config["mcpServers"]["bad-headers"] = {
        "type": "streamable-http",
        "url": "https://example.test/duplicate-headers",
        "headers": {"X-Tenant": "one", "x-tenant": "two"},
    }
    config["mcpServers"]["local_helper"] = {
        "type": "streamable-http",
        "url": "https://example.test/underscore-name",
    }
    mcp_path.write_text(json.dumps(config), encoding="utf-8")

    result = discover_agent_plugins(str(tmp_path))
    assert len(result.packages) == 1
    summary = result.packages[0]
    assert [skill.name for skill in summary.skills] == ["summarize"]
    assert [server.name for server in summary.servers] == [
        "local-helper",
        "remote-helper",
        "local_helper",
    ]
    assert len(result.diagnostics) == 3
    assert any(
        "bad-cwd" in item.message and "escapes" in item.message for item in result.diagnostics
    )
    assert any(
        "legacy-sse" in item.message and "unsupported" in item.message
        for item in result.diagnostics
    )
    assert any(
        "bad-headers" in item.message and "duplicate" in item.message for item in result.diagnostics
    )


def test_invalid_mcp_configuration_does_not_hide_package_skills(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    mcp_path = package / "mcp.json"
    mcp_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")

    result = discover_agent_plugins(str(tmp_path))
    assert len(result.packages) == 1
    assert [skill.name for skill in result.packages[0].skills] == ["summarize"]
    assert result.packages[0].servers == []
    assert len(result.diagnostics) == 1
    assert "mcp.json" in result.diagnostics[0].message


def test_invalid_plugin_skill_does_not_hide_valid_siblings_or_mcp_servers(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path)
    invalid_skill = package / "skills" / "bad-skill"
    invalid_skill.mkdir()
    (invalid_skill / "SKILL.md").write_text(
        "---\nname: mismatch\ndescription: Invalid package skill.\n---\nIgnored body.\n",
        encoding="utf-8",
    )

    result = discover_agent_plugins(str(tmp_path))
    assert len(result.packages) == 1
    summary = result.packages[0]
    assert [skill.name for skill in summary.skills] == ["summarize"]
    assert [server.name for server in summary.servers] == ["local-helper", "remote-helper"]
    assert len(result.diagnostics) == 1
    assert "bad-skill" in result.diagnostics[0].message


def test_invalid_skills_component_does_not_hide_plugin_mcp_servers(tmp_path: Path) -> None:
    package = make_package(tmp_path)
    skills_root = package / "skills"
    skills_root.rename(package / "skills-saved")
    skills_root.write_text("not a directory", encoding="utf-8")

    result = discover_agent_plugins(str(tmp_path))
    assert len(result.packages) == 1
    summary = result.packages[0]
    assert summary.skills == []
    assert [server.name for server in summary.servers] == ["local-helper", "remote-helper"]
    assert any("skills component is not a directory" in item.message for item in result.diagnostics)


def test_discovery_and_import_accept_standalone_hermes_skill_directory(tmp_path: Path) -> None:
    skills_root = tmp_path / "hermes-skills"
    skill_directory = skills_root / "summarize"
    (skill_directory / "references").mkdir(parents=True)
    skill_path = skill_directory / "SKILL.md"
    skill_path.write_text(
        "---\nname: summarize\ndescription: Summarize selected material.\nversion: 0.2.0\n"
        "metadata:\n  hermes:\n    category: writing\n---\n"
        "Use the linked reference only when it helps. [Guide](./references/guide.md)\n",
        encoding="utf-8",
    )
    (skill_directory / "references" / "guide.md").write_text("Reference content", encoding="utf-8")

    discovery = discover_agent_plugins(str(skills_root))
    assert len(discovery.packages) == 1
    package = discovery.packages[0]
    assert package.name == "summarize"
    assert package.version == "0.2.0"
    assert package.skills[0].path == "SKILL.md"
    assert package.skills[0].category == "writing"
    assert "Use the linked reference" not in package.model_dump_json()

    # Selecting the skill directory itself works as well as selecting its catalog root.
    direct = discover_agent_plugins(str(skill_directory))
    assert [item.name for item in direct.packages] == ["summarize"]
    imported = read_plugin_skill(str(skills_root), "summarize", "summarize")
    source_bytes = skill_path.read_bytes()
    assert imported.source_format == "hermes_skill"
    assert imported.package_name == "summarize"
    assert imported.package_version == "0.2.0"
    assert imported.content_sha256 == imported.manifest_sha256
    assert imported.content_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert imported.assets[0]["path"] == "references/guide.md"
    assert imported.assets[0]["content_base64"]
    assert imported.assets[0]["sha256"] == hashlib.sha256(b"Reference content").hexdigest()


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
            "hermes_required_tools": ["workspace.read_file"],
            "hermes_fallback_toolsets": ["browser"],
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
    assert result["hermes_tools_unresolved"] == ["workspace.read_file"]
    assert result["hermes_fallback_toolsets_unresolved"] == ["browser"]
    assert "command_semantics" in result["not_assessed"]
    assert "hermes_tools" in result["not_assessed"]
    assert "hermes_fallback_conditions" in result["not_assessed"]
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
    assert len(result.packages) == 1
    assert result.packages[0].skills == []
    assert any("aliases" in item.message for item in result.diagnostics)


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
    assert len(result.packages) == 1
    assert result.packages[0].skills == []
    assert any("category" in item.message for item in result.diagnostics)


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

    assert len(result.packages) == 1
    assert result.packages[0].skills == []
    assert any("disagree" in item.message for item in result.diagnostics)


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


def test_plugin_archive_install_is_authenticated_bounded_and_inactive(tmp_path: Path) -> None:
    package = make_package(tmp_path / "source")
    archive_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in package.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())
    store = RunStore(tmp_path / "state" / "api.sqlite3")
    app = create_app(FakeProvider(), SecretStr(TOKEN), store=store)
    with TestClient(app) as client:
        assert (
            client.post(
                "/extensions/packages/install", json={"archive_path": str(archive_path)}
            ).status_code
            == 401
        )
        response = client.post(
            "/extensions/packages/install",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"archive_path": str(archive_path)},
        )
        headers = {"Authorization": f"Bearer {TOKEN}"}
        installed_listing = client.get("/extensions/packages/installed", headers=headers)
        assert installed_listing.status_code == 200
        assert installed_listing.json()["packages"][0]["name"] == "sample-package"
    assert response.status_code == 201
    body = response.json()
    installed = Path(body["package"]["path"])
    assert installed == tmp_path / "state" / "agent-plugins" / "sample-package"
    assert body["package"]["skills"][0]["name"] == "summarize"
    assert (installed / "plugin.json").is_file()
    assert not list((tmp_path / "state" / "agent-plugins").glob("install-*"))
    data_file = tmp_path / "state" / "plugin-data" / "sample-package" / "helper" / "state.json"
    data_file.parent.mkdir(parents=True)
    data_file.write_text("preserved", encoding="utf-8")
    integration = MCPRegistry(store).create_server(
        MCPServerSpec(
            name="Package reference",
            command="python",
            plugin_root=str(installed),
            plugin_data=str(data_file.parent),
            plugin_sha256=agent_plugins.plugin_source_sha256(installed),
        )
    )
    with TestClient(app) as client:
        assert (
            client.post(
                "/extensions/packages/remove", json={"package_name": "sample-package"}
            ).status_code
            == 401
        )
        blocked = client.post(
            "/extensions/packages/remove",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"package_name": "sample-package"},
        )
        blocked_data = client.post(
            "/extensions/packages/data/remove",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"package_name": "sample-package"},
        )
        assert blocked.status_code == 409
        assert blocked_data.status_code == 409
    assert installed.exists()
    assert MCPRegistry(store).delete_server(integration.id)
    with TestClient(app) as client:
        removed = client.post(
            "/extensions/packages/remove",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"package_name": "sample-package"},
        )
        listing = client.get(
            "/extensions/packages/installed", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        retained = client.get(
            "/extensions/packages/data", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        unauthenticated_data_removal = client.post(
            "/extensions/packages/data/remove", json={"package_name": "sample-package"}
        )
    assert data_file.read_text(encoding="utf-8") == "preserved"
    with TestClient(app) as client:
        removed_data = client.post(
            "/extensions/packages/data/remove",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"package_name": "sample-package"},
        )
        retained_after_removal = client.get(
            "/extensions/packages/data", headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert removed.status_code == 200
    assert removed.json() == {"removed": True, "package_name": "sample-package"}
    assert not installed.exists()
    assert listing.json()["packages"] == []
    assert retained.json()["package_names"] == ["sample-package"]
    assert unauthenticated_data_removal.status_code == 401
    assert removed_data.status_code == 200
    assert removed_data.json() == {"removed": True, "package_name": "sample-package"}
    assert not data_file.parent.parent.exists()
    assert retained_after_removal.json()["package_names"] == []


def test_plugin_archive_install_rejects_traversal_and_existing_package(tmp_path: Path) -> None:
    malicious = tmp_path / "traversal.zip"
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("plugin.json", '{"name":"sample-package","version":"1"}')
        archive.writestr("../outside.txt", "escaped")
    with pytest.raises(ValueError, match="unsafe"):
        agent_plugins.install_agent_plugin_archive(str(malicious), tmp_path / "managed")
    assert not (tmp_path / "outside.txt").exists()

    package = make_package(tmp_path / "source")
    valid = tmp_path / "valid.zip"
    with zipfile.ZipFile(valid, "w") as archive:
        for path in package.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(package).as_posix())
    installed = agent_plugins.install_agent_plugin_archive(str(valid), tmp_path / "managed")
    with pytest.raises(FileExistsError, match="already installed"):
        agent_plugins.install_agent_plugin_archive(str(valid), tmp_path / "managed")
    assert Path(installed.package.path).is_dir()


def test_plugin_mcp_server_import_stores_secrets_in_vault_and_stays_disconnected(
    tmp_path: Path,
) -> None:
    make_package(tmp_path)

    class FakeCredentials:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get(self, profile: str = "lm-studio") -> str | None:
            return self.values.get(profile)

        def set(self, token: str, profile: str = "lm-studio") -> None:
            self.values[profile] = token

        def delete(self, profile: str = "lm-studio") -> None:
            self.values.pop(profile, None)

    store = RunStore(tmp_path / "package-mcp.sqlite3")
    credentials = FakeCredentials()
    app = create_app(
        FakeProvider(),
        SecretStr(TOKEN),
        store=store,
        credential_store=credentials,  # type: ignore[arg-type]
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        response = client.post(
            "/extensions/packages/import-mcp-server",
            headers=headers,
            json={
                "root": str(tmp_path),
                "package_name": "sample-package",
                "server_name": "local-helper",
            },
        )
        listed = client.get("/mcp/servers", headers=headers)

    assert response.status_code == 201
    assert response.json()["state"] == "disconnected"
    assert "secret-value" not in response.text
    assert credentials.values[f"mcp:{response.json()['id']}:API_TOKEN"] == "secret-value"
    assert "secret-value" not in listed.text
    assert "plugin_sha256" not in listed.text
    saved = MCPRegistry(store).get_server(response.json()["id"])
    assert saved is not None
    assert saved.plugin_root == str((tmp_path / "sample").resolve())
    assert saved.plugin_root is not None
    assert saved.plugin_sha256 == plugin_source_sha256(Path(saved.plugin_root))
    assert "plugin_sha256" not in response.text
    assert saved.secret_env == ["API_TOKEN"]
    assert saved.args == ["server.py", "secret-argument"]


def test_plugin_http_server_import_vaults_header_values_and_omits_them_from_storage(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path)
    config_path = package / "mcp.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["mcpServers"]["remote-helper"]["headers"] = {
        "Authorization": "Bearer ${MCP_TOKEN}",
        "X-Workspace-Key": "literal-private-header",
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")

    class FakeCredentials:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}

        def get(self, profile: str = "lm-studio") -> str | None:
            return self.values.get(profile)

        def set(self, token: str, profile: str = "lm-studio") -> None:
            self.values[profile] = token

        def delete(self, profile: str) -> None:
            self.values.pop(profile, None)

    store = RunStore(tmp_path / "package-http-mcp.sqlite3")
    credentials = FakeCredentials()
    app = create_app(
        FakeProvider(),
        SecretStr(TOKEN),
        store=store,
        credential_store=credentials,  # type: ignore[arg-type]
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        response = client.post(
            "/extensions/packages/import-mcp-server",
            headers=headers,
            json={
                "root": str(tmp_path),
                "package_name": "sample-package",
                "server_name": "remote-helper",
            },
        )

    assert response.status_code == 201
    assert "http-secret-value" not in response.text
    assert "literal-private-header" not in response.text
    server = MCPRegistry(store).get_server(response.json()["id"])
    assert server is not None
    assert server.transport == "streamable_http"
    assert server.plugin_headers == {
        "Authorization": "Bearer ${secret:MCP_TOKEN}",
        "X-Workspace-Key": "${secret:XAA_PLUGIN_HEADER_1}",
    }
    assert credentials.values[f"mcp:{server.id}:MCP_TOKEN"] == "http-secret-value"
    assert credentials.values[f"mcp:{server.id}:XAA_PLUGIN_HEADER_1"] == "literal-private-header"
    with store.connection() as connection:
        raw_row = connection.execute(
            "SELECT endpoint,secret_env,plugin_headers FROM mcp_servers WHERE id=?",
            (server.id,),
        ).fetchone()
    assert "http-secret-value" not in str(dict(raw_row))
    assert "literal-private-header" not in str(dict(raw_row))


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
    assert evidence["provenance"] == "agent_plugins_v1"
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
    assert declared["hermes_required_tools"] == ["shell.exec", "workspace.read_file"]
    assert declared["hermes_fallback_toolsets"] == ["browser"]
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
    assert revised_version.json()["evaluation_evidence"]["provenance"] == "agent_plugins_v1"
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


def test_hermes_skill_import_api_keeps_candidate_inactive_and_records_source(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / "hermes-skills"
    skill_directory = skills_root / "summarize"
    skill_directory.mkdir(parents=True)
    (skill_directory / "SKILL.md").write_text(
        "---\nname: summarize\ndescription: Summarize selected material.\nversion: 0.2.0\n"
        "allowed-tools: workspace.read_file\n---\n"
        "Read the relevant material and summarize it.\n"
        "A template URL may look like [Open](url).\n",
        encoding="utf-8",
    )
    app = create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(tmp_path / "hermes.sqlite3"))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = {
        "root": str(skills_root),
        "package_name": "summarize",
        "skill_name": "summarize",
    }
    with TestClient(app) as client:
        created = client.post("/extensions/packages/import-skill", headers=headers, json=payload)
        assert created.status_code == 201
        version = client.get(f"/skills/{created.json()['id']}/versions/1", headers=headers).json()

    assert created.json()["lifecycle_state"] == "candidate"
    assert version["required_permissions"] == ["workspace.read"]
    assert version["evaluation_evidence"]["provenance"] == "hermes_skill"
    assert version["evaluation_evidence"]["source"]["package_version"] == "0.2.0"
    assert version["evaluation_evidence"]["state"] == "candidate"


@pytest.mark.parametrize(
    "link",
    [
        "../outside.txt",
        "%2e%2e/outside.txt",
        "folder/../outside.txt",
        "/etc/passwd",
        "..\\outside.txt",
    ],
)
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
        frontmatter + "x" * (80 * 1024 + 1), encoding="utf-8"
    )
    app = create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(tmp_path / "large.sqlite3"))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = {"root": str(tmp_path), "package_name": "sample-package", "skill_name": "summarize"}
    with TestClient(app) as client:
        response = client.post("/extensions/packages/import-skill", headers=headers, json=payload)
        procedures = client.get("/skills", headers=headers)
    assert response.status_code == 400
    assert "80 KiB" in response.json()["detail"]
    assert procedures.json() == []

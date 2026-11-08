from xaa.runtime.validation_suggestions import suggest_validation_commands
from xaa.runtime.workspace_snapshot import SnapshotFile, WorkspaceSnapshot


def snapshot(*files: tuple[str, bytes]) -> WorkspaceSnapshot:
    directories = {parent for path, _ in files for parent in _parents(path)}
    return WorkspaceSnapshot(
        tuple(SnapshotFile(path, content) for path, content in files),
        tuple(sorted(directories)),
    )


def _parents(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def test_suggests_only_detected_manifests_and_supports_nested_packages():
    result = suggest_validation_commands(
        snapshot(
            ("pyproject.toml", b"[tool.pytest.ini_options]"),
            ("apps/client/package.json", b"{}"),
            ("crates/worker/Cargo.toml", b"[package]"),
            ("docs/guide.md", b"example"),
        )
    )

    assert [item["label"] for item in result] == [
        "Python tests (pytest)",
        "JavaScript tests (npm) — apps/client",
        "Rust tests (Cargo) — crates/worker",
    ]
    assert result[1]["argv"] == [
        "/usr/bin/env",
        "npm",
        "--prefix",
        "apps/client",
        "test",
    ]
    assert result[2]["argv"][-2:] == ["--manifest-path", "crates/worker/Cargo.toml"]
    assert all("confirm" in str(item["rationale"]).casefold() for item in result)


def test_no_manifest_means_no_suggestions():
    assert suggest_validation_commands(snapshot(("README.md", b"Documentation"))) == []


def test_suggests_go_and_nested_dotnet_checks():
    result = suggest_validation_commands(
        snapshot(("go.mod", b"module example"), ("src/App.csproj", b"<Project />"))
    )

    assert result[0]["argv"] == ["/usr/bin/env", "go", "test", "./..."]
    assert result[1]["argv"] == [
        "/usr/bin/env",
        "dotnet",
        "test",
        "src/App.csproj",
        "--no-restore",
    ]


def test_suggests_maven_gradle_and_declared_composer_test_scripts():
    result = suggest_validation_commands(
        snapshot(
            ("services/java/pom.xml", b"<project />"),
            ("apps/android/build.gradle.kts", b"plugins {}"),
            ("apps/php/composer.json", b'{"scripts":{"test":"phpunit"}}'),
            ("apps/php2/composer.json", b'{"require":{"php":"^8.3"}}'),
            ("apps/bad/composer.json", b"not-json"),
        )
    )

    assert [item["label"] for item in result] == [
        "Java tests (Maven) — services/java",
        "Java tests (Gradle) — apps/android",
        "PHP tests (Composer) — apps/php",
    ]
    assert result[0]["argv"] == [
        "/usr/bin/env",
        "mvn",
        "-B",
        "-o",
        "-f",
        "services/java/pom.xml",
        "test",
    ]
    assert result[1]["argv"] == [
        "/usr/bin/env",
        "gradle",
        "--offline",
        "--project-dir",
        "apps/android",
        "--build-file",
        "build.gradle.kts",
        "test",
    ]
    assert result[2]["argv"] == [
        "/usr/bin/env",
        "composer",
        "--working-dir",
        "apps/php",
        "run-script",
        "test",
        "--no-interaction",
    ]


def test_suggests_swift_package_tests_at_root_and_nested_paths():
    result = suggest_validation_commands(
        snapshot(
            ("Package.swift", b"// swift-tools-version: 5.9"),
            ("apps/ios/Package.swift", b"// swift-tools-version: 6.0"),
        )
    )

    assert [item["label"] for item in result] == [
        "Swift tests (Swift Package Manager) — apps/ios",
        "Swift tests (Swift Package Manager)",
    ]
    assert result[0]["argv"] == [
        "/usr/bin/env",
        "swift",
        "test",
        "--package-path",
        "apps/ios",
    ]
    assert result[1]["argv"] == ["/usr/bin/env", "swift", "test"]


def test_suggests_every_supported_manifest_in_a_monorepo():
    result = suggest_validation_commands(
        snapshot(
            ("apps/one/package.json", b"{}"),
            ("apps/two/package.json", b"{}"),
            ("services/one/pyproject.toml", b"[project]"),
            ("services/two/pyproject.toml", b"[project]"),
            ("crates/one/Cargo.toml", b"[package]"),
            ("crates/two/Cargo.toml", b"[package]"),
            ("services/one/go.mod", b"module one"),
            ("services/two/go.mod", b"module two"),
            ("dotnet/one.sln", b""),
            ("dotnet/two.sln", b""),
            ("ios/Package.swift", b"// swift-tools-version: 6.0"),
        )
    )

    assert len(result) == 11
    assert len({tuple(item["argv"]) for item in result}) == 11
    assert any("services/two" in str(item["argv"]) for item in result)

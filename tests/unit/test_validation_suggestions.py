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
            ("apps/client/package.json", b'{"scripts":{"test":"vitest run"}}'),
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


def test_ignores_npm_init_placeholder_test_script():
    result = suggest_validation_commands(
        snapshot(
            (
                "package.json",
                b'{"scripts":{"test":"echo \\"Error: no test specified\\" && exit 1"}}',
            ),
        )
    )

    assert result == []


def test_suggests_declared_cmake_test_workflows_only():
    result = suggest_validation_commands(
        snapshot(
            ("CMakeLists.txt", b"project(example)"),
            (
                "CMakePresets.json",
                (
                    b'{"version":6,"configurePresets":[{"name":"ci"},{"name":"other"}],'
                    b'"buildPresets":[{"name":"ci","configurePreset":"ci"},'
                    b'{"name":"other","configurePreset":"other"}],'
                    b'"testPresets":[{"name":"ci","configurePreset":"ci"},'
                    b'{"name":"other","configurePreset":"other"}],'
                    b'"workflowPresets":['
                    b'{"name":"ci","steps":[{"type":"configure","name":"ci"},'
                    b'{"type":"build","name":"ci"},{"type":"test","name":"ci"}]},'
                    b'{"name":"mismatched","steps":[{"type":"configure","name":"ci"},'
                    b'{"type":"build","name":"other"},{"type":"test","name":"other"}]},'
                    b'{"name":"configure-only","steps":[{"type":"configure","name":"ci"}]}]}'
                ),
            ),
        )
    )

    assert result == [
        {
            "label": "CMake tests (workflow: ci)",
            "rationale": (
                "CMakePresets.json declares a workflow with configure, build, and test steps. "
                "Confirm CMake and the project's dependencies are available in the restricted "
                "network-disabled WSL environment."
            ),
            "argv": ["/usr/bin/env", "cmake", "--workflow", "--preset", "ci"],
        }
    ]


def test_python_suggestions_require_real_pytest_configuration_and_deduplicate_projects():
    result = suggest_validation_commands(
        snapshot(
            ("pyproject.toml", b"[project]\nname = 'application'\n"),
            ("tests/pyproject.toml", b"[project]\nname = 'unrelated'\n"),
            ("tests/setup.cfg", b"[metadata]\nname = unrelated\n"),
            ("qa/pytest.ini", b""),
            ("qa/pyproject.toml", b"[tool.pytest.ini_options]\ntestpaths = ['tests']\n"),
            ("qa/setup.cfg", b"[tool:pytest]\naddopts = -q\n"),
            ("service/tox.ini", b"[pytest]\ntestpaths = tests\n"),
            ("tools/.pytest.toml", b"[pytest]\n"),
            ("broken/tox.ini", b"[testenv]\ncommands = pytest\n"),
            ("bad/pyproject.toml", b"[tool.pytest.ini_options\n"),
        )
    )

    assert [item["label"] for item in result] == [
        "Python tests (pytest) — qa",
        "Python tests (pytest) — service",
        "Python tests (pytest) — tools",
    ]
    assert result[0]["rationale"].startswith("qa/pytest.ini contains pytest configuration")
    assert result[0]["argv"] == ["/usr/bin/python3", "-I", "-m", "pytest", "-q", "qa"]


def test_javascript_suggestions_require_a_declared_test_script():
    result = suggest_validation_commands(
        snapshot(
            ("apps/valid/package.json", b'{"scripts":{"test":"vitest run"}}'),
            ("apps/missing/package.json", b'{"scripts":{"build":"vite build"}}'),
            ("apps/empty/package.json", b'{"scripts":{"test":"  "}}'),
            ("apps/wrong-type/package.json", b'{"scripts":{"test":true}}'),
            ("apps/malformed/package.json", b"not-json"),
        )
    )

    assert [item["label"] for item in result] == ["JavaScript tests (npm) — apps/valid"]
    assert result[0]["argv"] == ["/usr/bin/env", "npm", "--prefix", "apps/valid", "test"]


def test_deno_suggestions_require_an_explicit_test_task_and_support_jsonc():
    result = suggest_validation_commands(
        snapshot(
            (
                "deno.jsonc",
                (
                    b"{\n"
                    b"  // Deno configuration comments and trailing commas are valid.\n"
                    b'  "imports": {"example": "https://example.test/a//b",},\n'
                    b'  "tasks": {"test": "deno test",},\n'
                    b"}\n"
                ),
            ),
            ("services/api/deno.json", b'{"tasks":{"test":{"command":"deno test"}}}'),
            ("services/no-test/deno.json", b'{"tasks":{"check":"deno check ."}}'),
            ("services/blank/deno.json", b'{"tasks":{"test":"  "}}'),
            ("services/malformed/deno.jsonc", b'{"tasks": /* unfinished comment */'),
            ("services/ambiguous/deno.json", b'{"tasks":{"test":"deno test"}}'),
            ("services/ambiguous/deno.jsonc", b'{"tasks":{"test":"deno test --doc"}}'),
        )
    )

    assert [item["label"] for item in result] == [
        "Deno tests (task)",
        "Deno tests (task) — services/api",
    ]
    assert result[0]["argv"] == [
        "/usr/bin/env",
        "deno",
        "task",
        "--config",
        "deno.jsonc",
        "test",
    ]
    assert result[1]["argv"][-3:] == ["--config", "services/api/deno.json", "test"]


def test_javascript_suggestions_follow_declared_managers_and_local_lockfiles():
    result = suggest_validation_commands(
        snapshot(
            (
                "apps/npm/package.json",
                b'{"scripts":{"test":"vitest"},"packageManager":"npm@10.0.0"}',
            ),
            (
                "apps/pnpm/package.json",
                b'{"scripts":{"test":"vitest"},"packageManager":"pnpm@9.0.0"}',
            ),
            (
                "apps/yarn/package.json",
                b'{"scripts":{"test":"jest"},"packageManager":"yarn@4.0.0"}',
            ),
            (
                "apps/bun/package.json",
                b'{"scripts":{"test":"bun test"},"packageManager":"bun@1.1.0"}',
            ),
            ("apps/locked/package.json", b'{"scripts":{"test":"vitest"}}'),
            ("apps/locked/pnpm-lock.yaml", b"lockfileVersion: '9.0'"),
            ("apps/ambiguous/package.json", b'{"scripts":{"test":"vitest"}}'),
            ("apps/ambiguous/package-lock.json", b"{}"),
            ("apps/ambiguous/yarn.lock", b""),
        )
    )

    assert [item["label"] for item in result] == [
        "JavaScript tests (bun) — apps/bun",
        "JavaScript tests (pnpm) — apps/locked",
        "JavaScript tests (npm) — apps/npm",
        "JavaScript tests (pnpm) — apps/pnpm",
        "JavaScript tests (yarn) — apps/yarn",
    ]
    assert [item["argv"] for item in result] == [
        ["/usr/bin/env", "bun", "run", "--cwd", "apps/bun", "test"],
        ["/usr/bin/env", "pnpm", "--dir", "apps/locked", "test"],
        ["/usr/bin/env", "npm", "--prefix", "apps/npm", "test"],
        ["/usr/bin/env", "pnpm", "--dir", "apps/pnpm", "test"],
        ["/usr/bin/env", "yarn", "--cwd", "apps/yarn", "test"],
    ]


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
            ("apps/php-array/composer.json", b'{"scripts":{"test":["phpunit", "phpstan"]}}'),
            ("apps/php2/composer.json", b'{"require":{"php":"^8.3"}}'),
            ("apps/php-blank/composer.json", b'{"scripts":{"test":" "}}'),
            ("apps/php-wrong-type/composer.json", b'{"scripts":{"test":true}}'),
            ("apps/bad/composer.json", b"not-json"),
        )
    )

    assert [item["label"] for item in result] == [
        "Java tests (Maven) — services/java",
        "Java tests (Gradle) — apps/android",
        "PHP tests (Composer) — apps/php-array",
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
        "apps/php-array",
        "run-script",
        "test",
        "--no-interaction",
    ]
    assert result[3]["argv"] == [
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


def test_suggests_rake_only_for_explicit_test_tasks_and_locked_bundles():
    result = suggest_validation_commands(
        snapshot(
            ("Rakefile", b'task :test do\n  puts "testing"\nend\n'),
            ("apps/locked/Gemfile", b'source "https://rubygems.org"\n'),
            ("apps/locked/Gemfile.lock", b"GEM\n  specs:\n"),
            ("apps/locked/Rakefile", b"Rake::TestTask.new(:test)\n"),
            ("apps/unlocked/Gemfile", b'source "https://rubygems.org"\n'),
            ("apps/unlocked/Rakefile", b"task :test\n"),
            ("apps/comment-only/Rakefile", b"# task :test\n"),
            ("apps/no-test/Rakefile", b"task :build\n"),
        )
    )

    assert [item["label"] for item in result] == [
        "Ruby tests (Rake) — apps/locked",
        "Ruby tests (Rake)",
    ]
    assert result[0]["argv"] == [
        "/usr/bin/env",
        "BUNDLE_GEMFILE=apps/locked/Gemfile",
        "bundle",
        "exec",
        "rake",
        "--rakefile",
        "apps/locked/Rakefile",
        "test",
    ]
    assert result[1]["argv"] == ["/usr/bin/env", "rake", "--rakefile", "Rakefile", "test"]


def test_suggests_make_only_for_explicit_test_target_recipes():
    result = suggest_validation_commands(
        snapshot(
            ("Makefile", b".PHONY: test\ntest: unit\n\t@python3 -m unittest\n"),
            ("native/GNUmakefile", b"test:; ctest --test-dir build\n"),
            ("native/Makefile", b"test:\n\tfalse\n"),
            ("services/lower/makefile", b"test:\n\tgo test ./...\n"),
            ("services/api/Makefile", b"all: test\n\ntest check:\n\tgo test ./...\n"),
            ("apps/no-recipe/Makefile", b".PHONY: test\ntest:\n\ncheck:\n\ttrue\n"),
            (
                "apps/comment-only/Makefile",
                b"# test: ignored\n.PHONY: test\ntest:\n\t# no test command\n",
            ),
            ("apps/substring/Makefile", b"testing:\n\ttrue\n"),
            ("apps/malformed/Makefile", b"\xff"),
        )
    )

    assert [item["label"] for item in result] == [
        "Make tests",
        "Make tests — native",
        "Make tests — services/api",
        "Make tests — services/lower",
    ]
    assert result[0]["argv"] == [
        "/usr/bin/env",
        "make",
        "--no-print-directory",
        "-f",
        "Makefile",
        "test",
    ]
    assert result[1]["argv"] == [
        "/usr/bin/env",
        "make",
        "--no-print-directory",
        "-C",
        "native",
        "-f",
        "GNUmakefile",
        "test",
    ]
    assert result[2]["argv"] == [
        "/usr/bin/env",
        "make",
        "--no-print-directory",
        "-C",
        "services/api",
        "-f",
        "Makefile",
        "test",
    ]
    assert result[3]["argv"][-3:] == ["-f", "makefile", "test"]


def test_suggests_every_supported_manifest_in_a_monorepo():
    result = suggest_validation_commands(
        snapshot(
            ("apps/one/package.json", b'{"scripts":{"test":"vitest run"}}'),
            ("apps/two/package.json", b'{"scripts":{"test":"jest"}}'),
            ("services/one/pyproject.toml", b"[tool.pytest]\ntestpaths = ['tests']\n"),
            ("services/two/pyproject.toml", b"[tool.pytest.ini_options]\naddopts = '-q'\n"),
            ("crates/one/Cargo.toml", b"[package]"),
            ("crates/two/Cargo.toml", b"[package]"),
            ("services/one/go.mod", b"module one"),
            ("services/two/go.mod", b"module two"),
            ("dotnet/one.sln", b""),
            ("dotnet/two.sln", b""),
            ("ios/Package.swift", b"// swift-tools-version: 6.0"),
            ("apps/deno/deno.jsonc", b'{"tasks":{"test":"deno test"}}'),
        )
    )

    assert len(result) == 12
    assert len({tuple(item["argv"]) for item in result}) == 12
    assert any("services/two" in str(item["argv"]) for item in result)
    assert any("apps/deno" in str(item["argv"]) for item in result)

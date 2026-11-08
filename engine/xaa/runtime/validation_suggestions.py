"""Suggest user-reviewable checks from a captured project snapshot."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import cast

from xaa.runtime.workspace_snapshot import WorkspaceSnapshot

_JS_PACKAGE_MANAGERS = {
    "npm": ("test",),
    "pnpm": ("test",),
    "yarn": ("test",),
    "bun": ("run", "test"),
}
_JS_LOCKFILES = {
    "package-lock.json": "npm",
    "npm-shrinkwrap.json": "npm",
    "pnpm-lock.yaml": "pnpm",
    "yarn.lock": "yarn",
    "bun.lock": "bun",
    "bun.lockb": "bun",
}
_RAKE_TEST_TASK = re.compile(r"(?m)^\s*(?:Rake::TestTask\.new\b|task\s+(?::test\b|['\"]test['\"]))")


def _javascript_package_manager(
    package: str, manifest: dict[str, object], paths: dict[str, str]
) -> str | None:
    declared = manifest.get("packageManager")
    if isinstance(declared, str):
        match = re.match(r"^(npm|pnpm|yarn|bun)@[^\s]+$", declared)
        if match:
            return match.group(1)
        if declared:
            return None

    package_dir = PurePosixPath(package).parent
    ancestors = [package_dir, *package_dir.parents]
    for directory in ancestors:
        lockfile_managers = {
            manager
            for filename, manager in _JS_LOCKFILES.items()
            if (directory / filename).as_posix().casefold() in paths
        }
        if len(lockfile_managers) == 1:
            return next(iter(lockfile_managers))
        if len(lockfile_managers) > 1:
            return None
    return "npm"


def _javascript_test_argv(manager: str, package: str) -> list[str]:
    prefix = package.rpartition("/")[0]
    if manager == "npm":
        argv = ["/usr/bin/env", "npm"]
        if prefix:
            argv.extend(["--prefix", prefix])
        return [*argv, *_JS_PACKAGE_MANAGERS[manager]]
    argv = ["/usr/bin/env", manager]
    if manager == "bun":
        argv.append("run")
        if prefix:
            argv.extend(["--cwd", prefix])
        argv.append("test")
        return argv
    if manager == "pnpm" and prefix:
        argv.extend(["--dir", prefix])
    elif manager == "yarn" and prefix:
        argv.extend(["--cwd", prefix])
    argv.extend(_JS_PACKAGE_MANAGERS[manager])
    return argv


def suggest_validation_commands(snapshot: WorkspaceSnapshot) -> list[dict[str, object]]:
    """Return deterministic command presets inferred from project manifests.

    Suggestions are hints only. A sole suggestion may be dispatched by final
    review under the run's existing restricted-command grant; its evidence still
    requires human inspection and selection. Every supported manifest in the
    bounded snapshot is represented so a monorepo is not mistaken for a single
    package by the automatic dispatcher.
    """
    paths = {entry.path.casefold(): entry.path for entry in snapshot.files}
    suggestions: list[dict[str, object]] = []

    python_configs = [
        path
        for key, path in paths.items()
        if PurePosixPath(key).name in {"pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"}
    ]
    for config in sorted(python_configs, key=str.casefold):
        directory = str(PurePosixPath(config).parent)
        test_path = [] if directory == "." else [directory]
        suffix = "" if directory == "." else f" — {directory}"
        suggestions.append(
            {
                "label": f"Python tests (pytest){suffix}",
                "rationale": f"{config} was found. Confirm pytest is available in the restricted WSL environment.",
                "argv": ["/usr/bin/python3", "-I", "-m", "pytest", "-q", *test_path],
            }
        )

    packages = [path for key, path in paths.items() if PurePosixPath(key).name == "package.json"]
    for package in sorted(packages, key=str.casefold):
        try:
            content = next(
                entry.content
                for entry in snapshot.files
                if entry.path.casefold() == package.casefold()
            )
            parsed: object = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, dict):
            continue
        manifest = cast(dict[str, object], parsed)
        scripts = manifest.get("scripts")
        if not isinstance(scripts, dict):
            continue
        test_script = cast(dict[str, object], scripts).get("test")
        if not isinstance(test_script, str) or not test_script.strip():
            continue
        manager = _javascript_package_manager(package, manifest, paths)
        if manager is None:
            continue
        argv = _javascript_test_argv(manager, package)
        prefix = package.rpartition("/")[0]
        suffix = "" if not prefix else f" — {prefix}"
        suggestions.append(
            {
                "label": f"JavaScript tests ({manager}){suffix}",
                "rationale": f"{package} declares a test script. Confirm {manager} and dependencies are available in the restricted WSL environment.",
                "argv": argv,
            }
        )

    maven_projects = [path for key, path in paths.items() if PurePosixPath(key).name == "pom.xml"]
    for pom in sorted(maven_projects, key=str.casefold):
        suggestions.append(
            {
                "label": f"Java tests (Maven) — {PurePosixPath(pom).parent}",
                "rationale": f"{pom} was found. Offline mode prevents dependency downloads; confirm Maven and all required artifacts are available in the restricted WSL environment.",
                "argv": ["/usr/bin/env", "mvn", "-B", "-o", "-f", pom, "test"],
            }
        )

    gradle_files = [
        path
        for key, path in paths.items()
        if PurePosixPath(key).name in {"build.gradle", "build.gradle.kts"}
    ]
    for build_file in sorted(gradle_files, key=str.casefold):
        directory = str(PurePosixPath(build_file).parent)
        argv = ["/usr/bin/env", "gradle", "--offline"]
        if directory != ".":
            argv.extend(["--project-dir", directory])
        argv.extend(["--build-file", PurePosixPath(build_file).name, "test"])
        suggestions.append(
            {
                "label": f"Java tests (Gradle) — {directory}",
                "rationale": f"{build_file} was found. Offline mode prevents dependency downloads; confirm Gradle and all required artifacts are available in the restricted WSL environment.",
                "argv": argv,
            }
        )

    composer_projects = [
        path for key, path in paths.items() if PurePosixPath(key).name == "composer.json"
    ]
    for composer_file in sorted(composer_projects, key=str.casefold):
        try:
            content = next(
                entry.content
                for entry in snapshot.files
                if entry.path.casefold() == composer_file.casefold()
            )
            parsed: object = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, dict):
            continue
        manifest = cast(dict[str, object], parsed)
        scripts = manifest.get("scripts")
        if not isinstance(scripts, dict):
            continue
        test_script = cast(dict[str, object], scripts).get("test")
        valid_test_script = isinstance(test_script, str) and bool(test_script.strip())
        valid_test_script_list = False
        if isinstance(test_script, list):
            commands = cast(list[object], test_script)
            valid_test_script_list = len(commands) > 0 and all(
                isinstance(command, str) and bool(command.strip()) for command in commands
            )
        if not (valid_test_script or valid_test_script_list):
            continue
        directory = str(PurePosixPath(composer_file).parent)
        argv = ["/usr/bin/env", "composer"]
        if directory != ".":
            argv.extend(["--working-dir", directory])
        argv.extend(["run-script", "test", "--no-interaction"])
        suggestions.append(
            {
                "label": f"PHP tests (Composer) — {directory}",
                "rationale": f"{composer_file} defines a test script. Confirm Composer and its dependencies are available in the restricted WSL environment.",
                "argv": argv,
            }
        )

    cargo_files = [path for key, path in paths.items() if PurePosixPath(key).name == "cargo.toml"]
    for cargo in sorted(cargo_files, key=str.casefold):
        argv = ["/usr/bin/env", "cargo", "test", "--locked", "--offline"]
        if cargo.count("/"):
            argv.extend(["--manifest-path", cargo])
        suffix = "" if not cargo.count("/") else f" — {PurePosixPath(cargo).parent}"
        suggestions.append(
            {
                "label": f"Rust tests (Cargo){suffix}",
                "rationale": f"{cargo} was found. Offline mode prevents dependency downloads; confirm the required toolchain and crates are already available.",
                "argv": argv,
            }
        )

    swift_packages = [
        path for key, path in paths.items() if PurePosixPath(key).name == "package.swift"
    ]
    for manifest in sorted(swift_packages, key=str.casefold):
        directory = str(PurePosixPath(manifest).parent)
        argv = ["/usr/bin/env", "swift", "test"]
        suffix = ""
        if directory != ".":
            argv.extend(["--package-path", directory])
            suffix = f" — {directory}"
        suggestions.append(
            {
                "label": f"Swift tests (Swift Package Manager){suffix}",
                "rationale": f"{manifest} was found. Confirm Swift and any required dependencies are available in the restricted WSL environment.",
                "argv": argv,
            }
        )

    rakefiles = [path for key, path in paths.items() if PurePosixPath(key).name == "rakefile"]
    for rakefile in sorted(rakefiles, key=str.casefold):
        try:
            content = next(
                entry.content
                for entry in snapshot.files
                if entry.path.casefold() == rakefile.casefold()
            )
            rake_source = content.decode("utf-8")
        except (StopIteration, UnicodeDecodeError):
            continue
        executable_lines = "\n".join(
            line for line in rake_source.splitlines() if not line.lstrip().startswith("#")
        )
        if not _RAKE_TEST_TASK.search(executable_lines):
            continue

        directory = PurePosixPath(rakefile).parent
        ancestors = (directory, *directory.parents)
        gemfile = next(
            (
                paths[(ancestor / "Gemfile").as_posix().casefold()]
                for ancestor in ancestors
                if (ancestor / "Gemfile").as_posix().casefold() in paths
            ),
            None,
        )
        argv = ["/usr/bin/env"]
        if gemfile is not None:
            if f"{gemfile}.lock".casefold() not in paths:
                continue
            argv.extend([f"BUNDLE_GEMFILE={gemfile}", "bundle", "exec"])
        argv.extend(["rake", "--rakefile", rakefile, "test"])
        suffix = "" if str(directory) == "." else f" — {directory}"
        suggestions.append(
            {
                "label": f"Ruby tests (Rake){suffix}",
                "rationale": f"{rakefile} explicitly declares a test task. Confirm Ruby/Rake and any locked dependencies are available in the restricted network-disabled WSL environment.",
                "argv": argv,
            }
        )

    go_modules = [path for key, path in paths.items() if PurePosixPath(key).name == "go.mod"]
    for module in sorted(go_modules, key=str.casefold):
        directory = str(PurePosixPath(module).parent)
        argv = ["/usr/bin/env", "go"]
        if directory != ".":
            argv.extend(["-C", directory])
        argv.extend(["test", "./..."])
        suffix = "" if directory == "." else f" — {directory}"
        suggestions.append(
            {
                "label": f"Go tests{suffix}",
                "rationale": f"{module} was found. Confirm Go is available and dependencies are cached because network access is disabled.",
                "argv": argv,
            }
        )

    solutions = [path for path in paths.values() if path.casefold().endswith((".sln", ".slnx"))]
    projects = (
        solutions
        if solutions
        else [path for path in paths.values() if path.casefold().endswith(".csproj")]
    )
    for project in sorted(projects, key=str.casefold):
        argv = ["/usr/bin/env", "dotnet", "test", project, "--no-restore"]
        suffix = f" — {PurePosixPath(project).parent}"
        suggestions.append(
            {
                "label": f".NET tests{suffix}",
                "rationale": f"{project} was found. Confirm the .NET SDK and restored packages are available in the restricted WSL environment.",
                "argv": argv,
            }
        )

    return suggestions

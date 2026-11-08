"""Suggest user-reviewable checks from a captured project snapshot."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import cast

from xaa.runtime.workspace_snapshot import WorkspaceSnapshot


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
        prefix = package.rpartition("/")[0]
        argv = ["/usr/bin/env", "npm", "test"]
        if prefix:
            argv[2:2] = ["--prefix", prefix]
        suffix = "" if not prefix else f" — {prefix}"
        suggestions.append(
            {
                "label": f"JavaScript tests (npm){suffix}",
                "rationale": f"{package} was found. This runs the package's configured test script; confirm npm and dependencies are available in the restricted WSL environment.",
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
        if not isinstance(scripts, dict) or "test" not in cast(dict[str, object], scripts):
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

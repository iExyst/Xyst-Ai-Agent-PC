"""Suggest user-reviewable checks from a captured project snapshot."""

from __future__ import annotations

from pathlib import PurePosixPath

from xaa.runtime.workspace_snapshot import WorkspaceSnapshot


def suggest_validation_commands(snapshot: WorkspaceSnapshot) -> list[dict[str, object]]:
    """Return deterministic command presets inferred from project manifests.

    The suggestions are hints only. They are never executed until a user reviews
    the exact argv and starts a restricted project validation from the UI. Every
    supported manifest in the bounded snapshot is represented so a monorepo is
    not mistaken for a single-package project by the automatic dispatcher.
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

"""Suggest user-reviewable checks from a captured project snapshot."""

from __future__ import annotations

from xaa.runtime.workspace_snapshot import WorkspaceSnapshot


def suggest_validation_commands(snapshot: WorkspaceSnapshot) -> list[dict[str, object]]:
    """Return bounded command presets inferred from root project manifests.

    The suggestions are hints only. They are never executed until a user reviews
    the exact argv and starts a restricted project validation from the UI.
    """
    paths = {entry.path.casefold(): entry.path for entry in snapshot.files}
    suggestions: list[dict[str, object]] = []

    if "pyproject.toml" in paths or "pytest.ini" in paths or "tox.ini" in paths:
        suggestions.append(
            {
                "label": "Python tests (pytest)",
                "rationale": "Python project configuration was found at the workspace root. Confirm pytest is available in the restricted WSL environment.",
                "argv": ["/usr/bin/python3", "-I", "-m", "pytest", "-q"],
            }
        )

    package = next(
        (
            path
            for path in paths.values()
            if path.casefold() == "package.json"
            or (path.casefold().endswith("/package.json") and path.count("/") <= 2)
        ),
        None,
    )
    if package is not None:
        prefix = package.rpartition("/")[0]
        argv = ["/usr/bin/env", "npm", "test"]
        if prefix:
            argv[2:2] = ["--prefix", prefix]
        suggestions.append(
            {
                "label": "JavaScript tests (npm)",
                "rationale": f"{package} was found. This runs the package's configured test script; confirm npm and dependencies are available in the restricted WSL environment.",
                "argv": argv,
            }
        )

    cargo = next(
        (
            path
            for path in paths.values()
            if path.casefold() == "cargo.toml"
            or (path.casefold().endswith("/cargo.toml") and path.count("/") <= 2)
        ),
        None,
    )
    if cargo is not None:
        argv = ["/usr/bin/env", "cargo", "test", "--locked", "--offline"]
        if cargo.count("/"):
            argv.extend(["--manifest-path", cargo])
        suggestions.append(
            {
                "label": "Rust tests (Cargo)",
                "rationale": f"{cargo} was found. Offline mode prevents dependency downloads; confirm the required toolchain and crates are already available.",
                "argv": argv,
            }
        )

    if "go.mod" in paths:
        suggestions.append(
            {
                "label": "Go tests",
                "rationale": "A Go module was found at the workspace root. Confirm Go is available and dependencies are cached because network access is disabled.",
                "argv": ["/usr/bin/env", "go", "test", "./..."],
            }
        )

    project = next(
        (path for path in paths.values() if path.casefold().endswith((".sln", ".slnx", ".csproj"))),
        None,
    )
    if project is not None:
        argv = ["/usr/bin/env", "dotnet", "test", project, "--no-restore"]
        suggestions.append(
            {
                "label": ".NET tests",
                "rationale": f"{project} was found. Confirm the .NET SDK and restored packages are available in the restricted WSL environment.",
                "argv": argv,
            }
        )

    return suggestions[:8]

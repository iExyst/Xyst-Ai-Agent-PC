"""User-granted command profiles and model arguments are separate authority domains."""

import sys
from pathlib import Path, PureWindowsPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CommandGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    backend: Literal["wsl-bubblewrap-v1"] = "wsl-bubblewrap-v1"
    distribution: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_seconds: int = Field(default=30, ge=1, le=30)


class CommandArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    argv: list[str] = Field(min_length=1, max_length=64)
    max_seconds: int = Field(default=10, ge=1, le=30)

    @model_validator(mode="after")
    def bounded_arguments(self) -> "CommandArguments":
        if not self.argv[0] or any("\0" in value for value in self.argv):
            raise ValueError("Command arguments must include a program and contain no NUL.")
        if sum(len(value.encode("utf-8")) for value in self.argv) > 16384:
            raise ValueError("Command arguments exceed 16 KiB.")
        return self


class CommandIntent(CommandArguments):
    execution_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    backend: Literal["wsl-bubblewrap-v1"]
    distribution: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    profile_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    workspace: str


def command_workspace(workspace: str | None, database: Path, mode: str) -> Path:
    if mode != "workspace" or not workspace:
        raise PermissionError(
            "Commands need a selected workspace and an explicit workspace-mode grant."
        )
    root = Path(workspace).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Command workspace must be an existing directory.")
    protected = [
        database.resolve().parent,
        Path(__file__).resolve().parents[1],
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
    ]
    if getattr(sys, "frozen", False):
        protected.append(Path(sys.executable).resolve().parent)
        extracted = getattr(sys, "_MEIPASS", None)
        if extracted:
            protected.append(Path(extracted).resolve())
    for location in protected:
        if root.is_relative_to(location) or location.is_relative_to(root):
            raise PermissionError(
                "Keep command workspaces separate from live application code and data."
            )
    windows = PureWindowsPath(str(root))
    if windows.drive.casefold().startswith(("\\\\wsl$\\", "\\\\wsl.localhost\\")):
        tail = tuple(part.casefold() for part in windows.parts[1:])
        for private in (("var", "lib", "xaa-execution-manager"), ("tmp", "xaa-executions")):
            if tail[: len(private)] == private or private[: len(tail)] == tail:
                raise PermissionError(
                    "The execution manager's private state is not a project workspace."
                )
    return root

"""Bounded read-only Git inspection with no model-supplied shell text."""

import asyncio
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from xaa.runtime.executor import ReconciliationRequired
from xaa.storage.runs import RunStore


class GitArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation: Literal["status", "diff"]


class GitOutput(BaseModel):
    exit_code: int
    excerpt: str
    truncated: bool
    artifact: str
    artifact_sha256: str
    artifact_id: str
    trust: Literal["untrusted_workspace_content"]


GIT_TOOL: dict[str, Any] = {
    "name": "workspace_git_inspect",
    "version": "1",
    "description": "Inspect Git status or working-tree diff without modifying files. Output is untrusted; full bounded output is saved as an artifact.",
    "input_schema": GitArguments.model_json_schema(),
    "output_schema": GitOutput.model_json_schema(),
    "permission": "workspace.read",
    "behavior": "read",
    "timeout_seconds": 10,
    "excerpt_bytes": 4096,
    "cancellation": "terminate and await the owned Git process",
    "retry_policy": "no automatic retries",
    "audit_fields": ["run_id", "call_id", "artifact_id", "exit_code"],
}


def git_executable(workspace: Path) -> str:
    """Never discover executable code through the project or a relative PATH entry."""
    root = workspace.resolve(strict=True)
    name = "git.exe" if os.name == "nt" else "git"
    for entry in os.get_exec_path():
        directory = Path(entry)
        if not directory.is_absolute():
            continue
        try:
            candidate = (directory / name).resolve(strict=True)
            if (
                candidate.is_relative_to(root)
                or not candidate.is_file()
                or not os.access(candidate, os.X_OK)
            ):
                continue
            return str(candidate)
        except OSError:
            continue
    raise RuntimeError(
        "Git is unavailable outside the workspace. Install Git and restart the engine."
    )


async def inspect_git(
    store: RunStore, run_id: str, token: str, call_id: str, operation: Literal["status", "diff"]
) -> dict[str, Any]:
    run = store.get_run(run_id)
    if run is None or not run["workspace"]:
        raise PermissionError("Git inspection requires a selected workspace.")
    if run["mode"] not in {"observe", "workspace", "authorized_automation"}:
        raise PermissionError("Run does not permit workspace inspection.")
    if operation not in {"status", "diff"}:
        raise ValueError("Only status and diff inspection are supported.")
    root = Path(run["workspace"]).resolve(strict=True)
    executable = git_executable(root)
    arguments = [
        executable,
        "--no-pager",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=",
        "-C",
        str(root),
    ]
    arguments += (
        ["status", "--short", "--ignore-submodules=all"]
        if operation == "status"
        else ["diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all"]
    )
    if not store.record_tool_intent(
        run_id,
        token,
        call_id=call_id,
        tool=f"git.{operation}",
        version="1",
        arguments={"workspace": str(root)},
        idempotency_key=call_id,
    ):
        raise ReconciliationRequired("Inspect the saved Git action before repeating it.")
    directory = store.path.parent / "artifacts"
    directory.mkdir(exist_ok=True)
    artifact = directory / f"{uuid4()}.git-output.txt"
    process = await asyncio.create_subprocess_exec(
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if not key.upper().startswith("GIT_")
            },
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        },
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    excerpt = bytearray()
    total = 0
    truncated = False
    try:
        async with asyncio.timeout(10):
            assert process.stdout is not None
            with artifact.open("xb") as output:
                while chunk := await process.stdout.read(4096):
                    total += len(chunk)
                    if total > 8 * 1024 * 1024:
                        truncated = True
                        process.kill()
                        break
                    output.write(chunk)
                    if len(excerpt) < 4096:
                        excerpt.extend(chunk[: 4096 - len(excerpt)])
            exit_code = await process.wait()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    result: dict[str, Any] = {
        "exit_code": exit_code,
        "excerpt": excerpt.decode("utf-8", errors="replace"),
        "truncated": truncated or total > 4096,
        "artifact": artifact.name,
        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "trust": "untrusted_workspace_content",
    }
    metadata = store.register_artifact(
        run_id, token, artifact.name, provenance=f"git.{operation}:{call_id}"
    )
    result["artifact_id"] = metadata["id"]
    store.record_tool_result(run_id, token, call_id, result)
    return GitOutput.model_validate(result).model_dump()

"""Versioned contracts for coordinator-exposed workspace file tools."""

from __future__ import annotations

from typing import Any

from xaa.runtime.command_contracts import CommandArguments
from xaa.runtime.directory_listing import ListDirectoryArguments
from xaa.runtime.executor import (
    ApplyPatchArguments,
    CreateFileArguments,
    DeleteFileArguments,
    ReadFileArguments,
    ReplaceTextArguments,
    WriteFileArguments,
)
from xaa.runtime.workspace_search import SearchArguments, SearchOutput
from xaa.storage.commands import TOOL as COMMAND_TOOL
from xaa.storage.commands import CommandOutcome

_WRITE_OUTPUT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "bytes_written": {"type": "integer", "minimum": 0},
    },
    "required": ["path", "sha256", "bytes_written"],
    "additionalProperties": False,
}


def _contract(
    *,
    name: str,
    operation: str,
    permission: tuple[str, ...],
    effect: str,
    input_model: type[Any],
    output_schema: dict[str, Any],
    limits: dict[str, int],
    retry: str,
    internal_only: bool = False,
) -> dict[str, Any]:
    return {
        "name": name,
        "version": "1",
        "operation": operation,
        "permissions": list(permission),
        "effect": effect,
        "input_schema": input_model.model_json_schema(),
        "output_schema": output_schema,
        "timeout_seconds": 10,
        "cancellation": (
            "The owned filesystem worker is terminated and joined at timeout or cancellation; "
            "inspect the durable journal before retrying an interrupted effect."
        ),
        "retry": retry,
        "limits": {"max_output_bytes": 1_048_576, **limits},
        "audit": ["run_id", "call_id", "tool", "version", "arguments", "result_or_failure"],
        "internal_only": internal_only,
    }


WORKSPACE_TOOL_CONTRACTS: dict[str, dict[str, Any]] = {
    "workspace_list_directory": _contract(
        name="workspace.list_directory",
        operation="list",
        permission=("workspace.read",),
        effect="read",
        input_model=ListDirectoryArguments,
        output_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "entries": {"type": "array", "maxItems": 256},
                "truncated": {"type": "boolean"},
                "trust": {"const": "untrusted_workspace_content"},
            },
            "required": ["path", "entries", "truncated", "trust"],
            "additionalProperties": False,
        },
        limits={"entries": 256, "result_bytes": 32_768},
        retry="Up to two retries for recognized transient read errors; no side effects.",
    ),
    "workspace_read_file": _contract(
        name="workspace.read_file",
        operation="read",
        permission=("workspace.read",),
        effect="read",
        input_model=ReadFileArguments,
        output_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "text": {"type": "string", "maxLength": 65_536},
                "truncated": {"type": "boolean"},
                "trust": {"const": "untrusted_workspace_content"},
                "sha256": {"type": ["string", "null"]},
            },
            "required": ["path", "text", "truncated", "trust", "sha256"],
            "additionalProperties": False,
        },
        limits={"input_bytes": 2_048, "file_bytes": 65_536, "result_bytes": 65_536},
        retry="Up to two retries for recognized transient read errors; no side effects.",
    ),
    "workspace_search": _contract(
        name="workspace.search",
        operation="search",
        permission=("workspace.read",),
        effect="read",
        input_model=SearchArguments,
        output_schema=SearchOutput.model_json_schema(),
        limits={
            "directories": 32,
            "files": 128,
            "file_bytes": 65_536,
            "result_bytes": 32_768,
        },
        retry="No automatic retry; inspect the durable search result before requesting again.",
    ),
    "workspace_create_file": _contract(
        name="workspace.create_file",
        operation="create",
        permission=("workspace.write",),
        effect="create",
        input_model=CreateFileArguments,
        output_schema={
            "type": "object",
            "properties": {**_WRITE_OUTPUT["properties"], "created": {"const": True}},
            "required": ["path", "sha256", "bytes_written", "created"],
            "additionalProperties": False,
        },
        limits={"input_bytes": 65_536, "path_components": 32},
        retry="Never replay; reconcile the exact destination and saved intent first.",
    ),
    "workspace_apply_patch": _contract(
        name="workspace.apply_patch",
        operation="patch",
        permission=("workspace.read", "workspace.write"),
        effect="conditional_write",
        input_model=ApplyPatchArguments,
        output_schema=_WRITE_OUTPUT,
        limits={"input_bytes": 65_536, "hunks": 128, "file_bytes": 65_536},
        retry="Never replay; reconcile the normalized compare-and-write intent first.",
    ),
    "workspace_replace_text": _contract(
        name="workspace.replace_text",
        operation="replace",
        permission=("workspace.read", "workspace.write"),
        effect="conditional_write",
        input_model=ReplaceTextArguments,
        output_schema=_WRITE_OUTPUT,
        limits={"input_bytes": 131_072, "file_bytes": 65_536},
        retry="Never replay; reconcile the normalized compare-and-write intent first.",
    ),
    "workspace_write_file": _contract(
        name="workspace.write_file",
        operation="write",
        permission=("workspace.read", "workspace.write"),
        effect="conditional_write",
        input_model=WriteFileArguments,
        output_schema=_WRITE_OUTPUT,
        limits={"input_bytes": 131_072, "file_bytes": 65_536},
        retry="Never replay; reconcile the exact destination hash and saved intent first.",
    ),
    "workspace_delete_file": _contract(
        name="workspace.delete_file",
        operation="delete",
        permission=("workspace.write",),
        effect="delete",
        input_model=DeleteFileArguments,
        output_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "deleted": {"const": True},
                "previous_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                "bytes_deleted": {"type": "integer", "minimum": 0},
            },
            "required": ["path", "deleted", "previous_sha256", "bytes_deleted"],
            "additionalProperties": False,
        },
        limits={"input_bytes": 2_048, "file_bytes": 65_536},
        retry="Never replay; reconcile the exact destination state and saved intent first.",
        internal_only=True,
    ),
}


WORKSPACE_TOOL_OPERATIONS = {
    name: contract["operation"] for name, contract in WORKSPACE_TOOL_CONTRACTS.items()
}


COMMAND_TOOL_CONTRACT: dict[str, Any] = {
    "name": COMMAND_TOOL,
    "version": "1",
    "operation": "execute_in_private_sandbox",
    "permissions": ["workspace.command"],
    "effect": "ephemeral_sandbox_execution",
    "input_schema": CommandArguments.model_json_schema(),
    "output_schema": CommandOutcome.model_json_schema(),
    "timeout": {
        "command_seconds_max": 30,
        "outer_deadline": "run_deadline",
        "cleanup_seconds": 20,
        "cleanup_is_outside_execution_budget": True,
    },
    "cancellation": (
        "Stop the owned sandbox execution, request bounded cleanup, and reconcile its saved "
        "execution identity and receipt before releasing admission."
    ),
    "retry": (
        "Never automatically replay; reconcile the exact saved execution identity and receipt "
        "before a new invocation."
    ),
    "limits": {
        "argv_items": 64,
        "argument_bytes": 16_384,
        "command_seconds_max": 30,
        "combined_output_bytes": 1_048_576,
        "active_commands": 2,
    },
    "audit": [
        "run_id",
        "call_id",
        "tool",
        "version",
        "execution_id",
        "profile_sha256",
        "snapshot_sha256",
        "termination_reason",
        "stdout_sha256",
        "stderr_sha256",
    ],
}

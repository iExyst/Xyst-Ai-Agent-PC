"""Versioned contracts and bounded schemas for procedure-library tools."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from xaa.storage.skills import (
    MODEL_PROCEDURE_RESULT_BYTES,
    SkillContentRevision,
    SkillDraft,
    SkillResourceRemoval,
    SkillResourceRevision,
)

PROCEDURE_CATALOG_RESULT_BYTES = 512 * 1024
PROCEDURE_READ_RESULT_BYTES = 512 * 1024
PROCEDURE_WRITE_RESULT_BYTES = MODEL_PROCEDURE_RESULT_BYTES


class ProcedureCatalogArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=99)
    category: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )


_PROCEDURE_ITEM = {
    "type": "object",
    "required": [
        "id",
        "name",
        "description",
        "category",
        "lifecycle_state",
        "active_version",
        "latest_version",
        "required_tools",
        "required_permissions",
    ],
    "properties": {
        "id": {"type": "string", "maxLength": 64},
        "name": {"type": "string", "maxLength": 80},
        "description": {"type": "string", "maxLength": 256},
        "category": {"type": "string", "maxLength": 64},
        "lifecycle_state": {"enum": ["candidate", "evaluated", "active", "deprecated"]},
        "active_version": {"type": ["integer", "null"], "minimum": 1, "maximum": 128},
        "latest_version": {"type": "integer", "minimum": 1, "maximum": 128},
        "required_tools": {
            "type": "array",
            "maxItems": 32,
            "items": {
                "type": "object",
                "required": ["name", "version"],
                "properties": {
                    "name": {"type": "string", "maxLength": 128},
                    "version": {"type": "string", "maxLength": 32},
                },
                "additionalProperties": False,
            },
        },
        "required_permissions": {
            "type": "array",
            "maxItems": 2,
            "items": {"enum": ["workspace.read", "workspace.write"]},
        },
    },
    "additionalProperties": False,
}

PROCEDURE_CATALOG_CONTRACT: dict[str, Any] = {
    "name": "procedure.catalog",
    "version": "1",
    "operation": "list_saved_procedure_metadata",
    "permissions": ["procedure.catalog.read"],
    "effect": "read",
    "input_schema": ProcedureCatalogArguments.model_json_schema(),
    "output_schema": {
        "oneOf": [
            {
                "type": "object",
                "required": ["procedures", "next_offset", "selection", "trust"],
                "properties": {
                    "procedures": {
                        "type": "array",
                        "maxItems": 50,
                        "items": _PROCEDURE_ITEM,
                    },
                    "next_offset": {"type": ["integer", "null"], "minimum": 0, "maximum": 100},
                    "selection": {"const": "explicit_user_only"},
                    "trust": {"const": "untrusted_procedure_metadata"},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["status", "error", "trust"],
                "properties": {
                    "status": {"const": "output_limit"},
                    "error": {"type": "string", "maxLength": 160},
                    "trust": {"const": "untrusted_procedure_metadata"},
                },
                "additionalProperties": False,
            },
        ]
    },
    "timeout_seconds": 10,
    "cancellation": "Read-only local catalog query; cancellation has no external effect.",
    "retry": "No automatic retry; request a smaller page after an output-limit result.",
    "limits": {
        "page_items": 50,
        "page_offset_max": 99,
        "description_characters": 256,
        "tool_requirements_per_item": 32,
        "result_bytes": PROCEDURE_CATALOG_RESULT_BYTES,
    },
    "audit": ["run_id", "call_id", "tool", "version", "arguments", "result_or_failure"],
}


PROCEDURE_VIEW_CONTRACT: dict[str, Any] = {
    "name": "procedure.view",
    "version": "1",
    "operation": "read_selected_procedure_text",
    "permissions": ["procedure.selected.read"],
    "effect": "read",
    "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    "output_schema": {
        "oneOf": [
            {
                "type": "object",
                "required": ["skill_id", "version", "name", "content", "trust", "authority"],
                "properties": {
                    "skill_id": {"type": "string", "maxLength": 128},
                    "version": {"type": "integer", "minimum": 1, "maximum": 128},
                    "name": {"type": "string", "maxLength": 80},
                    "content": {"type": "string", "maxLength": 80 * 1024},
                    "trust": {"const": "untrusted_selected_procedure"},
                    "authority": {"const": "selection_does_not_expand_permissions_or_tools"},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["status", "error", "trust"],
                "properties": {
                    "status": {"const": "output_limit"},
                    "error": {"type": "string", "maxLength": 160},
                    "trust": {"const": "untrusted_selected_procedure"},
                },
                "additionalProperties": False,
            },
        ]
    },
    "timeout_seconds": 10,
    "cancellation": "Read-only selected snapshot; cancellation has no external effect.",
    "retry": "No automatic retry; inspect the saved tool result before requesting again.",
    "limits": {"content_bytes": 80 * 1024, "result_bytes": PROCEDURE_READ_RESULT_BYTES},
    "audit": ["run_id", "call_id", "tool", "version", "skill_id", "procedure_version"],
}


PROCEDURE_RESOURCE_READ_CONTRACT: dict[str, Any] = {
    "name": "procedure.read_resource",
    "version": "1",
    "operation": "read_selected_linked_text_resource",
    "permissions": ["procedure.selected.read"],
    "effect": "read",
    "input_schema": {
        "type": "object",
        "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 512}},
        "required": ["path"],
        "additionalProperties": False,
    },
    "output_schema": {
        "oneOf": [
            {
                "type": "object",
                "required": ["path", "content", "trust"],
                "properties": {
                    "path": {"type": "string", "maxLength": 512},
                    "content": {"type": "string", "maxLength": 64 * 1024},
                    "trust": {"const": "untrusted_skill_resource"},
                },
                "additionalProperties": False,
            },
            {
                "type": "object",
                "required": ["status", "error", "trust"],
                "properties": {
                    "status": {"const": "output_limit"},
                    "error": {"type": "string", "maxLength": 160},
                    "trust": {"const": "untrusted_skill_resource"},
                },
                "additionalProperties": False,
            },
        ]
    },
    "timeout_seconds": 10,
    "cancellation": "Read-only selected package resource; cancellation has no external effect.",
    "retry": "No automatic retry; inspect the saved tool result before requesting again.",
    "limits": {"content_bytes": 64 * 1024, "result_bytes": PROCEDURE_READ_RESULT_BYTES},
    "audit": ["run_id", "call_id", "tool", "version", "path", "resource_sha256"],
}


_PROCEDURE_SUMMARY = {
    "type": "object",
    "required": [
        "id",
        "name",
        "description",
        "category",
        "lifecycle_state",
        "active_version",
        "latest_version",
    ],
    "properties": {
        "id": {"type": "string", "maxLength": 128},
        "name": {"type": "string", "maxLength": 80},
        "description": {"type": "string", "maxLength": 1024},
        "category": {"type": "string", "maxLength": 64},
        "lifecycle_state": {"enum": ["candidate", "evaluated", "active", "deprecated"]},
        "active_version": {"type": ["integer", "null"], "minimum": 1, "maximum": 128},
        "latest_version": {"type": "integer", "minimum": 1, "maximum": 128},
        "lifecycle_revision": {"type": "integer", "minimum": 0},
        "created_at": {"type": "string", "maxLength": 40},
        "updated_at": {"type": "string", "maxLength": 40},
    },
    "additionalProperties": False,
}

_PROCEDURE_WRITE_FAILURE = {
    "type": "object",
    "required": ["ok", "effect", "error"],
    "properties": {
        "ok": {"const": False},
        "effect": {"const": "not_started"},
        "error": {
            "type": "object",
            "required": ["code", "message"],
            "properties": {
                "code": {"type": "string", "maxLength": 128},
                "message": {"type": "string", "maxLength": 1024},
            },
            "additionalProperties": False,
        },
    },
    "additionalProperties": False,
}


def _procedure_write_contract(
    *,
    name: str,
    operation: str,
    permission: str,
    input_schema: dict[str, Any],
    success_schema: dict[str, Any],
    limits: dict[str, int],
) -> dict[str, Any]:
    return {
        "name": name,
        "version": "1",
        "operation": operation,
        "permissions": [permission],
        "effect": "write_local_library_candidate",
        "input_schema": input_schema,
        "output_schema": {"oneOf": [success_schema, _PROCEDURE_WRITE_FAILURE]},
        "timeout_seconds": 10,
        "cancellation": "One SQLite transaction saves the inactive candidate and its durable tool result together; after interruption, inspect that durable result before considering a retry.",
        "retry": "No automatic retry; inspect the durable tool result before retrying an uncertain call.",
        "limits": {**limits, "result_bytes": PROCEDURE_WRITE_RESULT_BYTES},
        "audit": [
            "run_id",
            "call_id",
            "tool",
            "version",
            "arguments",
            "result_or_failure",
            "procedure_id",
            "procedure_version",
        ],
    }


PROCEDURE_DRAFT_CONTRACT = _procedure_write_contract(
    name="procedure.draft",
    operation="create_inactive_unevaluated_candidate_from_run",
    permission="procedure.drafting_granted",
    input_schema=SkillDraft.model_json_schema(),
    success_schema={
        "type": "object",
        "required": ["status", "procedure", "trust", "requires_user_review", "notice"],
        "properties": {
            "status": {"const": "candidate_created"},
            "procedure": _PROCEDURE_SUMMARY,
            "trust": {"const": "untrusted_model_draft"},
            "requires_user_review": {"const": True},
            "notice": {"type": "string", "maxLength": 160},
        },
        "additionalProperties": False,
    },
    limits={
        "content_bytes": 80 * 1024,
        "resources": 32,
        "resource_total_bytes": 1024 * 1024,
        "drafts_per_run": 3,
    },
)

_PROCEDURE_VERSION_RESULT = {
    "type": "object",
    "required": ["status", "procedure", "version", "trust", "requires_user_review"],
    "properties": {
        "status": {"const": "candidate_version_created"},
        "procedure": _PROCEDURE_SUMMARY,
        "version": {"type": "integer", "minimum": 2, "maximum": 128},
        "trust": {"type": "string", "maxLength": 64},
        "requires_user_review": {"const": True},
        "notice": {"type": "string", "maxLength": 256},
        "edit_mode": {"enum": ["full_rewrite", "targeted_replace"]},
        "replacements": {"type": ["integer", "null"], "minimum": 0, "maximum": 81920},
        "resource_path": {"type": "string", "maxLength": 512},
        "resource_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "resource_edit": {"enum": ["created", "replaced"]},
        "resources": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["path", "sha256", "edit"],
                "properties": {
                    "path": {"type": "string", "maxLength": 512},
                    "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                    "edit": {"enum": ["created", "replaced", "removed"]},
                },
                "additionalProperties": False,
            },
        },
        "resource_edit_count": {"type": "integer", "minimum": 1, "maximum": 8},
        "removed_resource_path": {"type": "string", "maxLength": 512},
    },
    "additionalProperties": False,
}


def _procedure_version_result(required: list[str]) -> dict[str, Any]:
    schema = json.loads(json.dumps(_PROCEDURE_VERSION_RESULT))
    schema["required"] = required
    return schema


PROCEDURE_CONTENT_REVISION_CONTRACT = _procedure_write_contract(
    name="procedure.revise",
    operation="create_content_only_candidate_version_of_selected_procedure",
    permission="procedure.revision_granted",
    input_schema=SkillContentRevision.model_json_schema(),
    success_schema=_procedure_version_result(
        [
            "status",
            "procedure",
            "version",
            "edit_mode",
            "replacements",
            "trust",
            "requires_user_review",
            "notice",
        ]
    ),
    limits={"content_bytes": 80 * 1024, "versions_per_procedure": 128},
)

PROCEDURE_RESOURCE_REVISION_CONTRACT = _procedure_write_contract(
    name="procedure.resource_revise",
    operation="create_candidate_version_with_atomic_linked_resource_edits",
    permission="procedure.resource_revision_granted",
    input_schema=SkillResourceRevision.model_json_schema(),
    success_schema=_procedure_version_result(
        [
            "status",
            "procedure",
            "version",
            "resource_path",
            "resource_sha256",
            "resource_edit",
            "resources",
            "resource_edit_count",
            "trust",
            "requires_user_review",
            "notice",
        ]
    ),
    limits={
        "resource_bytes_each": 64 * 1024,
        "operations_per_edit": 8,
        "versions_per_procedure": 128,
    },
)

PROCEDURE_RESOURCE_REMOVAL_CONTRACT = _procedure_write_contract(
    name="procedure.resource_remove",
    operation="create_candidate_version_with_one_linked_resource_removed",
    permission="procedure.resource_removal_granted",
    input_schema=SkillResourceRemoval.model_json_schema(),
    success_schema=_procedure_version_result(
        [
            "status",
            "procedure",
            "version",
            "removed_resource_path",
            "trust",
            "requires_user_review",
        ]
    ),
    limits={"removals_per_call": 1, "versions_per_procedure": 128},
)


def selected_procedure_write_schema(
    contract: dict[str, Any],
    *,
    trial: dict[str, Any],
    allow_resource_removal: bool = False,
) -> dict[str, Any]:
    schema = json.loads(json.dumps(contract["input_schema"]))
    properties = schema.get("properties", {})
    if "skill_id" in properties:
        properties["skill_id"]["enum"] = [trial["skill_id"]]
    if "expected_version" in properties:
        properties["expected_version"]["enum"] = [trial["version"]]
    if contract is PROCEDURE_RESOURCE_REVISION_CONTRACT and not allow_resource_removal:
        properties.pop("remove_paths", None)
    return schema


def bounded_procedure_catalog(result: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(result, ensure_ascii=True).encode("ascii")
    if len(encoded) <= PROCEDURE_CATALOG_RESULT_BYTES:
        return result
    return {
        "status": "output_limit",
        "error": "Procedure catalog result exceeded the 512 KiB tool limit; request a smaller page.",
        "trust": "untrusted_procedure_metadata",
    }


def bounded_procedure_read_result(result: dict[str, Any], *, trust: str) -> dict[str, Any]:
    encoded = json.dumps(result, ensure_ascii=True).encode("ascii")
    if len(encoded) <= PROCEDURE_READ_RESULT_BYTES:
        return result
    return {
        "status": "output_limit",
        "error": "Selected procedure read exceeded the 512 KiB tool limit.",
        "trust": trust,
    }

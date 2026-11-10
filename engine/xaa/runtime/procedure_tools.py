"""Versioned contracts and bounded schemas for procedure-library tools."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

PROCEDURE_CATALOG_RESULT_BYTES = 512 * 1024
PROCEDURE_READ_RESULT_BYTES = 512 * 1024


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

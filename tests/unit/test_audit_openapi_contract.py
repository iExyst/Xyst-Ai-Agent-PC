from __future__ import annotations

import sys
from pathlib import Path

scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(scripts_dir))

import audit_openapi_contract


def test_contract_audit_accepts_typed_payloads_and_bodyless_delete() -> None:
    document = {
        "paths": {
            "/items/{item_id}": {
                "get": {
                    "parameters": [{"name": "item_id", "schema": {"type": "string"}}],
                    "responses": {
                        "200": {"content": {"application/json": {"schema": {"type": "object"}}}}
                    },
                },
                "delete": {"responses": {"204": {"description": "Deleted"}}},
            }
        }
    }

    failures, operations, request_bodies, bodyless_successes = audit_openapi_contract.audit(
        document
    )

    assert failures == []
    assert operations == 2
    assert request_bodies == 0
    assert bodyless_successes == 1


def test_contract_audit_rejects_untyped_request_parameter_and_response() -> None:
    document = {
        "paths": {
            "/items": {
                "post": {
                    "requestBody": {"content": {"application/json": {}}},
                    "parameters": [{"name": "mode"}],
                    "responses": {"201": {"content": {"application/json": {"example": {}}}}},
                }
            }
        }
    }

    failures, operations, request_bodies, bodyless_successes = audit_openapi_contract.audit(
        document
    )

    assert operations == 1
    assert request_bodies == 1
    assert bodyless_successes == 0
    assert failures == [
        "POST /items: request body has no typed media schema",
        "POST /items: parameter has no schema",
        "POST /items: 201 response has untyped media content",
        "POST /items: no successful typed response or 204/304 response",
    ]

"""Check that generated OpenAPI operations keep typed request and response payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

CONTRACT = Path("contracts/openapi.json")
METHODS = {"get", "post", "put", "patch", "delete"}


def _media_schemas(content: Any) -> bool:
    if not isinstance(content, dict) or not content:
        return False
    media_types = cast(dict[str, Any], content)
    return all(
        isinstance(media, dict) and isinstance(cast(dict[str, Any], media).get("schema"), dict)
        for media in media_types.values()
    )


def audit(document: Any) -> tuple[list[str], int, int, int]:
    if not isinstance(document, dict):
        raise TypeError("OpenAPI document must be an object.")
    document_object = cast(dict[str, Any], document)
    paths = document_object.get("paths")
    if not isinstance(paths, dict):
        raise TypeError("OpenAPI document must contain a paths mapping.")
    path_objects = cast(dict[str, Any], paths)

    failures: list[str] = []
    operation_count = request_body_count = no_content_success_count = 0
    for path, path_item in path_objects.items():
        if not isinstance(path_item, dict):
            failures.append(f"{path}: path item is not an object")
            continue
        path_object = cast(dict[str, Any], path_item)
        for method, operation in path_object.items():
            if method not in METHODS:
                continue
            label = f"{method.upper()} {path}"
            operation_count += 1
            if not isinstance(operation, dict):
                failures.append(f"{label}: operation is not an object")
                continue
            operation_object = cast(dict[str, Any], operation)

            body = operation_object.get("requestBody")
            if body is not None:
                request_body_count += 1
                if not isinstance(body, dict):
                    failures.append(f"{label}: request body is not an object")
                    content = None
                else:
                    content = cast(dict[str, Any], body).get("content")
                if not _media_schemas(content):
                    failures.append(f"{label}: request body has no typed media schema")

            parameters = operation_object.get("parameters", [])
            if isinstance(parameters, list):
                for parameter in cast(list[Any], parameters):
                    if not isinstance(parameter, dict):
                        failures.append(f"{label}: parameter is not an object")
                    elif "schema" not in parameter:
                        failures.append(f"{label}: parameter has no schema")
            else:
                failures.append(f"{label}: parameters are not a list")

            responses = operation_object.get("responses")
            if not isinstance(responses, dict):
                failures.append(f"{label}: responses are missing")
                continue
            response_objects = cast(dict[str, Any], responses)
            success_responses: list[tuple[str, Any]] = []
            for status, response in response_objects.items():
                status_text = str(status)
                if not isinstance(response, dict):
                    failures.append(f"{label}: {status_text} response is not an object")
                    response_content = None
                else:
                    response_content = cast(dict[str, Any], response).get("content")
                if response_content not in (None, {}) and not _media_schemas(response_content):
                    failures.append(f"{label}: {status_text} response has untyped media content")
                if status_text.startswith("2"):
                    success_responses.append((status_text, cast(Any, response)))
            typed_success = False
            for status, response in success_responses:
                content = (
                    cast(dict[str, Any], response).get("content")
                    if isinstance(response, dict)
                    else None
                )
                if content is None or content == {}:
                    if str(status) in {"204", "304"}:
                        no_content_success_count += 1
                        typed_success = True
                    continue
                if _media_schemas(content):
                    typed_success = True
            if not typed_success:
                failures.append(f"{label}: no successful typed response or 204/304 response")

    return failures, operation_count, request_body_count, no_content_success_count


def main() -> None:
    document = json.loads(CONTRACT.read_text(encoding="utf-8"))
    document_object = cast(dict[str, Any], document)
    components = document_object.get("components", {})
    component_schemas = cast(dict[str, Any], components).get("schemas", {})
    schemas = cast(dict[str, Any], component_schemas)
    failures, operations, request_bodies, empty_successes = audit(document)
    if failures:
        raise SystemExit("OpenAPI type audit failed:\n- " + "\n- ".join(failures))
    print(
        f"OpenAPI type audit: {operations} operations, {len(schemas)} schemas, "
        f"{request_bodies} typed request bodies, {empty_successes} intentional bodyless success responses."
    )


if __name__ == "__main__":
    main()

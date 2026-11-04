"""Bounded local-reference expansion for provider tool-schema compatibility."""

from typing import Any, cast


class ToolSchemaError(ValueError):
    """A schema cannot be represented safely by the provider transport."""


SINGLE = {
    "items",
    "additionalProperties",
    "additionalItems",
    "contains",
    "not",
    "if",
    "then",
    "else",
    "propertyNames",
    "unevaluatedProperties",
    "unevaluatedItems",
    "contentSchema",
}
MAPPINGS = {"properties", "patternProperties", "dependentSchemas"}
ARRAYS = {"allOf", "anyOf", "oneOf", "prefixItems"}
ANNOTATIONS = {"title", "description", "default", "examples", "$comment"}


def expand_schema(schema: dict[str, Any]) -> dict[str, Any]:
    nodes = 0
    byte_count = 0

    def account(value: Any, depth: int) -> None:
        nonlocal nodes, byte_count
        nodes += 1
        byte_count += len(value.encode("utf-8")) if isinstance(value, str) else 8
        if depth > 32 or nodes > 20000 or byte_count > 1_048_576:
            raise ToolSchemaError("Expanded tool schema exceeds its depth/size limit.")

    def data(value: Any, depth: int) -> Any:
        account(value, depth)
        if isinstance(value, dict):
            mapping = cast(dict[str, Any], value)
            return {key: data(item, depth + 1) for key, item in mapping.items()}
        if isinstance(value, list):
            return [data(item, depth + 1) for item in cast(list[Any], value)]
        return value

    def visit(value: Any, active: tuple[str, ...], depth: int) -> Any:
        account(value, depth)
        if not isinstance(value, dict):
            return data(value, depth + 1)
        node = cast(dict[str, Any], value)
        if "$id" in node or "$anchor" in node or "$dynamicRef" in node:
            raise ToolSchemaError("Scoped/dynamic schema references are unsupported.")
        if "$ref" in node:
            ref = node["$ref"]
            if not isinstance(ref, str) or not ref.startswith("#/") or ref in active:
                raise ToolSchemaError("Only acyclic in-document schema references are supported.")
            if set(node) - {"$ref"} - ANNOTATIONS:
                raise ToolSchemaError("Schema reference siblings may contain annotations only.")
            target: Any = schema
            try:
                for part in ref[2:].split("/"):
                    key = part.replace("~1", "/").replace("~0", "~")
                    target = (
                        cast(list[Any], target)[int(key)]
                        if isinstance(target, list)
                        else target[key]
                    )
            except (KeyError, IndexError, ValueError, TypeError):
                raise ToolSchemaError("Local schema reference cannot be resolved.") from None
            resolved = visit(target, (*active, ref), depth + 1)
            annotations = {
                key: data(item, depth + 1) for key, item in node.items() if key != "$ref"
            }
            if annotations:
                if not isinstance(resolved, dict):
                    raise ToolSchemaError("Referenced annotations require an object schema.")
                return {**cast(dict[str, Any], resolved), **annotations}
            return resolved
        result: dict[str, Any] = {}
        for key, item in node.items():
            account(key, depth)
            if key in {"$defs", "definitions"}:
                continue
            if key in MAPPINGS and isinstance(item, dict):
                result[key] = {
                    name: visit(child, active, depth + 1)
                    for name, child in cast(dict[str, Any], item).items()
                }
            elif key in ARRAYS and isinstance(item, list):
                result[key] = [visit(child, active, depth + 1) for child in cast(list[Any], item)]
            elif key in SINGLE:
                result[key] = (
                    [visit(child, active, depth + 1) for child in cast(list[Any], item)]
                    if isinstance(item, list)
                    else visit(item, active, depth + 1)
                )
            else:
                result[key] = data(item, depth + 1)
        return result

    expanded = visit(schema, (), 0)
    if not isinstance(expanded, dict):
        raise ToolSchemaError("Tool parameters require an object schema.")
    return cast(dict[str, Any], expanded)


def prepare_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if tools is None:
        return None
    result: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function")
        if tool.get("type") != "function" or not isinstance(function, dict):
            raise ToolSchemaError("Only function tool definitions are supported.")
        definition = cast(dict[str, Any], function).copy()
        parameters = definition.get("parameters")
        if parameters is not None:
            if not isinstance(parameters, dict):
                raise ToolSchemaError("Tool parameters require an object schema.")
            definition["parameters"] = expand_schema(cast(dict[str, Any], parameters))
        result.append({**tool, "function": definition})
    return result

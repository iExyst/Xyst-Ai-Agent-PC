from copy import deepcopy

import pytest
from xaa.providers.tool_schemas import ToolSchemaError, expand_schema, prepare_tools


def test_expansion_preserves_constraints_and_literal_data_without_mutation():
    schema = {
        "$defs": {"A/B": {"type": "string", "minLength": 3}},
        "type": "object",
        "properties": {"value": {"$ref": "#/$defs/A~1B", "description": "value"}},
        "required": ["value"],
        "additionalProperties": False,
        "default": {"$ref": "literal data"},
    }
    original = deepcopy(schema)
    expanded = expand_schema(schema)
    assert expanded["properties"]["value"] == {
        "type": "string",
        "minLength": 3,
        "description": "value",
    }
    assert expanded["required"] == ["value"]
    assert expanded["additionalProperties"] is False
    assert expanded["default"] == {"$ref": "literal data"}
    assert "$defs" not in expanded
    assert schema == original
    tools = [{"type": "function", "function": {"name": "example", "parameters": schema}}]
    prepared = prepare_tools(tools)
    assert prepared is not None
    assert prepared[0]["function"]["parameters"] == expanded
    assert tools[0]["function"]["parameters"] == original


@pytest.mark.parametrize(
    "schema",
    [
        {"$ref": "https://example.invalid/schema"},
        {"$ref": "#/$defs/missing"},
        {"$defs": {"loop": {"$ref": "#/$defs/loop"}}, "$ref": "#/$defs/loop"},
        {
            "$defs": {"x": {"type": "string"}},
            "properties": {"x": {"$ref": "#/$defs/x", "maxLength": 5}},
        },
        {"$id": "scoped", "type": "object"},
    ],
)
def test_unsupported_reference_semantics_rejected(schema):
    with pytest.raises(ToolSchemaError):
        expand_schema(schema)


def test_expansion_bounded_against_exponential_reference_fanout():
    definitions = {"n0": {"type": "string"}}
    for index in range(1, 20):
        definitions[f"n{index}"] = {
            "anyOf": [
                {"$ref": f"#/$defs/n{index - 1}"},
                {"$ref": f"#/$defs/n{index - 1}"},
            ]
        }
    with pytest.raises(ToolSchemaError, match="limit"):
        expand_schema({"$defs": definitions, "properties": {"x": {"$ref": "#/$defs/n19"}}})

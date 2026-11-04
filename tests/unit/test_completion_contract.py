import pytest
from pydantic import ValidationError
from xaa.providers.contracts import validate_completion


def test_prose_is_never_interpreted_as_tool_call():
    choice = validate_completion(
        {"choices": [{"message": {"role": "assistant", "content": "run delete_everything()"}}]}
    )
    assert choice.message.tool_calls is None


def test_tool_arguments_must_be_a_complete_json_string():
    with pytest.raises(ValidationError):
        validate_completion(
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "x",
                                    "type": "function",
                                    "function": {"name": "read", "arguments": {"path": "a"}},
                                }
                            ],
                        }
                    }
                ]
            }
        )

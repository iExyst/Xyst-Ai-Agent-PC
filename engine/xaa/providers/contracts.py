"""Validated OpenAI-compatible completion boundary."""

from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FunctionCall(BaseModel):
    name: str = Field(min_length=1)
    arguments: str


class ToolCall(BaseModel):
    id: str = Field(min_length=1)
    type: Literal["function"]
    function: FunctionCall


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: Literal["assistant"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None

    @model_validator(mode="after")
    def unique_calls(self) -> "AssistantMessage":
        calls = self.tool_calls or []
        if len({call.id for call in calls}) != len(calls):
            raise ValueError("Duplicate tool-call IDs in one response.")
        return self


class CompletionChoice(BaseModel):
    message: AssistantMessage
    finish_reason: str | None = None


class Completion(BaseModel):
    choices: list[CompletionChoice] = Field(min_length=1, max_length=1)


class TokenUsage(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def consistent_total(self) -> "TokenUsage":
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("Provider token totals are inconsistent.")
        return self


def public_assistant_message(message: dict[str, Any]) -> dict[str, Any]:
    """Project public protocol fields without rewriting or decoding tool arguments.

    Legacy partial proposals keep their IDs/known fields for reconciliation. This
    is projection, not a substitute for completion or execution validation.
    """
    public = {key: message[key] for key in ("role", "content", "tool_calls") if key in message}
    calls = public.get("tool_calls")
    if isinstance(calls, list):
        projected: list[dict[str, Any]] = []
        for value in cast(list[Any], calls):
            if not isinstance(value, dict):
                raise TypeError("Stored tool call is not a structured object.")
            call = cast(dict[str, Any], value)
            item = {key: call[key] for key in ("id", "type", "function") if key in call}
            function = item.get("function")
            if isinstance(function, dict):
                function_data = cast(dict[str, Any], function)
                item["function"] = {
                    key: function_data[key] for key in ("name", "arguments") if key in function_data
                }
            projected.append(item)
        public["tool_calls"] = projected
    return public


def public_run_event(event: dict[str, Any]) -> dict[str, Any]:
    if event["event_type"] not in {"model.message", "task.model.message"}:
        return event
    payload = event["payload"]
    message = payload.get("message")
    if not isinstance(message, dict):
        return event
    return {
        **event,
        "payload": {**payload, "message": public_assistant_message(cast(dict[str, Any], message))},
    }


def validate_completion(response: dict[str, Any]) -> CompletionChoice:
    return Completion.model_validate(response).choices[0]

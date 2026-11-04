"""Accumulate bounded streamed completions without executing partial tool calls."""

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from xaa.providers.contracts import TokenUsage, validate_completion


class StreamAssemblyError(ValueError):
    """A stream cannot safely form one complete assistant response."""


class FunctionDelta(BaseModel):
    model_config = ConfigDict(strict=True)
    name: str | None = None
    arguments: str | None = None


class ToolDelta(BaseModel):
    model_config = ConfigDict(strict=True)
    index: int = Field(ge=0, le=7)
    id: str | None = None
    type: Literal["function"] | None = None
    function: FunctionDelta | None = None


class Delta(BaseModel):
    model_config = ConfigDict(strict=True)
    role: Literal["assistant"] | None = None
    content: str | None = None
    tool_calls: list[ToolDelta] | None = Field(default=None, max_length=8)


class ChoiceDelta(BaseModel):
    model_config = ConfigDict(strict=True)
    index: Literal[0] = 0
    delta: Delta
    finish_reason: Literal["stop", "tool_calls", "length", "content_filter"] | None = None


class StreamChunk(BaseModel):
    model_config = ConfigDict(strict=True)
    choices: list[ChoiceDelta] = Field(max_length=1)
    usage: TokenUsage | None = None


class CompletionAssembler:
    def __init__(self) -> None:
        self.content = ""
        self.calls: dict[int, dict[str, Any]] = {}
        self.finish_reason: str | None = None
        self.usage: dict[str, int] | None = None
        self.received_bytes = 0

    def feed(self, raw: str) -> str:
        try:
            self.received_bytes += len(raw.encode("utf-8"))
            if self.received_bytes > 8 * 1024 * 1024:
                raise ValueError("Stream exceeds transport allowance.")
            chunk = StreamChunk.model_validate_json(raw)
            if chunk.usage is not None:
                self.usage = chunk.usage.model_dump()
            if not chunk.choices:
                return ""
            choice = chunk.choices[0]
            delta = choice.delta
            if self.finish_reason is not None:
                raise ValueError("Response data arrived after the finish marker.")
            piece = delta.content or ""
            self.content += piece
            if len(self.content.encode("utf-8")) > 65536:
                raise ValueError("Streamed content exceeds limit.")
            for fragment in delta.tool_calls or []:
                call = self.calls.setdefault(
                    fragment.index,
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                call["id"] += fragment.id or ""
                if fragment.function is not None:
                    call["function"]["name"] += fragment.function.name or ""
                    call["function"]["arguments"] += fragment.function.arguments or ""
                if (
                    len(call["id"]) > 256
                    or len(call["function"]["name"]) > 128
                    or len(call["function"]["arguments"].encode("utf-8")) > 65536
                ):
                    raise ValueError("Streamed tool call exceeds limit.")
            self.finish_reason = choice.finish_reason
            return piece
        except (ValueError, UnicodeError) as exc:
            raise StreamAssemblyError("Malformed or oversized streamed completion.") from exc

    def complete(self) -> dict[str, Any]:
        try:
            if self.finish_reason is None or sorted(self.calls) != list(range(len(self.calls))):
                raise ValueError("Incomplete response or sparse tool indexes.")
            calls = [self.calls[index] for index in sorted(self.calls)]
            if not calls and not self.content.strip():
                raise ValueError("Stream ended without a public answer or tool call.")
            for call in calls:
                if not isinstance(json.loads(call["function"]["arguments"]), dict):
                    raise TypeError("Tool arguments must be an object.")
            message: dict[str, Any] = {"role": "assistant", "content": self.content}
            if calls:
                message["tool_calls"] = calls
            response: dict[str, Any] = {
                "choices": [{"message": message, "finish_reason": self.finish_reason}]
            }
            if self.usage is not None:
                response["usage"] = self.usage
            validate_completion(response)
            return response
        except (ValueError, TypeError) as exc:
            raise StreamAssemblyError(
                "Stream ended without a complete validated response."
            ) from exc

"""Owned LM Studio tokenizer adapter; never predicts or loads a model."""

import asyncio
import json
import os
import sys
from typing import Any, Literal, cast
from urllib.parse import urlsplit

import lmstudio as lms
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from xaa.providers.contracts import ToolCall
from xaa.providers.tool_schemas import ToolSchemaError, prepare_tools
from xaa.runtime.processes import stop_owned_process


class PromptMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = Field(default=None, max_length=65536)
    tool_calls: list[ToolCall] | None = Field(default=None, max_length=8)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def valid_sequence_part(self) -> "PromptMessage":
        if self.role == "tool" and (not self.tool_call_id or self.content is None):
            raise ValueError("Tool results need an ID and text content.")
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("Only assistant messages may propose tools.")
        if self.role != "tool" and self.tool_call_id:
            raise ValueError("Only tool results carry tool_call_id.")
        if self.content is None and not self.tool_calls:
            raise ValueError("A message needs text or tool calls.")
        return self


class PromptCountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str = Field(min_length=1, max_length=256)
    messages: list[PromptMessage] = Field(min_length=1, max_length=256)
    tools: list[dict[str, Any]] | None = Field(default=None, max_length=32)


class PromptCount(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model: str
    prompt_tokens: int = Field(ge=0)
    context_length: int = Field(ge=1)
    sdk_version: str


class PromptCountError(RuntimeError):
    """Safe user-facing count failure; SDK payloads and credentials are excluded."""


def make_chat(messages: list[PromptMessage]) -> lms.Chat:
    entries: list[
        lms.SystemPrompt | lms.UserMessage | lms.AssistantResponse | lms.ToolResultMessage
    ] = []
    for message in messages:
        # The SDK builder coalesces adjacent same-role entries. Build each separately
        # and use typed history so counting preserves the HTTP message boundaries.
        chat = lms.Chat()
        if message.role == "system":
            entries.append(chat.add_system_prompt(message.content or ""))
        elif message.role == "user":
            entries.append(chat.add_user_message(message.content or ""))
        elif message.role == "tool":
            entries.append(
                chat.add_tool_result(
                    lms.ToolCallResultData(
                        content=message.content or "", tool_call_id=message.tool_call_id
                    )
                )
            )
        else:
            calls: list[lms.ToolCallRequest] = []
            for call in message.tool_calls or []:
                arguments = json.loads(call.function.arguments)
                if not isinstance(arguments, dict):
                    raise TypeError("Tool arguments must be JSON objects for token counting.")
                calls.append(
                    lms.ToolCallRequest(
                        type="function",
                        id=call.id,
                        name=call.function.name,
                        arguments=cast(dict[str, Any], arguments),
                    )
                )
            entries.append(chat.add_assistant_response(message.content or "", calls))
    return lms.Chat.from_history(lms.ChatHistoryData(messages=entries))


def tokenizer_host(base_url: str) -> str:
    address = urlsplit(base_url)
    if (
        address.scheme != "http"
        or address.hostname not in {"127.0.0.1", "localhost", "::1"}
        or address.username
        or address.password
        or address.path not in {"", "/"}
        or address.query
        or address.fragment
    ):
        raise PromptCountError(
            "Tokenizer connection currently requires a plain HTTP loopback LM Studio address."
        )
    host = address.hostname
    return f"[{host}]:{address.port or 80}" if ":" in host else f"{host}:{address.port or 80}"


class TokenizerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: bool
    count: PromptCount | None = None
    error: str | None = None


async def count_prompt(
    base_url: str, request: PromptCountRequest, token: SecretStr | None, *, timeout: float = 5
) -> PromptCount:
    tokenizer_host(base_url)
    if timeout <= 0:
        raise PromptCountError("No prompt counting time remains.")
    try:
        request = request.model_copy(update={"tools": prepare_tools(request.tools)})
    except ToolSchemaError as exc:
        raise PromptCountError(str(exc)) from None
    payload = json.dumps(
        {
            "base_url": base_url,
            "request": request.model_dump(mode="json", exclude_none=True),
            "api_token": token.get_secret_value() if token else "",
        }
    ).encode("utf-8")
    if len(payload) > 1_048_576:
        raise PromptCountError("Prompt count request exceeds one MiB.")
    # Provider credentials use the input pipe only, never command line or environment.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        not in {
            "XAA_API_TOKEN",
            "LM_STUDIO_API_TOKEN",
            "LM_API_TOKEN",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "WS_PROXY",
            "WSS_PROXY",
            "NO_PROXY",
        }
    }
    environment["NO_PROXY"] = "*"  # This adapter is direct loopback RPC, including on Windows.
    command = (
        [sys.executable, "--tokenizer-worker"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-m", "xaa.providers.tokenizer_worker"]
    )
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=environment,
        creationflags=0x08000000 if os.name == "nt" else 0,
    )
    try:
        async with asyncio.timeout(timeout):
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
                if len(output) > 32768:
                    raise PromptCountError("Prompt counter returned oversized output.")
            await process.wait()
            if process.returncode != 0:
                raise PromptCountError("Prompt counter exited without a result.")
            response = TokenizerResponse.model_validate_json(output)
            if not response.ok or response.count is None:
                raise PromptCountError(
                    f"Prompt counting failed ({response.error}). Check the loaded model, SDK and LM Studio authentication."
                )
            return response.count
    except TimeoutError:
        raise PromptCountError(
            "Prompt counting timed out; check LM Studio and its selected model."
        ) from None
    except (ValueError, TypeError, OSError):
        raise PromptCountError("Prompt counter returned an invalid result.") from None
    finally:
        await stop_owned_process(process)

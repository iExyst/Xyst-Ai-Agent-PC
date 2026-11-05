"""LM Studio OpenAI-compatible client with native capability discovery."""

from __future__ import annotations

import asyncio
import ipaddress
import json
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from xaa.providers.contracts import TokenUsage, public_assistant_message, validate_completion
from xaa.providers.streaming import CompletionAssembler
from xaa.providers.tool_schemas import prepare_tools

if TYPE_CHECKING:
    from xaa.providers.tokenization import PromptCount, PromptCountRequest


DIAGNOSTIC_OUTPUT_TOKENS = 512

AdmissionCallback = Callable[[int, int], None]

DiagnosticRequest = Callable[
    [str, list[dict[str, Any]], list[dict[str, Any]] | None], Awaitable[dict[str, Any]]
]


class ProviderError(RuntimeError):
    """Actionable error returned by the local inference provider."""


class DiagnosticLimitError(ProviderError):
    """The bounded capability check was truncated and cannot enable tools."""


class ContextLimitError(ProviderError):
    """Request cannot safely fit the known loaded model context."""


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vision: bool = False
    trained_for_tool_use: bool = False


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    context_length: int | None = None
    capabilities: ModelCapabilities = Field(default_factory=ModelCapabilities)


class LMStudioProvider:
    """Talk to LM Studio's OpenAI-compatible API and inspect native model metadata."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234",
        token: str | None = None,
        *,
        exact_context: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.exact_context = exact_context
        self._credential = SecretStr(token) if token else None
        self._inference_lock = asyncio.Lock()
        self._diagnosed_models: set[str] = set()
        self._context_limits: dict[str, int] = {}
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=httpx.Timeout(60.0, connect=3.0),
        )

    @staticmethod
    def validate_endpoint(endpoint: str) -> str:
        """Restrict provider routing to an explicitly selected loopback HTTP server."""
        if not 1 <= len(endpoint) <= 300:
            raise ValueError("LM Studio address must be a short HTTP loopback URL.")
        try:
            parsed = urlsplit(endpoint)
            host = parsed.hostname
            port = parsed.port
            address = ipaddress.ip_address(host) if host and host != "localhost" else None
        except ValueError as exc:
            raise ValueError("LM Studio address must be a valid HTTP loopback URL.") from exc
        is_loopback = host == "localhost" or (address is not None and address.is_loopback)
        if (
            parsed.scheme != "http"
            or not is_loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError(
                "LM Studio address must be an HTTP loopback URL without credentials or a path."
            )
        if host == "localhost":
            return f"http://127.0.0.1{f':{port}' if port is not None else ''}"
        return f"http://{parsed.netloc}"

    async def configure_endpoint(self, endpoint: str) -> str:
        """Switch the local server while serialized with inference and model lifecycle work."""
        normalized = self.validate_endpoint(endpoint)
        async with self._inference_lock:
            previous = self._client
            self._client = httpx.AsyncClient(
                base_url=normalized,
                headers={"Authorization": f"Bearer {self._credential.get_secret_value()}"}
                if self._credential
                else {},
                timeout=httpx.Timeout(60.0, connect=3.0),
            )
            self.base_url = normalized
            self._diagnosed_models.clear()
            self._context_limits.clear()
            await previous.aclose()
        return normalized

    async def count_prompt(self, request: PromptCountRequest, *, timeout: float = 5) -> PromptCount:
        from xaa.providers.tokenization import PromptCountError, count_prompt

        deadline = asyncio.get_running_loop().time() + timeout
        try:
            async with asyncio.timeout(timeout):
                await self._inference_lock.acquire()
        except TimeoutError:
            raise PromptCountError(
                "Prompt counting timed out waiting for local inference."
            ) from None
        try:
            return await count_prompt(
                self.base_url,
                request,
                self._credential,
                timeout=max(0, deadline - asyncio.get_running_loop().time()),
            )
        finally:
            self._inference_lock.release()

    async def close(self) -> None:
        await self._client.aclose()

    async def diagnose_tools(self, model: str, *, request: DiagnosticRequest | None = None) -> bool:
        """Prove a structured harmless tool round trip before enabling workspace tools."""
        if model in self._diagnosed_models:
            return True

        async def send(
            messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
        ) -> dict[str, Any]:
            if request is not None:
                response = await request(model, messages, tools)
            else:
                response = await self.chat(
                    model=model,
                    messages=messages,
                    tools=tools,
                    max_output_tokens=DIAGNOSTIC_OUTPUT_TOKENS,
                )
            choice = validate_completion(response)
            if choice.finish_reason in {"length", "content_filter"}:
                raise DiagnosticLimitError(
                    "Tool-use diagnostic was truncated or filtered; tools remain disabled."
                )
            return response

        nonce = uuid4().hex
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": f"Call xaa_echo with value {nonce}, then report exactly the returned value.",
            }
        ]
        tools: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "xaa_echo",
                    "description": "Return a diagnostic string unchanged.",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
        response = await send(messages, tools)
        try:
            message = public_assistant_message(response["choices"][0]["message"])
            calls = message.get("tool_calls", [])
            if len(calls) != 1:
                raise ValueError("Expected one structured diagnostic tool call.")
            call = calls[0]
            if (
                call["type"] != "function"
                or call["function"]["name"] != "xaa_echo"
                or not call["id"]
            ):
                raise ValueError("Unexpected diagnostic tool call.")
            if json.loads(call["function"]["arguments"]) != {"value": nonce}:
                raise ValueError("Diagnostic arguments did not match the requested schema/value.")
            messages.extend(
                [message, {"role": "tool", "tool_call_id": call["id"], "content": nonce}]
            )
            final = await send(messages)
            answer = public_assistant_message(final["choices"][0]["message"])
            if answer.get("tool_calls") or nonce not in (answer.get("content") or ""):
                raise ValueError("Model did not finish with the correct tool result.")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(f"Tool-use diagnostic failed for {model}: {exc}") from exc
        self._diagnosed_models.add(model)
        return True

    async def health(self) -> bool:
        try:
            response = await self._client.get("/v1/models", timeout=3.0)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Cannot reach LM Studio at {self.base_url}. Start Local Server in LM Studio, "
                "confirm its port, then retry."
            ) from exc

    async def models(self) -> list[ModelInfo]:
        try:
            response = await self._client.get("/v1/models")
            response.raise_for_status()
            entries = response.json().get("data", [])
            result = [ModelInfo(id=item["id"]) for item in entries if "id" in item]
            try:
                native = await self._client.get("/api/v1/models")
                native.raise_for_status()
                metadata = {item["key"]: item for item in native.json().get("models", [])}
                for model in result:
                    details = metadata.get(model.id, {})
                    model.context_length = details.get("max_context_length")
                    model.capabilities = ModelCapabilities.model_validate(
                        details.get("capabilities", {})
                    )
                    for instance in details.get("loaded_instances", []):
                        limit = instance.get("config", {}).get("context_length")
                        if isinstance(limit, int) and limit > 0:
                            self._context_limits[instance["id"]] = limit
                            self._context_limits[model.id] = limit
            except (httpx.HTTPError, ValueError, KeyError):
                # Older LM Studio servers may only expose the OpenAI-compatible endpoint.
                pass
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(
                "LM Studio model discovery failed. Check the server version and /v1/models endpoint."
            ) from exc

    async def load_model(self, model: str, *, context_length: int | None = None) -> str:
        async with self._inference_lock:
            instance = await self._load_model(model, context_length=context_length)
            self._diagnosed_models.discard(model)
            if context_length is not None:
                self._context_limits[model] = context_length
                self._context_limits[instance] = context_length
            await self.models()
            return instance

    async def _load_model(self, model: str, *, context_length: int | None = None) -> str:
        """Load an already-installed model through LM Studio's native API; never downloads it."""
        payload: dict[str, Any] = {"model": model}
        if context_length is not None:
            payload["context_length"] = context_length
        try:
            response = await self._client.post("/api/v1/models/load", json=payload)
            response.raise_for_status()
            instance_id = response.json().get("instance_id")
            if not isinstance(instance_id, str) or not instance_id:
                raise ProviderError("LM Studio loaded the model without returning an instance ID.")
            return instance_id
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"LM Studio could not load {model} ({exc.response.status_code}). "
                "Confirm the model is installed and enough memory is available."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "LM Studio model loading failed; check native REST API support."
            ) from exc

    async def unload_model(self, instance_id: str) -> None:
        async with self._inference_lock:
            await self._unload_model(instance_id)
            self._diagnosed_models.clear()
            self._context_limits.clear()

    async def _unload_model(self, instance_id: str) -> None:
        """Unload the specified instance through LM Studio's native API."""
        try:
            response = await self._client.post(
                "/api/v1/models/unload", json={"instance_id": instance_id}
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"LM Studio could not unload model instance {instance_id} "
                f"({exc.response.status_code})."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "LM Studio model unload failed; check native REST API support."
            ) from exc

    @staticmethod
    def output_options(limit: int | None) -> dict[str, int]:
        if limit is None:
            return {}
        if type(limit) is not int or not 1 <= limit <= 1_000_000:
            raise ValueError("Output token limit must be an integer from 1 to 1000000.")
        return {"max_output_tokens": limit}

    @staticmethod
    def verify_output_limit(response: dict[str, Any], limit: int | None) -> dict[str, Any]:
        if limit is not None:
            if response.get("usage") is None:
                raise ProviderError("Cannot verify output token limit: provider omitted usage.")
            usage = TokenUsage.model_validate(response["usage"])
            if usage.completion_tokens > limit:
                raise ProviderError("Provider exceeded the requested output token limit.")
        return response

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        on_admission: AdmissionCallback | None = None,
    ) -> dict[str, Any]:
        async with self._inference_lock:
            if self.exact_context and max_output_tokens is None:
                max_output_tokens = 512
            options = self.output_options(max_output_tokens)
            prepared = prepare_tools(tools)
            await self._admit_context(
                model,
                messages,
                prepared,
                max_output_tokens=max_output_tokens,
                on_admission=on_admission,
            )
            return await self._chat(model=model, messages=messages, tools=prepared, **options)

    async def _admit_context(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        max_output_tokens: int | None,
        on_admission: AdmissionCallback | None = None,
    ) -> None:
        # Caller owns the inference/lifecycle lock throughout counting and prediction.
        if not self.exact_context:
            self._check_context(model, messages, tools, max_output_tokens=max_output_tokens)
            return
        from pydantic import ValidationError

        from xaa.providers.tokenization import PromptCountError, PromptCountRequest

        try:
            request = PromptCountRequest.model_validate(
                {"model": model, "messages": messages, "tools": tools}
            )
            counted = await self._count_prompt_owned(request)
        except (PromptCountError, ValidationError):
            raise ContextLimitError(
                "Exact context admission could not count this request. Confirm the selected "
                "instance is already loaded and the SDK supports its prompt format."
            ) from None
        if counted.model != model:
            raise ContextLimitError("Prompt counter returned a different model instance.")
        assert max_output_tokens is not None
        if counted.prompt_tokens + max_output_tokens > counted.context_length:
            raise ContextLimitError(
                f"Prompt ({counted.prompt_tokens}) plus output allowance ({max_output_tokens}) "
                f"exceeds the loaded context ({counted.context_length})."
            )
        if on_admission is not None:
            on_admission(counted.prompt_tokens, max_output_tokens)

    async def _count_prompt_owned(self, request: PromptCountRequest) -> PromptCount:
        from xaa.providers.tokenization import count_prompt

        return await count_prompt(self.base_url, request, self._credential, timeout=5)

    def _check_context(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        max_output_tokens: int | None = None,
    ) -> None:
        limit = self._context_limits.get(model)
        if limit is None:
            return
        # UTF-8 bytes plus template allowance are a deliberately conservative
        # estimate, with the selected output allowance reserved separately.
        # No history or tool/result pairs are silently discarded.
        estimate = (
            len(
                json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False).encode(
                    "utf-8"
                )
            )
            + 128 * len(messages)
            + 1024
            + (max_output_tokens or 0)
        )
        if estimate > limit:
            raise ContextLimitError(
                f"Request exceeds the conservative prompt-plus-output context budget for {model} ({limit} tokens). Select a larger loaded context or reduce input size."
            )

    async def _chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
        if max_output_tokens is not None:
            self.output_options(max_output_tokens)
            payload["max_tokens"] = max_output_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            response = await self._client.post("/v1/chat/completions", json=payload)
            response.raise_for_status()
            return self.verify_output_limit(response.json(), max_output_tokens)
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"LM Studio rejected chat request ({exc.response.status_code}). "
                "The selected model or server may not support tool calling."
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                "LM Studio chat request failed; check the local server status."
            ) from exc

    async def chat_streamed(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        on_admission: AdmissionCallback | None = None,
        on_content: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        if self.exact_context and max_output_tokens is None:
            max_output_tokens = 512
        assembler = CompletionAssembler()
        generator = self.stream(
            model=model,
            messages=messages,
            tools=tools,
            on_admission=on_admission,
            **self.output_options(max_output_tokens),
        )
        try:
            async for raw in generator:
                piece = assembler.feed(raw)
                if piece and on_content is not None:
                    on_content(piece)
        finally:
            await generator.aclose()
        return self.verify_output_limit(assembler.complete(), max_output_tokens)

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
        on_admission: AdmissionCallback | None = None,
    ) -> AsyncGenerator[str]:
        async with self._inference_lock:
            if self.exact_context and max_output_tokens is None:
                max_output_tokens = 512
            options = self.output_options(max_output_tokens)
            prepared = prepare_tools(tools)
            await self._admit_context(
                model,
                messages,
                prepared,
                max_output_tokens=max_output_tokens,
                on_admission=on_admission,
            )
            generator = self._stream(model=model, messages=messages, tools=prepared, **options)
            try:
                async for chunk in generator:
                    yield chunk
            finally:
                await generator.aclose()

    async def _stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
    ) -> AsyncGenerator[str]:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if max_output_tokens is not None:
            self.output_options(max_output_tokens)
            payload["max_tokens"] = max_output_tokens
            payload["stream_options"] = {"include_usage": True}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            async with self._client.stream(
                "POST", "/v1/chat/completions", json=payload
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: ") and line[6:] != "[DONE]":
                        yield line[6:]
        except httpx.HTTPError as exc:
            raise ProviderError("LM Studio response stream failed or was interrupted.") from exc

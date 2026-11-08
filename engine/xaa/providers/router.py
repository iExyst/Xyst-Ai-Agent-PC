"""Stable model identifiers and dispatch for explicitly configured providers."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from xaa.providers.lm_studio import LMStudioProvider, ModelInfo, ProviderError
from xaa.providers.openai_compatible import OpenAICompatibleProvider

OPENAI_MODEL_PREFIX = "openai-compatible::"
OPENAI_INSTANCE_PREFIX = "openai-compatible::"


class ProviderRouter:
    """Keep model calls attached to the adapter encoded in their discovered ID.

    LM Studio IDs remain unchanged for backwards compatibility. Optional provider
    IDs are namespaced so saved runs and specialist routes survive settings changes.
    """

    def __init__(
        self,
        lm_studio: LMStudioProvider,
        openai_compatible: OpenAICompatibleProvider | None = None,
    ) -> None:
        self.lm_studio = lm_studio
        self.openai_compatible = openai_compatible
        self._retired: list[OpenAICompatibleProvider] = []
        self.exact_context = lm_studio.exact_context

    @staticmethod
    def validate_endpoint(endpoint: str) -> str:
        return LMStudioProvider.validate_endpoint(endpoint)

    async def configure_endpoint(self, endpoint: str) -> str:
        return await self.lm_studio.configure_endpoint(endpoint)

    async def configure_credential(self, token: str | None) -> None:
        await self.lm_studio.configure_credential(token)

    async def configure_openai_compatible(self, endpoint: str | None, token: str | None) -> None:
        normalized = OpenAICompatibleProvider.validate_endpoint(endpoint) if endpoint else None
        previous = self.openai_compatible
        replacement = (
            OpenAICompatibleProvider(normalized, token) if normalized is not None else None
        )
        self.openai_compatible = replacement
        if previous is not None:
            # In-flight API requests may already hold this client reference. Keep
            # replaced clients alive until shutdown so settings changes cannot
            # tear down an active streaming response.
            self._retired.append(previous)

    def _resolve(self, model: str) -> tuple[LMStudioProvider | OpenAICompatibleProvider, str]:
        if model.startswith(OPENAI_MODEL_PREFIX):
            if self.openai_compatible is None:
                raise ProviderError("The selected OpenAI-compatible provider is not configured.")
            raw_model = model.removeprefix(OPENAI_MODEL_PREFIX)
            if not raw_model:
                raise ProviderError("The selected provider model identifier is invalid.")
            return self.openai_compatible, raw_model
        if "::" in model:
            raise ProviderError("The selected provider model identifier is not supported.")
        return self.lm_studio, model

    async def health(self) -> bool:
        return await self.lm_studio.health()

    async def models(self) -> list[ModelInfo]:
        models = await self.lm_studio.models()
        if self.openai_compatible is not None:
            try:
                optional = await self.openai_compatible.models()
            except ProviderError:
                optional = []
            models.extend(
                item.model_copy(update={"id": OPENAI_MODEL_PREFIX + item.id}) for item in optional
            )
        return models

    def output_options(self, limit: int | None) -> dict[str, int]:
        return LMStudioProvider.output_options(limit)

    def exact_context_for(self, model: str) -> bool:
        provider, _raw_model = self._resolve(model)
        return provider.exact_context

    @property
    def base_url(self) -> str:
        return self.lm_studio.base_url

    async def chat(self, *, model: str, **kwargs: Any) -> dict[str, Any]:
        provider, raw_model = self._resolve(model)
        return await provider.chat(model=raw_model, **kwargs)

    async def chat_streamed(self, *, model: str, **kwargs: Any) -> dict[str, Any]:
        provider, raw_model = self._resolve(model)
        return await provider.chat_streamed(model=raw_model, **kwargs)

    async def stream(self, *, model: str, **kwargs: Any) -> AsyncIterator[str]:
        provider, raw_model = self._resolve(model)
        async for chunk in provider.stream(model=raw_model, **kwargs):
            yield chunk

    async def diagnose_tools(
        self, model: str, *, request: Callable[..., Any] | None = None
    ) -> bool:
        provider, raw_model = self._resolve(model)
        if request is None:
            return await provider.diagnose_tools(raw_model)

        async def wrapped(
            _selected: str,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
        ) -> dict[str, Any]:
            return await request(model, messages, tools)

        return await provider.diagnose_tools(raw_model, request=wrapped)

    async def count_prompt(self, request: Any, *, timeout: float = 20) -> Any:
        del timeout
        provider, raw_model = self._resolve(request.model)
        return await provider.count_prompt(request.model_copy(update={"model": raw_model}))

    async def load_model(self, model: str, *, context_length: int | None = None) -> str:
        provider, raw_model = self._resolve(model)
        instance = await provider.load_model(raw_model, context_length=context_length)
        if provider is self.openai_compatible:
            return OPENAI_INSTANCE_PREFIX + instance
        return instance

    async def unload_model(self, instance_id: str) -> None:
        if instance_id.startswith(OPENAI_INSTANCE_PREFIX):
            if self.openai_compatible is None:
                raise ProviderError("The selected OpenAI-compatible provider is not configured.")
            await self.openai_compatible.unload_model(
                instance_id.removeprefix(OPENAI_INSTANCE_PREFIX)
            )
            return
        await self.lm_studio.unload_model(instance_id)

    async def transcribe_scanned_pages(
        self, *, model: str, objective: str, pages: list[tuple[int, bytes]]
    ) -> tuple[str, list[tuple[str, dict[str, int]]]]:
        provider, raw_model = self._resolve(model)
        return await provider.transcribe_scanned_pages(
            model=raw_model, objective=objective, pages=pages
        )

    async def close(self) -> None:
        await self.lm_studio.close()
        if self.openai_compatible is not None:
            await self.openai_compatible.close()
        for provider in self._retired:
            await provider.close()

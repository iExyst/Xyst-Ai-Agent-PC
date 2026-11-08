"""OpenAI-compatible chat adapter for explicitly selected HTTPS/loopback servers."""

from __future__ import annotations

from typing import cast
from urllib.parse import urlsplit

import httpx

from xaa.providers.lm_studio import (
    LMStudioProvider,
    ModelCapabilities,
    ModelInfo,
    ProviderError,
)
from xaa.providers.tokenization import PromptCount, PromptCountError, PromptCountRequest


class OpenAICompatibleProvider(LMStudioProvider):
    """Use the Chat Completions protocol without assuming LM Studio-only endpoints.

    The endpoint must be explicitly supplied by the user. Remote endpoints require HTTPS;
    plaintext HTTP is accepted only for a numeric or named loopback address. Model
    lifecycle, exact SDK tokenization, and scanned-PDF OCR remain provider-specific.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized = self.validate_endpoint(base_url)
        super().__init__(normalized, token, exact_context=False, transport=transport)
        self._provider_label = "OpenAI-compatible provider"
        self._output_token_parameter = "max_completion_tokens"

    @staticmethod
    def validate_endpoint(endpoint: str) -> str:
        if not 1 <= len(endpoint) <= 2048:
            raise ValueError("Provider address must be a short HTTP(S) server URL.")
        try:
            parsed = urlsplit(endpoint)
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            raise ValueError("Provider address is not a valid HTTP(S) URL.") from None
        if (
            parsed.scheme not in {"http", "https"}
            or host is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65535)
        ):
            raise ValueError(
                "Provider address must be an HTTP(S) origin without credentials or a path."
            )
        if parsed.scheme == "http" and host.casefold() != "localhost":
            import ipaddress

            try:
                if not ipaddress.ip_address(host).is_loopback:
                    raise ValueError("Plain HTTP is allowed only for a loopback provider.")
            except ValueError:
                raise ValueError("Plain HTTP is allowed only for a loopback provider.") from None
        if parsed.scheme == "http" and host.casefold() == "localhost":
            host = "127.0.0.1"
        authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"{parsed.scheme}://{authority}{f':{port}' if port is not None else ''}"

    async def health(self) -> bool:
        try:
            response = await self._client.get("/v1/models", timeout=5.0)
            response.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"Cannot reach the configured OpenAI-compatible provider at {self.base_url}."
            ) from exc

    async def models(self) -> list[ModelInfo]:
        try:
            response = await self._client.get("/v1/models")
            response.raise_for_status()
            payload: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError("OpenAI-compatible model discovery failed at /v1/models.") from exc
        if not isinstance(payload, dict):
            raise ProviderError("OpenAI-compatible model inventory has an invalid shape.")
        entries: object = cast(dict[str, object], payload).get("data")
        if not isinstance(entries, list):
            raise ProviderError("OpenAI-compatible model inventory omitted its data list.")
        result: list[ModelInfo] = []
        seen: set[str] = set()
        for entry in cast(list[object], entries):
            if not isinstance(entry, dict):
                continue
            raw_id: object = cast(dict[str, object], entry).get("id")
            # Leave room for the adapter namespace in the persisted 256-character
            # run/specialist model field.
            if not isinstance(raw_id, str) or not raw_id or len(raw_id) > 237 or raw_id in seen:
                continue
            seen.add(raw_id)
            result.append(ModelInfo(id=raw_id, capabilities=ModelCapabilities()))
        return result

    async def count_prompt(
        self, request: PromptCountRequest, *, timeout: float = 20
    ) -> PromptCount:
        del request, timeout
        raise PromptCountError(
            "Exact token counting is unavailable for this provider. Disable pre-inference token reservations."
        )

    async def load_model(self, model: str, *, context_length: int | None = None) -> str:
        del model, context_length
        raise ProviderError(
            "This provider does not expose model loading. Load the selected model in its provider service."
        )

    async def unload_model(self, instance_id: str) -> None:
        del instance_id
        raise ProviderError("This provider does not expose model unloading.")

    async def transcribe_scanned_pages(
        self,
        *,
        model: str,
        objective: str,
        pages: list[tuple[int, bytes]],
    ) -> tuple[str, list[tuple[str, dict[str, int]]]]:
        del model, objective, pages
        raise ProviderError(
            "Scanned PDF OCR requires LM Studio's native vision endpoint; use a text-based document with this provider."
        )

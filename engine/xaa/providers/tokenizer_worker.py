"""Fixed tokenizer worker. No inference, model loading, tools, API service or database."""

import sys
from importlib.metadata import version
from typing import Any, Protocol, cast

import lmstudio as lms
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from xaa.providers.tokenization import (
    PromptCount,
    PromptCountRequest,
    TokenizerResponse,
    make_chat,
    tokenizer_host,
)


class SyncPromptFormatter(Protocol):
    def apply_prompt_template(self, history: lms.Chat, opts: dict[str, Any]) -> str: ...


class TokenizerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_url: str
    request: PromptCountRequest
    api_token: SecretStr = Field(max_length=512)


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Tokenizer input limit.")
        request = TokenizerRequest.model_validate_json(raw)
        host = tokenizer_host(request.base_url)
        chat = make_chat(request.request.messages)
        # Client.__enter__ performs a separate one-second HTTP health probe.
        # On loaded servers it can time out even when the SDK WebSocket is healthy;
        # the explicit host and next SDK RPC provide the needed connection check.
        client = lms.Client(host, api_token=request.api_token.get_secret_value())
        try:
            matches = [
                model
                for model in client.llm.list_loaded()
                if model.identifier == request.request.model
            ]
            if len(matches) != 1:
                raise ValueError("Exactly one already-loaded instance is required.")
            model = matches[0]
            # Public SDK method, with an explicit typing boundary for generic tool schemas.
            formatted = cast(SyncPromptFormatter, model).apply_prompt_template(
                chat, {"toolDefinitions": request.request.tools or []}
            )
            count = PromptCount(
                model=model.identifier,
                prompt_tokens=len(model.tokenize(formatted)),
                context_length=model.get_context_length(),
                sdk_version=version("lmstudio"),
            )
        finally:
            client.close()
        response = TokenizerResponse(ok=True, count=count)
    except Exception as exc:  # noqa: BLE001 - fixed worker boundary returns only an exception class, never payloads
        response = TokenizerResponse(ok=False, error=type(exc).__name__)
    sys.stdout.write(response.model_dump_json())
    sys.stdout.flush()


if __name__ == "__main__":
    main()

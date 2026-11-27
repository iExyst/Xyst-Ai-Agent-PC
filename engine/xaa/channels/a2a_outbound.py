"""Owner-configured outbound A2A JSON-RPC client."""

from __future__ import annotations

import base64
import json
import ssl
from collections.abc import Mapping
from typing import Any, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

MAX_RESPONSE_BYTES = 1_048_576
MAX_AGENT_CARD_BYTES = 65_536


class A2AOutboundError(ValueError):
    """A remote A2A operation failed without exposing response or credential data."""


class A2AUnsupportedInputError(A2AOutboundError):
    """The peer does not advertise support for a requested media type."""


def _sanitize_remote_task(value: dict[str, Any]) -> dict[str, Any]:
    """Remove peer-supplied attachment bytes and URLs before task history is persisted."""

    def sanitize(item: Any) -> Any:
        if isinstance(item, list):
            parts = cast(list[Any], item)
            return [sanitize(part) for part in parts]
        if isinstance(item, dict):
            fields = cast(dict[str, Any], item)
            if "raw" in fields or "url" in fields:
                media_type = fields.get("mediaType")
                return {
                    "mediaType": media_type
                    if isinstance(media_type, str)
                    else "application/octet-stream",
                    "text": "[Remote attachment omitted by X.A.A.]",
                }
            return {key: sanitize(nested) for key, nested in fields.items()}
        return item

    return cast(dict[str, Any], sanitize(value))


def validate_agent_card_url(value: str) -> str:
    if len(value) > 2048:
        raise ValueError("Agent Card URL exceeds 2048 characters.")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.path and not parsed.path.startswith("/"))
    ):
        raise ValueError("Agent Card URL must be an HTTPS URL without user info or query data.")
    try:
        _ = parsed.port
    except ValueError:
        raise ValueError("Agent Card URL contains an invalid port.") from None
    return value


def _same_origin(first: str, second: str) -> bool:
    left, right = urlsplit(first), urlsplit(second)
    left_port = left.port or 443
    right_port = right.port or 443
    return (
        left.scheme == right.scheme == "https"
        and left.hostname is not None
        and left.hostname.casefold() == (right.hostname or "").casefold()
        and left_port == right_port
    )


class A2AOutboundClient:
    def __init__(
        self,
        *,
        verify: ssl.SSLContext | bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._verify = verify
        self._transport = transport

    async def send_task(
        self,
        *,
        agent_card_url: str,
        bearer_token: str,
        text: str,
        message_id: str,
        image: tuple[bytes, str] | None = None,
    ) -> dict[str, Any]:
        card_url = validate_agent_card_url(agent_card_url)
        async with self._client(bearer_token) as client:
            card = await self._json(client, "GET", card_url, limit=MAX_AGENT_CARD_BYTES)
            endpoint = self._jsonrpc_endpoint(card, card_url)
            parts: list[dict[str, str]] = [{"text": text, "mediaType": "text/plain"}]
            if image is not None:
                image_bytes, media_type = image
                input_modes = card.get("defaultInputModes")
                if not isinstance(input_modes, list) or media_type not in input_modes:
                    raise A2AUnsupportedInputError(
                        "The peer does not advertise support for this image type."
                    )
                parts.append(
                    {
                        "raw": base64.b64encode(image_bytes).decode("ascii"),
                        "mediaType": media_type,
                    }
                )
            response = await self._json(
                client,
                "POST",
                endpoint,
                limit=MAX_RESPONSE_BYTES,
                json_body={
                    "jsonrpc": "2.0",
                    "id": message_id,
                    "method": "SendMessage",
                    "params": {
                        "message": {
                            "messageId": message_id,
                            "role": "ROLE_USER",
                            "parts": parts,
                        }
                    },
                },
            )
        result = self._result(response, expected_id=message_id)
        task_value = result.get("task", result)
        if not isinstance(task_value, dict):
            raise A2AOutboundError("The peer did not return an A2A task.")
        task = cast(dict[str, Any], task_value)
        if not isinstance(task.get("id"), str) or not task["id"]:
            raise A2AOutboundError("The peer returned an invalid task identity.")
        return _sanitize_remote_task(task)

    async def get_task(
        self, *, agent_card_url: str, bearer_token: str, task_id: str
    ) -> dict[str, Any]:
        card_url = validate_agent_card_url(agent_card_url)
        request_id = uuid4().hex
        async with self._client(bearer_token) as client:
            card = await self._json(client, "GET", card_url, limit=MAX_AGENT_CARD_BYTES)
            endpoint = self._jsonrpc_endpoint(card, card_url)
            response = await self._json(
                client,
                "POST",
                endpoint,
                limit=MAX_RESPONSE_BYTES,
                json_body={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "GetTask",
                    "params": {"id": task_id, "historyLength": 100},
                },
            )
        result = self._result(response, expected_id=request_id)
        task_value = result.get("task", result)
        if not isinstance(task_value, dict):
            raise A2AOutboundError("The peer returned a mismatched A2A task.")
        task_value = cast(dict[str, Any], task_value)
        if task_value.get("id") != task_id:
            raise A2AOutboundError("The peer returned a mismatched A2A task.")
        return _sanitize_remote_task(task_value)

    async def cancel_task(
        self, *, agent_card_url: str, bearer_token: str, task_id: str
    ) -> dict[str, Any]:
        card_url = validate_agent_card_url(agent_card_url)
        request_id = uuid4().hex
        async with self._client(bearer_token) as client:
            card = await self._json(client, "GET", card_url, limit=MAX_AGENT_CARD_BYTES)
            endpoint = self._jsonrpc_endpoint(card, card_url)
            response = await self._json(
                client,
                "POST",
                endpoint,
                limit=MAX_RESPONSE_BYTES,
                json_body={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "CancelTask",
                    "params": {"id": task_id},
                },
            )
        result = self._result(response, expected_id=request_id)
        task_value = result.get("task", result)
        if not isinstance(task_value, dict):
            raise A2AOutboundError("The peer did not return the canceled A2A task.")
        task = cast(dict[str, Any], task_value)
        if task.get("id") != task_id:
            raise A2AOutboundError("The peer returned a mismatched canceled A2A task.")
        return _sanitize_remote_task(task)

    def _client(self, bearer_token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self._verify,
            transport=self._transport,
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(10.0, connect=5.0),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            headers={"Authorization": f"Bearer {bearer_token}"},
        )

    async def _json(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        limit: int,
        json_body: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        try:
            async with client.stream(
                method,
                url,
                json=json_body,
                headers={"A2A-Version": "1.0"},
            ) as response:
                if response.is_redirect:
                    raise A2AOutboundError("The peer redirected an A2A request.")
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > limit:
                        raise A2AOutboundError("The peer response exceeded its size limit.")
        except A2AOutboundError:
            raise
        except (httpx.HTTPError, OSError, ValueError):
            raise A2AOutboundError("The configured A2A peer could not be reached safely.") from None
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise A2AOutboundError("The peer returned invalid JSON.") from None
        if not isinstance(value, dict):
            raise A2AOutboundError("The peer response must be a JSON object.")
        return cast(dict[str, Any], value)

    @staticmethod
    def _jsonrpc_endpoint(card: Mapping[str, Any], card_url: str) -> str:
        interfaces = card.get("supportedInterfaces")
        if not isinstance(interfaces, list) or not interfaces:
            raise A2AOutboundError("The Agent Card has no supported interface.")
        for item in cast(list[Any], interfaces):
            if not isinstance(item, dict):
                continue
            interface = cast(dict[str, Any], item)
            endpoint = interface.get("url")
            if (
                interface.get("protocolBinding") != "JSONRPC"
                or interface.get("protocolVersion") != "1.0"
                or not isinstance(endpoint, str)
            ):
                continue
            try:
                endpoint = validate_agent_card_url(endpoint)
            except ValueError:
                continue
            if _same_origin(card_url, endpoint):
                return endpoint
        raise A2AOutboundError("The Agent Card has no same-origin A2A 1.0 JSON-RPC interface.")

    @staticmethod
    def _result(response: Mapping[str, Any], *, expected_id: str) -> dict[str, Any]:
        if response.get("jsonrpc") != "2.0" or response.get("id") != expected_id:
            raise A2AOutboundError("The peer returned a mismatched JSON-RPC response.")
        error = response.get("error")
        if isinstance(error, dict):
            raise A2AOutboundError("The configured A2A peer rejected the request.")
        result = response.get("result")
        if not isinstance(result, dict):
            raise A2AOutboundError("The peer returned an invalid JSON-RPC result.")
        return cast(dict[str, Any], result)

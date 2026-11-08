"""Signed, text-only generic webhook ingress."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MAX_WEBHOOK_BYTES = 64 * 1024
MAX_CLOCK_SKEW_SECONDS = 300
_EVENT_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_SIGNATURE = re.compile(r"sha256=([0-9a-f]{64})\Z")


class WebhookEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sender_id: str = Field(min_length=1, max_length=256)
    conversation_id: str | None = Field(default=None, max_length=256)
    text: str = Field(min_length=1, max_length=10_000)


class InvalidWebhook(ValueError):
    """A signed webhook failed validation or authentication."""


def sign_webhook(secret: str, timestamp: str, event_id: str, body: bytes) -> str:
    """Return the X.A.A. signature for timestamp, event identity and exact body."""
    digest = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + event_id.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def parse_delivery(
    *,
    secret: str,
    timestamp: str | None,
    event_id: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> tuple[WebhookEvent, str, str]:
    """Authenticate one bounded event and return its stable ID and body digest."""
    if len(body) > MAX_WEBHOOK_BYTES:
        raise InvalidWebhook("Webhook body exceeds 64 KiB.")
    if (
        timestamp is None
        or not 10 <= len(timestamp) <= 12
        or not timestamp.isascii()
        or not timestamp.isdecimal()
    ):
        raise InvalidWebhook("Webhook timestamp is missing or invalid.")
    if event_id is None or _EVENT_ID.fullmatch(event_id) is None:
        raise InvalidWebhook("Webhook event ID is missing or invalid.")
    if signature is None:
        raise InvalidWebhook("Webhook signature is missing.")
    match = _SIGNATURE.fullmatch(signature)
    if match is None:
        raise InvalidWebhook("Webhook signature format is invalid.")
    try:
        sent_at = int(timestamp)
    except ValueError:
        raise InvalidWebhook("Webhook timestamp is invalid.") from None
    current = time.time() if now is None else now
    if abs(current - sent_at) > MAX_CLOCK_SKEW_SECONDS:
        raise InvalidWebhook("Webhook timestamp is outside the five-minute acceptance window.")
    expected = sign_webhook(secret, timestamp, event_id, body)
    if not hmac.compare_digest(expected, signature):
        raise InvalidWebhook("Webhook signature did not match.")
    try:
        parsed = WebhookEvent.model_validate_json(body)
    except (ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise InvalidWebhook("Webhook payload must contain a valid text event.") from exc
    return parsed, event_id, hashlib.sha256(body).hexdigest()


def request_identity(event_id: str) -> str:
    """Derive a bounded SQLite idempotency identity without storing sender data."""
    return "webhook:" + hashlib.sha256(event_id.encode("ascii")).hexdigest()

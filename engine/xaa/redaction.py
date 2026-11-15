"""Shared secret redaction for exported data and local diagnostics."""

from __future__ import annotations

import re
from typing import Any, cast

_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_AUTH_HEADER = re.compile(r"(?im)(\bauthorization\s*[:=]\s*)[^\r\n]+")
_COOKIE_HEADER = re.compile(r"(?im)(\b(?:set-cookie|cookie)\s*:\s*)[^\r\n]+")
_URL_USERINFO = re.compile(r"(?i)\b((?:https?|wss?)://)[^\s/@:]+:[^\s/@]+@")
_DISCORD_WEBHOOK = re.compile(
    r"(?i)\bhttps?://discord(?:app)?\.com/api/webhooks/\d{10,}/[A-Za-z0-9._-]{20,}"
)
_SLACK_WEBHOOK = re.compile(
    r"(?i)\bhttps?://hooks\.slack\.com/services/"
    r"[A-Z0-9]{6,}/[A-Z0-9]{6,}/[A-Za-z0-9]{20,}"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b")
_NAMED_SECRET = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|"
    r"auth[_-]?token|session[_-]?token|oauth[_-]?token|client[_-]?secret|"
    r"webhook[_-]?secret|private[_-]?key|aws[_-]?secret[_-]?access[_-]?key|"
    r"secret[_-]?access[_-]?key|cookie|set[_-]?cookie|password|secret|"
    r"authorization)\b\s*[:=]\s*)"
    r"(['\"]?)[^\s,;\]\[}\"']+"
)
_SECRET_FIELD = re.compile(
    r"(?i)(?:^|[^a-z0-9])(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"id[_-]?token|auth[_-]?token|session[_-]?token|oauth[_-]?token|"
    r"client[_-]?secret|webhook[_-]?secret|private[_-]?key|"
    r"aws[_-]?secret[_-]?access[_-]?key|secret[_-]?access[_-]?key|"
    r"cookie|set[_-]?cookie|password|secret|authorization)(?:$|[^a-z0-9])"
)
_SECRET_FIELD_EXACT = frozenset({"token", "credential", "credentials", "cookie"})
_COMMON_API_TOKEN = re.compile(
    r"(?i)\b(?:sk-(?:proj-|live-|ant-)?[A-Za-z0-9_-]{16,}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{20,}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16})\b"
)
_TELEGRAM_TOKEN = re.compile(r"(?i)(/bot)[0-9]{5,}:[A-Za-z0-9_-]{20,}")


def redact(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    """Redact exact credentials and common secret forms recursively."""
    if isinstance(value, str):
        result = value
        for secret in secrets:
            if len(secret) >= 4:
                result = result.replace(secret, "[REDACTED]")
        result = _AUTH_HEADER.sub(r"\1[REDACTED]", result)
        result = _COOKIE_HEADER.sub(r"\1[REDACTED]", result)
        result = _URL_USERINFO.sub(r"\1[REDACTED]@", result)
        result = _DISCORD_WEBHOOK.sub("[REDACTED]", result)
        result = _SLACK_WEBHOOK.sub("[REDACTED]", result)
        result = _JWT.sub("[REDACTED]", result)
        result = _BEARER.sub("Bearer [REDACTED]", result)
        result = _NAMED_SECRET.sub(r"\1[REDACTED]", result)
        result = _COMMON_API_TOKEN.sub("[REDACTED]", result)
        return _TELEGRAM_TOKEN.sub(r"\1[REDACTED]", result)
    if isinstance(value, list):
        items = cast(list[Any], value)
        return [redact(item, secrets) for item in items]
    if isinstance(value, dict):
        mapping = cast(dict[str, Any], value)
        return {
            key: "[REDACTED]"
            if key.lower() in _SECRET_FIELD_EXACT or _SECRET_FIELD.search(key)
            else redact(item, secrets)
            for key, item in mapping.items()
        }
    return value


def redact_text(value: str, secrets: tuple[str, ...] = ()) -> str:
    """Redact exact credentials and common secret forms from one string."""
    result = redact(value, secrets)
    assert isinstance(result, str)
    return result

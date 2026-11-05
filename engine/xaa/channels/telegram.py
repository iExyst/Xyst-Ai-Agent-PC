"""Telegram Bot API polling and idempotent delivery through the shared run store."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Self, cast
from uuid import uuid4

import httpx

from xaa.credentials import CredentialStore
from xaa.storage.runs import RunState, RunStore, utc_now
from xaa.storage.schedules import quiet_hours_active

logger = logging.getLogger(__name__)
PAIR_COMMAND = re.compile(r"^/pair(?:@[A-Za-z0-9_]+)?\s+([A-Za-z0-9_-]{24,128})$")
MAX_TEXT_CHARS = 4096
OFFSET_RESET_AFTER = timedelta(days=6)
MAX_SCHEDULE_DELIVERY_ATTEMPTS = 3
MAX_TELEGRAM_RETRY_AFTER_SECONDS = 300


def as_mapping(value: object) -> Mapping[str, Any] | None:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else None


class TelegramApiError(RuntimeError):
    def __init__(
        self, message: str, *, definitive: bool, retry_after_seconds: int | None = None
    ) -> None:
        super().__init__(message)
        self.definitive = definitive
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class TelegramIdentity:
    account_id: str
    username: str | None


class TelegramBotApi:
    """Small HTTPS-only Bot API client. Error text never includes the token URL."""

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        if not token or len(token) > 512 or "\n" in token or "\r" in token:
            raise ValueError("Telegram bot token is malformed.")
        self.token = token
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(35, connect=10))
        self.owns_client = client is None

    async def close(self) -> None:
        if self.owns_client:
            await self.client.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def call(self, method: str, payload: Mapping[str, object] | None = None) -> object:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", method):
            raise ValueError("Invalid Telegram method name.")
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                json=dict(payload or {}),
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise TelegramApiError(
                "Telegram request failed before a response was received.", definitive=False
            ) from None
        try:
            body = as_mapping(response.json())
        except ValueError as exc:
            raise TelegramApiError(
                "Telegram returned an invalid response.", definitive=False
            ) from exc
        error_code = body.get("error_code") if body is not None else None
        if response.status_code >= 500 or (type(error_code) is int and error_code >= 500):
            raise TelegramApiError("Telegram service returned a server error.", definitive=False)
        if response.status_code == 429 or error_code == 429:
            parameters = as_mapping(body.get("parameters")) if body is not None else None
            retry_after = parameters.get("retry_after") if parameters is not None else None
            delay = (
                retry_after
                if type(retry_after) is int and 1 <= retry_after <= MAX_TELEGRAM_RETRY_AFTER_SECONDS
                else None
            )
            raise TelegramApiError(
                "Telegram rate limited the request.",
                definitive=True,
                retry_after_seconds=delay,
            )
        if body is None or body.get("ok") is not True:
            raise TelegramApiError("Telegram rejected the request.", definitive=True)
        return body.get("result")

    async def get_me(self) -> TelegramIdentity:
        result = as_mapping(await self.call("getMe"))
        if result is None or not isinstance(result.get("id"), int):
            raise TelegramApiError("Telegram returned an invalid bot identity.", definitive=False)
        username = result.get("username")
        return TelegramIdentity(str(result["id"]), username if isinstance(username, str) else None)

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, object] = {"timeout": 20, "limit": 100, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = await self.call("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramApiError("Telegram returned malformed updates.", definitive=False)
        updates = cast(list[object], result)
        if any(as_mapping(item) is None for item in updates):
            raise TelegramApiError("Telegram returned malformed updates.", definitive=False)
        return [cast(dict[str, Any], item) for item in updates]

    async def send_message(self, chat_id: str, text: str) -> int:
        result = as_mapping(
            await self.call("sendMessage", {"chat_id": chat_id, "text": text[:4096]})
        )
        if result is None or not isinstance(result.get("message_id"), int):
            raise TelegramApiError("Telegram returned an invalid send result.", definitive=False)
        return int(result["message_id"])


class TelegramStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    @staticmethod
    def hash_pairing_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def create_pairing(self, code: str, *, ttl: timedelta = timedelta(minutes=5)) -> str:
        now = datetime.now(UTC)
        expires = now + ttl
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM telegram_pairing_codes WHERE expires_at<=? OR consumed_at IS NOT NULL",
                (now.isoformat(),),
            )
            connection.execute(
                "INSERT INTO telegram_pairing_codes (code_hash,created_at,expires_at,consumed_at) VALUES (?,?,?,NULL)",
                (self.hash_pairing_code(code), now.isoformat(), expires.isoformat()),
            )
        return expires.isoformat()

    def save_bot(self, identity: TelegramIdentity) -> None:
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute(
                "INSERT INTO telegram_bots (account_id,username,created_at,updated_at) VALUES (?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET username=excluded.username,updated_at=excluded.updated_at",
                (identity.account_id, identity.username, now, now),
            )

    def revoke_users(self, account_id: str) -> int:
        """Remove authorization for every user paired to this bot account."""
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM telegram_pairing_codes")
            revoked = connection.execute(
                "UPDATE channel_bindings SET authorized=0 WHERE channel='telegram' AND account_id=? AND authorized=1",
                (account_id,),
            ).rowcount
            pending = connection.execute(
                "SELECT execution_id FROM schedule_deliveries WHERE account_id=? AND state='pending' AND external_identity IN (SELECT external_identity FROM channel_bindings WHERE channel='telegram' AND account_id=? AND authorized=0)",
                (account_id, account_id),
            ).fetchall()
            connection.execute(
                "UPDATE schedule_deliveries SET state='revoked',updated_at=? WHERE account_id=? AND state='pending' AND external_identity IN (SELECT external_identity FROM channel_bindings WHERE channel='telegram' AND account_id=? AND authorized=0)",
                (utc_now(), account_id, account_id),
            )
            for row in pending:
                connection.execute(
                    "UPDATE schedule_executions SET delivery_evidence='revoked' WHERE id=?",
                    (row["execution_id"],),
                )
            return revoked

    def linked_users(self, account_id: str) -> list[str]:
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT external_identity FROM channel_bindings WHERE channel='telegram' AND account_id=? AND authorized=1 ORDER BY external_identity",
                (account_id,),
            ).fetchall()
        return [str(row["external_identity"]) for row in rows]

    def latest_account_id(self) -> str | None:
        with self.runs.connection() as connection:
            row = connection.execute(
                "SELECT account_id FROM telegram_bots ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        return str(row["account_id"]) if row is not None else None

    def profile(self, account_id: str | None = None) -> dict[str, Any]:
        with self.runs.connection() as connection:
            if account_id is None:
                bot = connection.execute(
                    "SELECT * FROM telegram_bots ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
                bindings = connection.execute(
                    "SELECT count(*) FROM channel_bindings WHERE channel='telegram' AND authorized=1"
                ).fetchone()[0]
                updates = connection.execute("SELECT count(*) FROM telegram_updates").fetchone()[0]
                deliveries = connection.execute(
                    "SELECT delivery_state,count(*) AS amount FROM telegram_updates WHERE delivery_state IS NOT NULL GROUP BY delivery_state"
                ).fetchall()
            else:
                bot = connection.execute(
                    "SELECT * FROM telegram_bots WHERE account_id=?", (account_id,)
                ).fetchone()
                bindings = connection.execute(
                    "SELECT count(*) FROM channel_bindings WHERE channel='telegram' AND account_id=? AND authorized=1",
                    (account_id,),
                ).fetchone()[0]
                updates = connection.execute(
                    "SELECT count(*) FROM telegram_updates WHERE account_id=?", (account_id,)
                ).fetchone()[0]
                deliveries = connection.execute(
                    "SELECT delivery_state,count(*) AS amount FROM telegram_updates WHERE account_id=? AND delivery_state IS NOT NULL GROUP BY delivery_state",
                    (account_id,),
                ).fetchall()
        return {
            "account_id": bot["account_id"] if bot else None,
            "username": bot["username"] if bot else None,
            "linked_users": int(bindings),
            "processed_updates": int(updates),
            "deliveries": {str(row["delivery_state"]): int(row["amount"]) for row in deliveries},
        }

    def current_offset(self, account_id: str, now: datetime | None = None) -> int | None:
        current = now or datetime.now(UTC)
        with self.runs.connection() as connection:
            row = connection.execute(
                "SELECT next_offset,last_update_at FROM telegram_offsets WHERE account_id=?",
                (account_id,),
            ).fetchone()
        if row is None:
            return None
        last_update = datetime.fromisoformat(str(row["last_update_at"]))
        if current - last_update > OFFSET_RESET_AFTER:
            # Telegram may randomize the next update_id after a week without updates.
            with self.runs.connection() as connection:
                connection.execute("DELETE FROM telegram_offsets WHERE account_id=?", (account_id,))
            return None
        return int(row["next_offset"])

    def process_update(
        self,
        identity: TelegramIdentity,
        update: Mapping[str, Any],
        *,
        model: str | None,
    ) -> dict[str, Any]:
        update_id = update.get("update_id")
        if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
            return {"state": "invalid", "run_id": None}
        now = utc_now()
        message = as_mapping(update.get("message"))
        user = as_mapping(message.get("from")) if message is not None else None
        chat = as_mapping(message.get("chat")) if message is not None else None
        user_id = user.get("id") if isinstance(user, dict) else None
        chat_id = chat.get("id") if isinstance(chat, dict) else None
        text = message.get("text") if isinstance(message, dict) else None
        private = (
            isinstance(user_id, int)
            and not isinstance(user_id, bool)
            and isinstance(chat_id, int)
            and not isinstance(chat_id, bool)
            and chat_id == user_id
            and chat is not None
            and chat.get("type") == "private"
        )
        user_key = str(user_id) if private else None
        chat_key = str(chat_id) if private else None
        run_id: str | None = None
        state = "ignored"
        session_id: str | None = None
        delivery_state: str | None = None
        content = text.strip() if isinstance(text, str) else ""
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO telegram_updates (account_id,update_id,user_id,chat_id,session_id,run_id,state,delivery_state,delivery_message_id,received_at,updated_at) VALUES (?,?,?,?,NULL,NULL,'processing',NULL,NULL,?,?)",
                (identity.account_id, update_id, user_key, chat_key, now, now),
            ).rowcount
            if inserted == 0:
                previous = connection.execute(
                    "SELECT state,run_id FROM telegram_updates WHERE account_id=? AND update_id=?",
                    (identity.account_id, update_id),
                ).fetchone()
                connection.execute(
                    "INSERT INTO telegram_offsets (account_id,next_offset,last_update_at) VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET next_offset=max(next_offset,excluded.next_offset),last_update_at=excluded.last_update_at",
                    (identity.account_id, update_id + 1, now),
                )
                return {"state": previous["state"], "run_id": previous["run_id"], "duplicate": True}

            if private and content:
                pair_match = PAIR_COMMAND.fullmatch(content)
                if pair_match is not None:
                    code_hash = self.hash_pairing_code(pair_match.group(1))
                    pairing = connection.execute(
                        "SELECT code_hash FROM telegram_pairing_codes WHERE code_hash=? AND expires_at>? AND consumed_at IS NULL",
                        (code_hash, now),
                    ).fetchone()
                    if pairing is not None:
                        connection.execute(
                            "UPDATE telegram_pairing_codes SET consumed_at=? WHERE code_hash=? AND consumed_at IS NULL",
                            (now, code_hash),
                        )
                        binding = connection.execute(
                            "SELECT id,session_id FROM channel_bindings WHERE channel='telegram' AND account_id=? AND external_identity=?",
                            (identity.account_id, user_key),
                        ).fetchone()
                        if binding is None:
                            session_id = str(__import__("uuid").uuid4())
                            connection.execute(
                                "INSERT INTO sessions (id,channel,account_id,title,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                                (
                                    session_id,
                                    "telegram",
                                    identity.account_id,
                                    f"Telegram user {user_key}",
                                    now,
                                    now,
                                ),
                            )
                            connection.execute(
                                "INSERT INTO channel_bindings (id,channel,account_id,external_identity,session_id,identity_link_id,authorized,created_at) VALUES (?,?,?,?,?,NULL,1,?)",
                                (
                                    str(__import__("uuid").uuid4()),
                                    "telegram",
                                    identity.account_id,
                                    user_key,
                                    session_id,
                                    now,
                                ),
                            )
                        else:
                            session_id = str(binding["session_id"])
                            connection.execute(
                                "UPDATE channel_bindings SET authorized=1 WHERE id=?",
                                (binding["id"],),
                            )
                        state = "paired"
                    else:
                        state = "pairing_rejected"
                elif user_key is not None:
                    binding = connection.execute(
                        "SELECT session_id FROM channel_bindings WHERE channel='telegram' AND account_id=? AND external_identity=? AND authorized=1",
                        (identity.account_id, user_key),
                    ).fetchone()
                    if binding is not None:
                        session_id = str(binding["session_id"])
                        if model is None:
                            state = "model_unavailable"
                        elif len(content) > MAX_TEXT_CHARS:
                            state = "message_too_long"
                        else:
                            run_id = str(uuid4())
                            message_id = str(uuid4())
                            sequence = connection.execute(
                                "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?",
                                (session_id,),
                            ).fetchone()[0]
                            connection.execute(
                                "INSERT INTO runs (id,objective,acceptance_criteria,workspace,mode,model,state,created_at,updated_at,revision,lease_owner,lease_expires_at) VALUES (?,?,?,?,?,?,?,?,?,0,NULL,NULL)",
                                (
                                    run_id,
                                    content,
                                    "[]",
                                    None,
                                    "observe",
                                    model,
                                    RunState.QUEUED.value,
                                    now,
                                    now,
                                ),
                            )
                            connection.execute(
                                "INSERT INTO messages (id,session_id,run_id,sequence,role,content,created_at) VALUES (?,?,?,?,?,?,?)",
                                (message_id, session_id, run_id, sequence, "user", content, now),
                            )
                            RunStore.record_event(
                                connection,
                                run_id,
                                "run.queued",
                                {
                                    "state": RunState.QUEUED.value,
                                    "channel": "telegram",
                                    "telegram_update_id": update_id,
                                },
                            )
                            connection.execute(
                                "INSERT INTO run_limits VALUES (?,?,?)", (run_id, 8, 900)
                            )
                            connection.execute("INSERT INTO run_checks VALUES (?, '[]')", (run_id,))
                            connection.execute(
                                "UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id)
                            )
                            state = "queued"
                            delivery_state = "pending"
            connection.execute(
                "UPDATE telegram_updates SET session_id=?,run_id=?,state=?,delivery_state=?,updated_at=? WHERE account_id=? AND update_id=?",
                (session_id, run_id, state, delivery_state, now, identity.account_id, update_id),
            )
            connection.execute(
                "INSERT INTO telegram_offsets (account_id,next_offset,last_update_at) VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET next_offset=max(next_offset,excluded.next_offset),last_update_at=excluded.last_update_at",
                (identity.account_id, update_id + 1, now),
            )
        return {"state": state, "run_id": run_id, "duplicate": False}

    def claim_delivery(self, account_id: str) -> dict[str, Any] | None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT u.update_id,u.chat_id,u.run_id,r.state FROM telegram_updates u JOIN runs r ON r.id=u.run_id WHERE u.account_id=? AND u.delivery_state='pending' AND r.state IN (?,?,?,?) ORDER BY u.update_id LIMIT 1",
                (
                    account_id,
                    RunState.COMPLETED.value,
                    RunState.FAILED.value,
                    RunState.BLOCKED.value,
                    RunState.CANCELLED.value,
                ),
            ).fetchone()
            if row is None:
                return None
            events = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='model.message' ORDER BY sequence DESC",
                (row["run_id"],),
            ).fetchall()
            text: str | None = None
            for event in events:
                payload = json.loads(event["payload"])
                payload_mapping = as_mapping(payload)
                message = as_mapping(payload_mapping.get("message")) if payload_mapping else None
                candidate = message.get("content") if message is not None else None
                if isinstance(candidate, str) and candidate.strip():
                    text = candidate.strip()
                    break
            if str(row["state"]) != RunState.COMPLETED.value:
                text = "X.A.A. could not complete this objective. Open the desktop app to review the saved run status."
            if text is None:
                connection.execute(
                    "UPDATE telegram_updates SET delivery_state='failed',updated_at=? WHERE account_id=? AND update_id=? AND delivery_state='pending'",
                    (utc_now(), account_id, row["update_id"]),
                )
                return None
            changed = connection.execute(
                "UPDATE telegram_updates SET delivery_state='sending',updated_at=? WHERE account_id=? AND update_id=? AND delivery_state='pending'",
                (utc_now(), account_id, row["update_id"]),
            ).rowcount
            if changed != 1:
                return None
            return {
                "update_id": int(row["update_id"]),
                "chat_id": str(row["chat_id"]),
                "run_id": str(row["run_id"]),
                "text": text[:MAX_TEXT_CHARS],
            }

    def finish_delivery(
        self,
        account_id: str,
        update_id: int,
        *,
        state: str,
        message_id: int | None = None,
        text: str | None = None,
    ) -> None:
        if state not in {"sent", "failed", "unknown"}:
            raise ValueError("Invalid Telegram delivery state.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT session_id,run_id FROM telegram_updates WHERE account_id=? AND update_id=? AND delivery_state='sending'",
                (account_id, update_id),
            ).fetchone()
            changed = connection.execute(
                "UPDATE telegram_updates SET delivery_state=?,delivery_message_id=?,updated_at=? WHERE account_id=? AND update_id=? AND delivery_state='sending'",
                (state, message_id, utc_now(), account_id, update_id),
            ).rowcount
            if changed == 1 and state == "sent" and row is not None and text:
                session_id = str(row["session_id"])
                sequence = connection.execute(
                    "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
                connection.execute(
                    "INSERT INTO messages (id,session_id,run_id,sequence,role,content,created_at) VALUES (?,?,?,?,?,?,?)",
                    (
                        str(uuid4()),
                        session_id,
                        row["run_id"],
                        sequence,
                        "assistant",
                        text,
                        utc_now(),
                    ),
                )
                connection.execute(
                    "UPDATE sessions SET updated_at=? WHERE id=?", (utc_now(), session_id)
                )

    def claim_schedule_delivery(
        self, account_id: str, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        current = now or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("Delivery clock must be timezone-aware.")
        current = current.astimezone(UTC)
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            revoked = connection.execute(
                "SELECT execution_id FROM schedule_deliveries d WHERE account_id=? AND state='pending' AND NOT EXISTS (SELECT 1 FROM channel_bindings b WHERE b.channel='telegram' AND b.account_id=d.account_id AND b.external_identity=d.external_identity AND b.authorized=1)",
                (account_id,),
            ).fetchall()
            for row in revoked:
                connection.execute(
                    "UPDATE schedule_deliveries SET state='revoked',updated_at=? WHERE execution_id=? AND state='pending'",
                    (utc_now(), row["execution_id"]),
                )
                connection.execute(
                    "UPDATE schedule_executions SET delivery_evidence='revoked' WHERE id=?",
                    (row["execution_id"],),
                )
            rows = connection.execute(
                "SELECT d.execution_id,d.chat_id,e.run_id,r.state,s.quiet_hours,s.timezone FROM schedule_deliveries d JOIN schedule_executions e ON e.id=d.execution_id JOIN runs r ON r.id=e.run_id JOIN schedules s ON s.id=e.schedule_id WHERE d.account_id=? AND d.state='pending' AND COALESCE(d.next_attempt_at,'')<=? AND r.state IN (?,?,?,?) AND EXISTS (SELECT 1 FROM channel_bindings b WHERE b.channel='telegram' AND b.account_id=d.account_id AND b.external_identity=d.external_identity AND b.authorized=1) ORDER BY d.created_at",
                (
                    account_id,
                    current.isoformat(),
                    RunState.COMPLETED.value,
                    RunState.FAILED.value,
                    RunState.BLOCKED.value,
                    RunState.CANCELLED.value,
                ),
            ).fetchall()
            row = next(
                (
                    candidate
                    for candidate in rows
                    if not quiet_hours_active(
                        candidate["quiet_hours"], str(candidate["timezone"]), current
                    )
                ),
                None,
            )
            if row is None:
                return None
            text: str | None = None
            if str(row["state"]) == RunState.COMPLETED.value:
                events = connection.execute(
                    "SELECT payload FROM run_events WHERE run_id=? AND event_type='model.message' ORDER BY sequence DESC",
                    (row["run_id"],),
                ).fetchall()
                for event in events:
                    payload = as_mapping(json.loads(event["payload"]))
                    message = as_mapping(payload.get("message")) if payload else None
                    candidate = message.get("content") if message else None
                    if isinstance(candidate, str) and candidate.strip():
                        text = candidate.strip()
                        break
                if text is None:
                    connection.execute(
                        "UPDATE schedule_deliveries SET state='failed',updated_at=? WHERE execution_id=? AND state='pending'",
                        (utc_now(), row["execution_id"]),
                    )
                    connection.execute(
                        "UPDATE schedule_executions SET delivery_evidence='failed' WHERE id=?",
                        (row["execution_id"],),
                    )
                    return None
            else:
                text = "X.A.A. could not complete this scheduled objective. Open the desktop app to review the saved run status."
            changed = connection.execute(
                "UPDATE schedule_deliveries SET state='sending',attempt_count=attempt_count+1,next_attempt_at=NULL,updated_at=? WHERE execution_id=? AND state='pending'",
                (current.isoformat(), row["execution_id"]),
            ).rowcount
            if changed != 1:
                return None
            connection.execute(
                "UPDATE schedule_executions SET delivery_evidence='sending' WHERE id=?",
                (row["execution_id"],),
            )
            return {
                "execution_id": str(row["execution_id"]),
                "chat_id": str(row["chat_id"]),
                "text": text[:MAX_TEXT_CHARS],
            }

    def finish_schedule_delivery(
        self,
        execution_id: str,
        *,
        state: str,
        message_id: int | None = None,
        retry_after_seconds: int | None = None,
        now: datetime | None = None,
    ) -> None:
        if state not in {"sent", "failed", "unknown"}:
            raise ValueError("Invalid Telegram schedule delivery state.")
        if (
            retry_after_seconds is not None
            and not 1 <= retry_after_seconds <= MAX_TELEGRAM_RETRY_AFTER_SECONDS
        ):
            raise ValueError("Telegram retry delay is outside the bounded policy.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT attempt_count FROM schedule_deliveries WHERE execution_id=? AND state='sending'",
                (execution_id,),
            ).fetchone()
            if current is None:
                return
            if (
                state == "failed"
                and retry_after_seconds is not None
                and current["attempt_count"] < MAX_SCHEDULE_DELIVERY_ATTEMPTS
            ):
                current_time = now or datetime.now(UTC)
                if current_time.tzinfo is None:
                    raise ValueError("Delivery clock must be timezone-aware.")
                retry_at = current_time.astimezone(UTC) + timedelta(seconds=retry_after_seconds)
                connection.execute(
                    "UPDATE schedule_deliveries SET state='pending',next_attempt_at=?,updated_at=? WHERE execution_id=? AND state='sending'",
                    (retry_at.isoformat(), utc_now(), execution_id),
                )
                connection.execute(
                    "UPDATE schedule_executions SET delivery_evidence='retrying' WHERE id=?",
                    (execution_id,),
                )
                return
            changed = connection.execute(
                "UPDATE schedule_deliveries SET state=?,message_id=?,next_attempt_at=NULL,updated_at=? WHERE execution_id=? AND state='sending'",
                (state, message_id, utc_now(), execution_id),
            ).rowcount
            if changed == 1:
                connection.execute(
                    "UPDATE schedule_executions SET delivery_evidence=? WHERE id=?",
                    (state, execution_id),
                )

    def reconcile_abandoned_sends(self) -> int:
        """Fence requests interrupted after send intent; never replay an ambiguous send."""
        with self.runs.connection() as connection:
            changed = connection.execute(
                "UPDATE telegram_updates SET delivery_state='unknown',updated_at=? WHERE delivery_state='sending'",
                (utc_now(),),
            ).rowcount
            executions = connection.execute(
                "SELECT execution_id FROM schedule_deliveries WHERE state='sending'"
            ).fetchall()
            connection.execute(
                "UPDATE schedule_deliveries SET state='unknown',updated_at=? WHERE state='sending'",
                (utc_now(),),
            )
            for row in executions:
                connection.execute(
                    "UPDATE schedule_executions SET delivery_evidence='unknown' WHERE id=?",
                    (row["execution_id"],),
                )
            return changed + len(executions)


class TelegramService:
    """Poll authorized private chats and submit Observe runs to the shared supervisor."""

    def __init__(
        self,
        runs: RunStore,
        credentials: CredentialStore,
        submit: Callable[[str], None],
        *,
        api_factory: Callable[[str], TelegramBotApi] = TelegramBotApi,
    ) -> None:
        self.runs = runs
        self.store = TelegramStore(runs)
        self.credentials = credentials
        self.submit = submit
        self.api_factory = api_factory
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="telegram-channel")

    async def close(self) -> None:
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def run(self) -> None:
        while True:
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except (TelegramApiError, sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
                logger.error("Telegram adapter cycle failed (%s)", type(exc).__name__)
            await asyncio.sleep(2)

    async def cycle(self) -> None:
        """Process one poll and delivery pass; injectable for deterministic acceptance."""
        token = self.credentials.get("telegram-bot")
        if not token:
            return
        self.store.reconcile_abandoned_sends()
        async with self.api_factory(token) as api:
            identity = await api.get_me()
            self.store.save_bot(identity)
            offset = self.store.current_offset(identity.account_id)
            for update in await api.get_updates(offset):
                with self.runs.connection() as connection:
                    selected = connection.execute(
                        "SELECT selected_model FROM provider_profiles WHERE id='lm-studio'"
                    ).fetchone()
                result = self.store.process_update(
                    identity,
                    update,
                    model=str(selected[0]) if selected and selected[0] else None,
                )
                if result["run_id"]:
                    self.submit(str(result["run_id"]))
            while (delivery := self.store.claim_delivery(identity.account_id)) is not None:
                try:
                    message_id = await api.send_message(delivery["chat_id"], delivery["text"])
                except TelegramApiError as exc:
                    self.store.finish_delivery(
                        identity.account_id,
                        delivery["update_id"],
                        state="failed" if exc.definitive else "unknown",
                    )
                except httpx.HTTPError as exc:
                    self.store.finish_delivery(
                        identity.account_id, delivery["update_id"], state="unknown"
                    )
                    logger.error("Telegram delivery outcome is unknown (%s)", type(exc).__name__)
                else:
                    self.store.finish_delivery(
                        identity.account_id,
                        delivery["update_id"],
                        state="sent",
                        message_id=message_id,
                        text=delivery["text"],
                    )
            while (delivery := self.store.claim_schedule_delivery(identity.account_id)) is not None:
                try:
                    message_id = await api.send_message(delivery["chat_id"], delivery["text"])
                except TelegramApiError as exc:
                    self.store.finish_schedule_delivery(
                        delivery["execution_id"],
                        state="failed" if exc.definitive else "unknown",
                        retry_after_seconds=(exc.retry_after_seconds if exc.definitive else None),
                    )
                except httpx.HTTPError as exc:
                    self.store.finish_schedule_delivery(delivery["execution_id"], state="unknown")
                    logger.error(
                        "Telegram schedule delivery outcome is unknown (%s)",
                        type(exc).__name__,
                    )
                else:
                    self.store.finish_schedule_delivery(
                        delivery["execution_id"], state="sent", message_id=message_id
                    )

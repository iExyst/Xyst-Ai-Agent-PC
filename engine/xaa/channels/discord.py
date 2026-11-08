"""Discord private-message gateway primitives and durable authorization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

import discord
import httpx

from xaa.credentials import CredentialStore
from xaa.storage.runs import RunState, RunStore, utc_now

logger = logging.getLogger(__name__)
PAIR_COMMAND = re.compile(r"^/pair\s+([A-Za-z0-9_-]{24,128})$")
MAX_TEXT_CHARS = 4096


def as_mapping(value: object) -> dict[str, Any] | None:
    return cast(dict[str, Any], value) if isinstance(value, dict) else None


@dataclass(frozen=True)
class DiscordIdentity:
    account_id: str
    username: str


@dataclass(frozen=True)
class DiscordMessage:
    message_id: str
    author_id: str
    channel_id: str
    content: str
    is_direct_message: bool


def dm_only_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.dm_messages = True
    return intents


class DiscordApiError(RuntimeError):
    def __init__(self, message: str, *, definitive: bool) -> None:
        super().__init__(message)
        self.definitive = definitive


class DiscordBotApi:
    """Small Discord REST client for bot identity verification and DM replies."""

    def __init__(self, token: str) -> None:
        if not token or len(token) > 512 or "\n" in token or "\r" in token:
            raise ValueError("Discord bot token is malformed.")
        self.token = token

    async def get_me(self) -> DiscordIdentity:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5)) as client:
                response = await client.get(
                    "https://discord.com/api/v10/users/@me",
                    headers={"Authorization": f"Bot {self.token}"},
                )
                response.raise_for_status()
                body = cast(object, response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise DiscordApiError(
                "Discord could not verify the bot identity.", definitive=True
            ) from exc
        if not isinstance(body, dict):
            raise DiscordApiError("Discord returned an invalid bot identity.", definitive=True)
        data = cast(dict[str, object], body)
        account_id = data.get("id")
        username = data.get("username")
        if (
            not isinstance(account_id, str)
            or not account_id.isdecimal()
            or not isinstance(username, str)
        ):
            raise DiscordApiError("Discord returned an invalid bot identity.", definitive=True)
        return DiscordIdentity(account_id, username)

    async def send_message(self, channel_id: str, content: str, *, nonce: str) -> str:
        if not channel_id.isdecimal() or len(channel_id) > 20:
            raise DiscordApiError("Discord reply destination is invalid.", definitive=True)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5)) as client:
                response = await client.post(
                    f"https://discord.com/api/v10/channels/{channel_id}/messages",
                    headers={"Authorization": f"Bot {self.token}"},
                    json={
                        "content": content[:2000],
                        "nonce": nonce[:25],
                        "enforce_nonce": True,
                        "allowed_mentions": {"parse": []},
                    },
                )
        except httpx.HTTPError as exc:
            raise DiscordApiError("Discord reply outcome is unknown.", definitive=False) from exc
        if response.status_code >= 500:
            raise DiscordApiError("Discord reply outcome is unknown.", definitive=False)
        if response.status_code >= 400:
            raise DiscordApiError("Discord rejected the reply.", definitive=True)
        try:
            body = response.json()
        except ValueError as exc:
            raise DiscordApiError("Discord reply outcome is unknown.", definitive=False) from exc
        if not isinstance(body, dict):
            raise DiscordApiError("Discord reply outcome is unknown.", definitive=False)
        message_id = cast(dict[str, object], body).get("id")
        if not isinstance(message_id, str) or not message_id.isdecimal():
            raise DiscordApiError("Discord reply outcome is unknown.", definitive=False)
        return message_id


class DiscordStore:
    """Persist Discord account links and inbound event idempotency."""

    def __init__(self, runs: RunStore) -> None:
        self.runs = runs
        self._initialize()

    def _initialize(self) -> None:
        with self.runs.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS discord_bots (
                    account_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS discord_pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS discord_messages (
                    account_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    user_id TEXT,
                    channel_id TEXT,
                    session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
                    run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
                    state TEXT NOT NULL,
                    delivery_state TEXT,
                    delivery_message_id TEXT,
                    received_at TEXT NOT NULL,
                    updated_at TEXT,
                    PRIMARY KEY(account_id,message_id)
                );
                CREATE TABLE IF NOT EXISTS discord_delivery_reconciliations (
                    id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    resolution TEXT NOT NULL CHECK (resolution IN ('delivered','retry')),
                    resolved_at TEXT NOT NULL,
                    FOREIGN KEY(account_id,message_id)
                        REFERENCES discord_messages(account_id,message_id) ON DELETE CASCADE
                );
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(discord_messages)").fetchall()
            }
            if "delivery_state" not in columns:
                connection.execute("ALTER TABLE discord_messages ADD COLUMN delivery_state TEXT")
            if "delivery_message_id" not in columns:
                connection.execute(
                    "ALTER TABLE discord_messages ADD COLUMN delivery_message_id TEXT"
                )
            if "updated_at" not in columns:
                connection.execute("ALTER TABLE discord_messages ADD COLUMN updated_at TEXT")
            connection.execute(
                "UPDATE discord_messages SET updated_at=received_at WHERE updated_at IS NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS discord_messages_delivery ON discord_messages(account_id,delivery_state,updated_at)"
            )

    @staticmethod
    def hash_pairing_code(code: str) -> str:
        return hashlib.sha256(code.encode("utf-8")).hexdigest()

    def create_pairing(
        self, account_id: str, code: str, *, ttl: timedelta = timedelta(minutes=5)
    ) -> str:
        now = datetime.now(UTC)
        expires = now + ttl
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM discord_pairing_codes WHERE account_id=? AND (expires_at<=? OR consumed_at IS NOT NULL)",
                (account_id, now.isoformat()),
            )
            connection.execute(
                "INSERT INTO discord_pairing_codes VALUES (?,?,?,?,NULL)",
                (self.hash_pairing_code(code), account_id, now.isoformat(), expires.isoformat()),
            )
        return expires.isoformat()

    def save_bot(self, identity: DiscordIdentity) -> None:
        with self.runs.connection() as connection:
            connection.execute(
                "INSERT INTO discord_bots VALUES (?,?,?) ON CONFLICT(account_id) DO UPDATE SET username=excluded.username,updated_at=excluded.updated_at",
                (identity.account_id, identity.username, utc_now()),
            )

    def profile(self, account_id: str | None = None) -> dict[str, Any]:
        with self.runs.connection() as connection:
            bot = (
                connection.execute(
                    "SELECT account_id,username FROM discord_bots WHERE account_id=?",
                    (account_id,),
                ).fetchone()
                if account_id
                else connection.execute(
                    "SELECT account_id,username FROM discord_bots ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            )
            selected = str(bot["account_id"]) if bot else account_id
            linked = (
                connection.execute(
                    "SELECT count(*) FROM channel_bindings WHERE channel='discord' AND account_id=? AND authorized=1",
                    (selected,),
                ).fetchone()[0]
                if selected
                else 0
            )
            messages = (
                connection.execute(
                    "SELECT count(*) FROM discord_messages WHERE account_id=?", (selected,)
                ).fetchone()[0]
                if selected
                else 0
            )
            deliveries: dict[str, int] = {}
            unknown_deliveries: list[dict[str, Any]] = []
            if selected:
                deliveries = {
                    str(row["delivery_state"]): int(row["amount"])
                    for row in connection.execute(
                        "SELECT delivery_state,count(*) AS amount FROM discord_messages WHERE account_id=? AND delivery_state IS NOT NULL GROUP BY delivery_state",
                        (selected,),
                    ).fetchall()
                }
                unknown_deliveries = [
                    {
                        "message_id": str(row["message_id"]),
                        "user_id": row["user_id"],
                        "run_id": row["run_id"],
                        "updated_at": str(row["updated_at"]),
                    }
                    for row in connection.execute(
                        "SELECT message_id,user_id,run_id,updated_at FROM discord_messages WHERE account_id=? AND delivery_state='unknown' ORDER BY updated_at,message_id",
                        (selected,),
                    ).fetchall()
                ]
        return {
            "configured": bot is not None,
            "bot_id": str(bot["account_id"]) if bot else None,
            "bot_username": str(bot["username"]) if bot else None,
            "linked_users": int(linked),
            "processed_messages": int(messages),
            "deliveries": deliveries,
            "unknown_deliveries": unknown_deliveries,
        }

    def linked_users(self, account_id: str) -> list[str]:
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT external_identity FROM channel_bindings WHERE channel='discord' AND account_id=? AND authorized=1 ORDER BY external_identity",
                (account_id,),
            ).fetchall()
        return [str(row["external_identity"]) for row in rows]

    def revoke_users(self, account_id: str) -> int:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM discord_pairing_codes WHERE account_id=?", (account_id,)
            )
            revoked = int(
                connection.execute(
                    "UPDATE channel_bindings SET authorized=0 WHERE channel='discord' AND account_id=? AND authorized=1",
                    (account_id,),
                ).rowcount
            )
            connection.execute(
                "UPDATE discord_messages SET delivery_state='revoked',updated_at=? WHERE account_id=? AND delivery_state='pending'",
                (utc_now(), account_id),
            )
            return revoked

    def process_message(
        self,
        identity: DiscordIdentity,
        message: DiscordMessage,
        *,
        model: str | None,
    ) -> dict[str, Any]:
        """Consume a DM once; only a valid one-time code may link a Discord user."""
        if not re.fullmatch(r"[0-9]{1,20}", message.message_id):
            return {"state": "invalid", "run_id": None}
        if not message.author_id.isdecimal() or not message.channel_id.isdecimal():
            return {"state": "invalid", "run_id": None}
        now = utc_now()
        text = message.content.strip()
        session_id: str | None = None
        run_id: str | None = None
        state = "ignored"
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO discord_messages (account_id,message_id,user_id,channel_id,session_id,run_id,state,delivery_state,delivery_message_id,received_at,updated_at) VALUES (?,?,?,?,NULL,NULL,'processing',NULL,NULL,?,?)",
                (
                    identity.account_id,
                    message.message_id,
                    message.author_id if message.is_direct_message else None,
                    message.channel_id if message.is_direct_message else None,
                    now,
                    now,
                ),
            ).rowcount
            if not inserted:
                row = connection.execute(
                    "SELECT state,run_id FROM discord_messages WHERE account_id=? AND message_id=?",
                    (identity.account_id, message.message_id),
                ).fetchone()
                return {"state": str(row["state"]), "run_id": row["run_id"], "duplicate": True}

            if message.is_direct_message and text:
                pair = PAIR_COMMAND.fullmatch(text)
                binding = connection.execute(
                    "SELECT id,session_id,authorized FROM channel_bindings WHERE channel='discord' AND account_id=? AND external_identity=?",
                    (identity.account_id, message.author_id),
                ).fetchone()
                if pair:
                    code_hash = self.hash_pairing_code(pair.group(1))
                    pairing = connection.execute(
                        "SELECT code_hash FROM discord_pairing_codes WHERE account_id=? AND code_hash=? AND expires_at>? AND consumed_at IS NULL",
                        (identity.account_id, code_hash, now),
                    ).fetchone()
                    if pairing:
                        connection.execute(
                            "UPDATE discord_pairing_codes SET consumed_at=? WHERE code_hash=? AND consumed_at IS NULL",
                            (now, code_hash),
                        )
                        if binding is None:
                            session_id = str(uuid4())
                            connection.execute(
                                "INSERT INTO sessions (id,channel,account_id,title,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                                (
                                    session_id,
                                    "discord",
                                    identity.account_id,
                                    f"Discord user {message.author_id}",
                                    now,
                                    now,
                                ),
                            )
                            connection.execute(
                                "INSERT INTO channel_bindings (id,channel,account_id,external_identity,session_id,identity_link_id,authorized,created_at) VALUES (?,?, ?,?,?,NULL,1,?)",
                                (
                                    str(uuid4()),
                                    "discord",
                                    identity.account_id,
                                    message.author_id,
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
                elif binding and binding["session_id"] and binding["authorized"]:
                    session_id = str(binding["session_id"])
                    if not model:
                        state = "model_unavailable"
                    elif len(text) > MAX_TEXT_CHARS:
                        state = "message_too_long"
                    else:
                        run_id = str(uuid4())
                        sequence = int(
                            connection.execute(
                                "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?",
                                (session_id,),
                            ).fetchone()[0]
                        )
                        connection.execute(
                            "INSERT INTO runs (id,objective,acceptance_criteria,workspace,mode,model,state,created_at,updated_at,revision,lease_owner,lease_expires_at) VALUES (?,?,?,?,?,?,?,?,?,0,NULL,NULL)",
                            (
                                run_id,
                                text,
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
                            (str(uuid4()), session_id, run_id, sequence, "user", text, now),
                        )
                        RunStore.record_event(
                            connection,
                            run_id,
                            "run.queued",
                            {
                                "state": RunState.QUEUED.value,
                                "channel": "discord",
                                "discord_message_id": message.message_id,
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
            connection.execute(
                "UPDATE discord_messages SET session_id=?,run_id=?,state=?,delivery_state=?,updated_at=? WHERE account_id=? AND message_id=?",
                (
                    session_id,
                    run_id,
                    state,
                    "pending" if state == "queued" else None,
                    now,
                    identity.account_id,
                    message.message_id,
                ),
            )
        return {"state": state, "run_id": run_id, "duplicate": False}

    def reconcile_abandoned_sends(self) -> int:
        with self.runs.connection() as connection:
            changed = connection.execute(
                "UPDATE discord_messages SET delivery_state='unknown',updated_at=? WHERE delivery_state='sending'",
                (utc_now(),),
            ).rowcount
        return int(changed)

    def claim_delivery(self, account_id: str) -> dict[str, Any] | None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT m.message_id,m.channel_id,m.session_id,m.run_id,r.state FROM discord_messages m JOIN runs r ON r.id=m.run_id WHERE m.account_id=? AND m.delivery_state='pending' AND r.state IN (?,?,?,?) ORDER BY m.received_at,m.message_id LIMIT 1",
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
            authorized = connection.execute(
                "SELECT 1 FROM channel_bindings WHERE channel='discord' AND account_id=? AND external_identity=(SELECT user_id FROM discord_messages WHERE account_id=? AND message_id=?) AND authorized=1",
                (account_id, account_id, row["message_id"]),
            ).fetchone()
            if authorized is None:
                connection.execute(
                    "UPDATE discord_messages SET delivery_state='revoked',updated_at=? WHERE account_id=? AND message_id=? AND delivery_state='pending'",
                    (utc_now(), account_id, row["message_id"]),
                )
                return None
            text: str | None = None
            if str(row["state"]) == RunState.COMPLETED.value:
                events = connection.execute(
                    "SELECT payload FROM run_events WHERE run_id=? AND event_type='model.message' ORDER BY sequence DESC",
                    (row["run_id"],),
                ).fetchall()
                for event in events:
                    payload = json.loads(event["payload"])
                    payload_mapping = as_mapping(payload)
                    response = (
                        as_mapping(payload_mapping.get("message")) if payload_mapping else None
                    )
                    candidate = response.get("content") if response else None
                    if isinstance(candidate, str) and candidate.strip():
                        text = candidate.strip()
                        break
            if str(row["state"]) != RunState.COMPLETED.value:
                text = "X.A.A. could not complete this objective. Open the desktop app to review the saved run status."
            if text is None:
                connection.execute(
                    "UPDATE discord_messages SET delivery_state='failed',updated_at=? WHERE account_id=? AND message_id=? AND delivery_state='pending'",
                    (utc_now(), account_id, row["message_id"]),
                )
                return None
            text = text[:1950] + ("… [truncated]" if len(text) > 1950 else "")
            changed = connection.execute(
                "UPDATE discord_messages SET delivery_state='sending',updated_at=? WHERE account_id=? AND message_id=? AND delivery_state='pending'",
                (utc_now(), account_id, row["message_id"]),
            ).rowcount
            if changed != 1:
                return None
            return {
                "message_id": str(row["message_id"]),
                "channel_id": str(row["channel_id"]),
                "session_id": str(row["session_id"]),
                "run_id": str(row["run_id"]),
                "text": text,
            }

    def finish_delivery(
        self,
        account_id: str,
        message_id: str,
        *,
        state: str,
        delivery_message_id: str | None = None,
    ) -> None:
        if state not in {"sent", "failed", "unknown"}:
            raise ValueError("Invalid Discord delivery state.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE discord_messages SET delivery_state=?,delivery_message_id=?,updated_at=? WHERE account_id=? AND message_id=? AND delivery_state='sending'",
                (state, delivery_message_id, utc_now(), account_id, message_id),
            )

    def reconcile_delivery(self, account_id: str, message_id: str, resolution: str) -> bool:
        if resolution not in {"delivered", "retry"}:
            raise ValueError("Invalid Discord delivery resolution.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT user_id,run_id FROM discord_messages WHERE account_id=? AND message_id=? AND delivery_state='unknown'",
                (account_id, message_id),
            ).fetchone()
            if row is None:
                return False
            authorized = connection.execute(
                "SELECT 1 FROM channel_bindings WHERE channel='discord' AND account_id=? AND external_identity=? AND authorized=1",
                (account_id, row["user_id"]),
            ).fetchone()
            if resolution == "retry" and authorized is None:
                return False
            if resolution == "retry":
                attempts = connection.execute(
                    "SELECT count(*) FROM discord_delivery_reconciliations WHERE account_id=? AND message_id=? AND resolution='retry'",
                    (account_id, message_id),
                ).fetchone()[0]
                if int(attempts) >= 2:
                    return False
            state = "sent" if resolution == "delivered" else "pending"
            changed = connection.execute(
                "UPDATE discord_messages SET delivery_state=?,updated_at=? WHERE account_id=? AND message_id=? AND delivery_state='unknown'",
                (state, utc_now(), account_id, message_id),
            ).rowcount
            if changed != 1:
                return False
            connection.execute(
                "INSERT INTO discord_delivery_reconciliations (id,account_id,message_id,resolution,resolved_at) VALUES (?,?,?,?,?)",
                (str(uuid4()), account_id, message_id, resolution, utc_now()),
            )
            return True


class DiscordGatewayClient(discord.Client):
    def __init__(
        self,
        runs: RunStore,
        store: DiscordStore,
        submit: Callable[[str], None],
        token: str,
    ) -> None:
        super().__init__(intents=dm_only_intents())
        self.runs = runs
        self.store = store
        self.submit = submit
        self.api = DiscordBotApi(token)
        self.delivery_task: asyncio.Task[None] | None = None

    async def setup_hook(self) -> None:
        self.delivery_task = asyncio.create_task(
            self._deliver_replies(), name="discord-reply-outbox"
        )

    async def close(self) -> None:
        task, self.delivery_task = self.delivery_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await super().close()

    async def _deliver_replies(self) -> None:
        while not self.is_closed():
            identity = self.user
            if identity is not None:
                while (delivery := self.store.claim_delivery(str(identity.id))) is not None:
                    nonce = hashlib.sha256(
                        f"{identity.id}:{delivery['message_id']}".encode("ascii")
                    ).hexdigest()[:24]
                    try:
                        sent_id = await self.api.send_message(
                            delivery["channel_id"], delivery["text"], nonce=nonce
                        )
                    except DiscordApiError as exc:
                        self.store.finish_delivery(
                            str(identity.id),
                            delivery["message_id"],
                            state="failed" if exc.definitive else "unknown",
                        )
                    except (httpx.HTTPError, OSError, TimeoutError) as exc:
                        self.store.finish_delivery(
                            str(identity.id), delivery["message_id"], state="unknown"
                        )
                        logger.error("Discord reply outcome unknown (%s)", type(exc).__name__)
                    else:
                        self.store.finish_delivery(
                            str(identity.id),
                            delivery["message_id"],
                            state="sent",
                            delivery_message_id=sent_id,
                        )
            await asyncio.sleep(1)

    async def on_ready(self) -> None:
        user = self.user
        if user is not None:
            self.store.save_bot(DiscordIdentity(str(user.id), user.name))
            logger.info("Discord gateway connected for configured bot account.")

    async def on_message(self, message: discord.Message) -> None:
        if message.guild is not None or message.author.bot:
            return
        with self.runs.connection() as connection:
            selected = connection.execute(
                "SELECT selected_model FROM provider_profiles WHERE id='lm-studio'"
            ).fetchone()
        identity = self.user
        if identity is None:
            return
        result = self.store.process_message(
            DiscordIdentity(str(identity.id), identity.name),
            DiscordMessage(
                str(message.id),
                str(message.author.id),
                str(message.channel.id),
                message.content,
                True,
            ),
            model=str(selected[0]) if selected and selected[0] else None,
        )
        if result["run_id"]:
            self.submit(str(result["run_id"]))


class DiscordGatewayService:
    """Receive only private Discord messages and queue them through the shared supervisor."""

    def __init__(
        self,
        runs: RunStore,
        credentials: CredentialStore,
        submit: Callable[[str], None],
    ) -> None:
        self.runs = runs
        self.store = DiscordStore(runs)
        self.credentials = credentials
        self.submit = submit
        self.task: asyncio.Task[None] | None = None
        self.client: DiscordGatewayClient | None = None

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="discord-channel")

    async def close(self) -> None:
        task, self.task = self.task, None
        client, self.client = self.client, None
        if client is not None and not client.is_closed():
            await client.close()
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def run(self) -> None:
        while True:
            token = self.credentials.get("discord-bot")
            if not token:
                await asyncio.sleep(2)
                continue
            self.store.reconcile_abandoned_sends()
            client = DiscordGatewayClient(self.runs, self.store, self.submit, token)
            self.client = client

            try:
                connection = asyncio.create_task(client.start(token, reconnect=True))
                while not connection.done():
                    await asyncio.sleep(2)
                    if self.credentials.get("discord-bot") != token:
                        await client.close()
                        break
                await connection
            except asyncio.CancelledError:
                raise
            except (discord.LoginFailure, discord.HTTPException, OSError, RuntimeError) as exc:
                logger.error("Discord gateway stopped (%s)", type(exc).__name__)
            finally:
                self.client = None
                if not client.is_closed():
                    await client.close()
            await asyncio.sleep(2)

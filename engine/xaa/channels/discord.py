"""Discord private-message gateway primitives and durable authorization."""

from __future__ import annotations

import asyncio
import hashlib
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
    pass


class DiscordBotApi:
    """Identity-only Discord REST client used when an owner saves a bot token."""

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
            raise DiscordApiError("Discord could not verify the bot identity.") from exc
        if not isinstance(body, dict):
            raise DiscordApiError("Discord returned an invalid bot identity.")
        data = cast(dict[str, object], body)
        account_id = data.get("id")
        username = data.get("username")
        if (
            not isinstance(account_id, str)
            or not account_id.isdecimal()
            or not isinstance(username, str)
        ):
            raise DiscordApiError("Discord returned an invalid bot identity.")
        return DiscordIdentity(account_id, username)


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
                    received_at TEXT NOT NULL,
                    PRIMARY KEY(account_id,message_id)
                );
                """
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
        return {
            "configured": bot is not None,
            "bot_id": str(bot["account_id"]) if bot else None,
            "bot_username": str(bot["username"]) if bot else None,
            "linked_users": int(linked),
            "processed_messages": int(messages),
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
            return int(
                connection.execute(
                    "UPDATE channel_bindings SET authorized=0 WHERE channel='discord' AND account_id=? AND authorized=1",
                    (account_id,),
                ).rowcount
            )

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
                "INSERT OR IGNORE INTO discord_messages VALUES (?,?,?,?,NULL,NULL,'processing',?)",
                (
                    identity.account_id,
                    message.message_id,
                    message.author_id if message.is_direct_message else None,
                    message.channel_id if message.is_direct_message else None,
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
                "UPDATE discord_messages SET session_id=?,run_id=?,state=? WHERE account_id=? AND message_id=?",
                (session_id, run_id, state, identity.account_id, message.message_id),
            )
        return {"state": state, "run_id": run_id, "duplicate": False}

    def reconcile_abandoned_sends(self) -> int:
        # Outbound delivery is added in the next adapter slice. Keeping this explicit avoids
        # implying a delivery guarantee before a durable outbox exists.
        return 0


class DiscordGatewayClient(discord.Client):
    def __init__(
        self,
        runs: RunStore,
        store: DiscordStore,
        submit: Callable[[str], None],
    ) -> None:
        super().__init__(intents=dm_only_intents())
        self.runs = runs
        self.store = store
        self.submit = submit

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
            client = DiscordGatewayClient(self.runs, self.store, self.submit)
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

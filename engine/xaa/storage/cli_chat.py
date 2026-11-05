"""Durable, bounded terminal chat sessions backed by shared conversation tables."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from xaa.storage.runs import RunStore, utc_now

ChatMode = Literal["observe", "workspace"]
_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_MAX_MESSAGES = 12
_MAX_CONTEXT_BYTES = 8192


def _clip_utf8(value: str, max_bytes: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value
    suffix = (
        "\n[Prior answer shortened in saved chat context; full candidate remains in run history.]"
    )
    prefix = raw[: max(0, max_bytes - len(suffix.encode("utf-8")))].decode("utf-8", "ignore")
    return prefix + suffix


class CLIChatStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    @staticmethod
    def validate_id(session_id: str) -> None:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError(
                "Chat session IDs may contain only letters, numbers, '.', '_' and '-'."
            )

    def get(self, session_id: str) -> dict[str, Any] | None:
        self.validate_id(session_id)
        with self.runs.connection() as connection:
            row = connection.execute(
                "SELECT s.id,s.channel,s.account_id,c.model,c.workspace,c.mode "
                "FROM sessions AS s JOIN cli_chat_config AS c ON c.session_id=s.id "
                "WHERE s.id=? AND s.channel='cli' AND s.account_id='local-user'",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            messages = connection.execute(
                "SELECT role,content,run_id FROM messages WHERE session_id=? "
                "ORDER BY sequence DESC LIMIT ?",
                (session_id, _MAX_MESSAGES),
            ).fetchall()
        return {
            "session_id": session_id,
            "model": row["model"],
            "workspace": row["workspace"],
            "mode": row["mode"],
            "messages": [dict(item) for item in reversed(messages)],
        }

    def append_turn(
        self,
        session_id: str,
        *,
        run_id: str,
        model: str,
        workspace: str | None,
        mode: ChatMode,
        user_message: str,
        assistant_message: str,
    ) -> dict[str, Any]:
        self.validate_id(session_id)
        if not model.strip() or len(model) > 256:
            raise ValueError("Chat model identifier must contain 1–256 characters.")
        if workspace is not None and len(workspace) > 4096:
            raise ValueError("Chat workspace path exceeds 4,096 characters.")
        if mode not in {"observe", "workspace"}:
            raise ValueError("Unsupported chat mode.")
        if not user_message.strip() or len(user_message.encode("utf-8")) > 2000:
            raise ValueError("User chat message must contain 1–2,000 UTF-8 bytes.")
        if not assistant_message.strip() or len(assistant_message) > 16000:
            raise ValueError("Assistant chat message must contain 1–16,000 characters.")
        stored_assistant = _clip_utf8(assistant_message, 6000)
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            saved = connection.execute(
                "SELECT model,workspace,mode FROM cli_chat_config WHERE session_id=?",
                (session_id,),
            ).fetchone()
            existing = connection.execute(
                "SELECT channel,account_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if existing is not None and (
                existing["channel"] != "cli" or existing["account_id"] != "local-user"
            ):
                raise ValueError("Chat session name is already used by another session.")
            if saved is not None and (
                saved["model"] != model or saved["workspace"] != workspace or saved["mode"] != mode
            ):
                raise ValueError(
                    "Chat session settings changed. Reset the session or choose a new session ID."
                )
            if saved is None:
                connection.execute(
                    "INSERT INTO sessions(id,channel,account_id,title,created_at,updated_at) "
                    "VALUES(?,'cli','local-user',?,?,?)",
                    (session_id, f"CLI chat {session_id}", now, now),
                )
                connection.execute(
                    "INSERT INTO cli_chat_config(session_id,model,workspace,mode) VALUES(?,?,?,?)",
                    (session_id, model, workspace, mode),
                )
            if connection.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is None:
                raise KeyError(run_id)
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()[0]
            connection.executemany(
                "INSERT INTO messages(id,session_id,run_id,sequence,role,content,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    (
                        f"{session_id}:{sequence}",
                        session_id,
                        run_id,
                        sequence,
                        "user",
                        user_message,
                        now,
                    ),
                    (
                        f"{session_id}:{sequence + 1}",
                        session_id,
                        run_id,
                        sequence + 1,
                        "assistant",
                        stored_assistant,
                        now,
                    ),
                ),
            )
            connection.execute(
                "DELETE FROM messages WHERE session_id=? AND sequence<=?",
                (session_id, sequence + 1 - _MAX_MESSAGES),
            )
            ordered = connection.execute(
                "SELECT sequence,content FROM messages WHERE session_id=? ORDER BY sequence",
                (session_id,),
            ).fetchall()
            while (
                ordered
                and len(
                    json.dumps([item["content"] for item in ordered], ensure_ascii=False).encode(
                        "utf-8"
                    )
                )
                > _MAX_CONTEXT_BYTES
            ):
                oldest_pair = [item["sequence"] for item in ordered[:2]]
                if not oldest_pair:
                    break
                connection.executemany(
                    "DELETE FROM messages WHERE session_id=? AND sequence=?",
                    ((session_id, item) for item in oldest_pair),
                )
                ordered = connection.execute(
                    "SELECT sequence,content FROM messages WHERE session_id=? ORDER BY sequence",
                    (session_id,),
                ).fetchall()
            connection.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
        result = self.get(session_id)
        if result is None:
            raise RuntimeError("Chat session disappeared after a committed turn.")
        return result

    def delete(self, session_id: str) -> bool:
        self.validate_id(session_id)
        with self.runs.connection() as connection:
            return (
                connection.execute(
                    "DELETE FROM sessions WHERE id=? AND channel='cli' AND account_id='local-user'",
                    (session_id,),
                ).rowcount
                == 1
            )

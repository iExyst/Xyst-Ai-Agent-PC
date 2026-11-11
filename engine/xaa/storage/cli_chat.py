"""Durable, bounded terminal chat sessions backed by shared conversation tables."""

from __future__ import annotations

import json
import re
import unicodedata
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
                "SELECT s.id,s.title,s.channel,s.account_id,c.model,c.workspace,c.mode "
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
            "title": row["title"],
            "model": row["model"],
            "workspace": row["workspace"],
            "mode": row["mode"],
            "messages": [dict(item) for item in reversed(messages)],
        }

    def list_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """List the most recently used local coordinator-backed chat sessions."""
        if not 1 <= limit <= 100:
            raise ValueError("Chat session page size must be 1–100.")
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT s.id AS session_id,s.title,c.model,c.workspace,c.mode,s.updated_at "
                "FROM sessions AS s JOIN cli_chat_config AS c ON c.session_id=s.id "
                "WHERE s.channel='cli' AND s.account_id='local-user' "
                "ORDER BY s.updated_at DESC,s.id LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def ensure_session(
        self,
        session_id: str,
        *,
        model: str,
        workspace: str | None,
        mode: ChatMode,
    ) -> dict[str, Any]:
        """Create an empty named session or validate its immutable runtime settings."""
        self.validate_id(session_id)
        self._validate_settings(model, workspace, mode)
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT channel,account_id FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if existing is not None and (
                existing["channel"] != "cli" or existing["account_id"] != "local-user"
            ):
                raise ValueError("Chat session name is already used by another session.")
            saved = connection.execute(
                "SELECT model,workspace,mode FROM cli_chat_config WHERE session_id=?",
                (session_id,),
            ).fetchone()
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
                    (session_id, session_id, now, now),
                )
                connection.execute(
                    "INSERT INTO cli_chat_config(session_id,model,workspace,mode) VALUES(?,?,?,?)",
                    (session_id, model, workspace, mode),
                )
        result = self.get(session_id)
        if result is None:
            raise RuntimeError("Chat session disappeared after it was opened.")
        return result

    def rename(self, session_id: str, title: str) -> bool:
        """Change only the display title of an owned local chat session."""
        self.validate_id(session_id)
        normalized = title.strip()
        if (
            not normalized
            or len(normalized) > 80
            or any(unicodedata.category(character) in {"Cc", "Cs"} for character in normalized)
        ):
            raise ValueError("Chat title must contain 1–80 characters and no control characters.")
        with self.runs.connection() as connection:
            result = connection.execute(
                "UPDATE sessions SET title=? WHERE id=? AND channel='cli' "
                "AND account_id='local-user' AND EXISTS ("
                "SELECT 1 FROM cli_chat_config AS c WHERE c.session_id=sessions.id)",
                (normalized, session_id),
            )
            return result.rowcount == 1

    def list_acp(
        self, *, model: str, workspace: str, mode: ChatMode, after: str = "", limit: int = 50
    ) -> list[dict[str, str]]:
        """List only ACP generated sessions matching this adapter's fixed settings."""
        if after and re.fullmatch(r"[0-9a-f]{32}", after) is None:
            raise ValueError("Invalid ACP session cursor.")
        if not 1 <= limit <= 50:
            raise ValueError("ACP session page size must be 1–50.")
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT s.id,s.title,s.updated_at FROM sessions AS s "
                "JOIN cli_chat_config AS c ON c.session_id=s.id "
                "WHERE s.channel='cli' AND s.account_id='local-user' "
                "AND c.model=? AND c.workspace=? AND c.mode=? AND s.id>? "
                "AND length(s.id)=32 AND s.id NOT GLOB '*[^0-9a-f]*' "
                "ORDER BY s.id LIMIT ?",
                (model, workspace, mode, after, limit),
            ).fetchall()
        return [dict(row) for row in rows]

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
        self._validate_settings(model, workspace, mode)
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
            run = connection.execute(
                "SELECT model,workspace,mode FROM runs WHERE id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise KeyError(run_id)
            if run["model"] != model or run["workspace"] != workspace or run["mode"] != mode:
                raise ValueError("Chat turn settings must match its coordinator run.")
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

    @staticmethod
    def _validate_settings(model: str, workspace: str | None, mode: ChatMode) -> None:
        if not model.strip() or len(model) > 256:
            raise ValueError("Chat model identifier must contain 1–256 characters.")
        if workspace is not None and len(workspace) > 4096:
            raise ValueError("Chat workspace path exceeds 4,096 characters.")
        if mode not in {"observe", "workspace"}:
            raise ValueError("Unsupported chat mode.")

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

    def delete_acp(self, session_id: str, *, model: str, workspace: str, mode: ChatMode) -> bool:
        """Delete only an ACP-shaped session matching its bound runtime settings."""
        if re.fullmatch(r"[0-9a-f]{32}", session_id) is None:
            raise ValueError("ACP session ID must be a lowercase UUID hex string.")
        if not model.strip() or len(model) > 256 or len(workspace) > 4096:
            raise ValueError("ACP session settings are invalid.")
        if mode not in {"observe", "workspace"}:
            raise ValueError("Unsupported ACP chat mode.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "DELETE FROM sessions WHERE id=? AND channel='cli' AND account_id='local-user' "
                "AND EXISTS (SELECT 1 FROM cli_chat_config AS c WHERE c.session_id=sessions.id "
                "AND c.model=? AND c.workspace=? AND c.mode=?)",
                (session_id, model, workspace, mode),
            )
            return result.rowcount == 1

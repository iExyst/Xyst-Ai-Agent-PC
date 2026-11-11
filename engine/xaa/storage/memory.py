"""Explicit user memory with transactional FTS5 index maintenance."""

import json
import re
from typing import Any
from uuid import uuid4

from xaa.storage.runs import RunStore, utc_now


class MemoryStore:
    @staticmethod
    def _query_expression(query: str) -> str | None:
        # FTS5's default unicode61 tokenizer treats underscores as separators.
        words = re.findall(r"[^\W_]+", query, flags=re.UNICODE)[:32]
        if not words:
            return None
        return " OR ".join('"' + word.replace('"', '""') + '"' for word in words)

    def recall_preferences(
        self, scope: str, *, max_bytes: int = 4096, query: str | None = None
    ) -> dict[str, Any]:
        if max_bytes < 1:
            raise ValueError("Recall budget must be positive.")
        expression = self._query_expression(query) if query is not None else None
        with self.runs.connection() as connection:
            if query is not None and expression is None:
                rows = []
            elif expression is not None:
                rows = connection.execute(
                    "SELECT memories.id,memories.content FROM memories JOIN memory_search ON memory_search.id=memories.id WHERE memories.scope=? AND memories.kind='preference' AND memories.provenance='user' AND memory_search MATCH ? ORDER BY bm25(memory_search),memories.updated_at DESC,memories.id",
                    (scope, expression),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id,content FROM memories WHERE scope=? AND kind='preference' AND provenance='user' ORDER BY updated_at DESC,id",
                    (scope,),
                ).fetchall()
        selected: list[dict[str, str]] = []
        used = 0
        for row in rows:
            item = {"id": row["id"], "content": row["content"]}
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 2
            if used + size <= max_bytes:
                selected.append(item)
                used += size
        return {"items": selected, "omitted": len(rows) - len(selected), "bytes": used}

    def recall_sourced_knowledge(
        self, scope: str, *, max_bytes: int = 4096, query: str | None = None
    ) -> dict[str, Any]:
        if max_bytes < 1:
            raise ValueError("Recall budget must be positive.")
        expression = self._query_expression(query) if query is not None else None
        with self.runs.connection() as connection:
            if query is not None and expression is None:
                rows = []
            elif expression is not None:
                rows = connection.execute(
                    "SELECT memories.id,memories.content,memories.source_uri FROM memories JOIN memory_search ON memory_search.id=memories.id WHERE memories.scope=? AND memories.kind='knowledge' AND memories.provenance='user_source' AND memory_search MATCH ? ORDER BY bm25(memory_search),memories.updated_at DESC,memories.id",
                    (scope, expression),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT id,content,source_uri FROM memories WHERE scope=? AND kind='knowledge' AND provenance='user_source' ORDER BY updated_at DESC,id",
                    (scope,),
                ).fetchall()
        selected: list[dict[str, str]] = []
        used = 0
        for row in rows:
            item = {"id": row["id"], "content": row["content"], "source_uri": row["source_uri"]}
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 2
            if used + size <= max_bytes:
                selected.append(item)
                used += size
        return {"items": selected, "omitted": len(rows) - len(selected), "bytes": used}

    def __init__(self, runs: RunStore) -> None:
        self.runs = runs
        with runs.connection() as connection:
            connection.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_search USING fts5(id UNINDEXED, scope UNINDEXED, content);
                CREATE TRIGGER IF NOT EXISTS memory_insert AFTER INSERT ON memories BEGIN
                    INSERT INTO memory_search(id,scope,content) VALUES(new.id,new.scope,new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_update AFTER UPDATE ON memories BEGIN
                    DELETE FROM memory_search WHERE id=old.id;
                    INSERT INTO memory_search(id,scope,content) VALUES(new.id,new.scope,new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS memory_delete AFTER DELETE ON memories BEGIN
                    DELETE FROM memory_search WHERE id=old.id;
                END;
                INSERT INTO memory_search(id,scope,content)
                    SELECT id,scope,content FROM memories WHERE id NOT IN (SELECT id FROM memory_search);
            """)

    def save_preference(
        self, scope: str, content: str, *, memory_id: str | None = None
    ) -> dict[str, Any]:
        if not scope or not content.strip() or len(content.encode("utf-8")) > 16384:
            raise ValueError("Preference requires a scope and 1–16384 bytes of content.")
        identity = memory_id or str(uuid4())
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if memory_id:
                changed = connection.execute(
                    "UPDATE memories SET content=?, updated_at=? WHERE id=? AND scope=? AND kind='preference' AND provenance='user'",
                    (content, now, identity, scope),
                ).rowcount
                if not changed:
                    raise KeyError(identity)
            else:
                connection.execute(
                    "INSERT INTO memories(id,kind,scope,content,provenance,confidence,created_at,updated_at,schema_version,source_uri) VALUES (?, 'preference', ?, ?, 'user', 1.0, ?, ?, 1, NULL)",
                    (identity, scope, content, now, now),
                )
            row = connection.execute(
                "SELECT * FROM memories WHERE id=? AND scope=?", (identity, scope)
            ).fetchone()
        return dict(row)

    def save_sourced_knowledge(
        self,
        scope: str,
        content: str,
        source_uri: str,
        *,
        memory_id: str | None = None,
    ) -> dict[str, Any]:
        if (
            not scope
            or not content.strip()
            or len(content.encode("utf-8")) > 16384
            or not source_uri.strip()
            or len(source_uri.encode("utf-8")) > 2048
        ):
            raise ValueError(
                "Sourced knowledge requires a scope, 1–16384 bytes of content, and a 1–2048 byte source reference."
            )
        identity = memory_id or str(uuid4())
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if memory_id:
                changed = connection.execute(
                    "UPDATE memories SET content=?, source_uri=?, updated_at=? WHERE id=? AND scope=? AND kind='knowledge' AND provenance='user_source'",
                    (content, source_uri.strip(), now, identity, scope),
                ).rowcount
                if not changed:
                    raise KeyError(identity)
            else:
                connection.execute(
                    "INSERT INTO memories(id,kind,scope,content,provenance,confidence,created_at,updated_at,schema_version,source_uri) VALUES (?, 'knowledge', ?, ?, 'user_source', 1.0, ?, ?, 1, ?)",
                    (identity, scope, content, now, now, source_uri.strip()),
                )
            row = connection.execute(
                "SELECT * FROM memories WHERE id=? AND scope=?", (identity, scope)
            ).fetchone()
        return dict(row)

    def search(self, scope: str, query: str = "") -> list[dict[str, Any]]:
        words = re.findall(r"\w+", query, flags=re.UNICODE)[:32]
        with self.runs.connection() as connection:
            if words:
                expression = " AND ".join('"' + word + '"' for word in words)
                rows = connection.execute(
                    "SELECT memories.* FROM memories JOIN memory_search ON memory_search.id=memories.id WHERE memory_search MATCH ? AND memories.scope=? ORDER BY bm25(memory_search) LIMIT 50",
                    (expression, scope),
                ).fetchall()
            elif query:
                return []
            else:
                rows = connection.execute(
                    "SELECT * FROM memories WHERE scope=? ORDER BY updated_at DESC LIMIT 50",
                    (scope,),
                ).fetchall()
        return [dict(row) for row in rows]

    def export(self, scope: str) -> list[dict[str, Any]]:
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM memories WHERE scope=? ORDER BY created_at,id", (scope,)
            ).fetchall()
        return [dict(row) for row in rows]

    def delete(self, scope: str, memory_id: str) -> bool:
        with self.runs.connection() as connection:
            return (
                connection.execute(
                    "DELETE FROM memories WHERE id=? AND scope=?", (memory_id, scope)
                ).rowcount
                == 1
            )

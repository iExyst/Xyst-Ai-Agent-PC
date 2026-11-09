"""Bounded authenticated run history using stable keyset pagination."""

import base64
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from xaa.runtime.states import TERMINAL_STATES
from xaa.storage.runs import RunStore


class HistoryCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    created_at: datetime
    id: UUID


def list_runs(
    store: RunStore,
    *,
    limit: int = 20,
    before: str | None = None,
    query: str = "",
    scope: Literal["objective", "conversation"] = "objective",
) -> dict[str, Any]:
    if not 1 <= limit <= 50 or len(query) > 256 or (before is not None and len(before) > 1024):
        raise ValueError("Invalid run-history bounds.")
    if "\x00" in query:
        raise ValueError("Search text cannot contain a NUL character.")
    if scope not in {"objective", "conversation"}:
        raise ValueError("Invalid history search scope.")
    clauses: list[str] = []
    values: list[Any] = []
    if query:
        if scope == "conversation":
            clauses.append(
                "id IN (SELECT run_id FROM conversation_fts WHERE conversation_fts MATCH ?)"
            )
            values.append('"' + query.replace('"', '""') + '"')
        else:
            clauses.append("instr(objective,?)>0")
            values.append(query)
    if before:
        try:
            raw = base64.b64decode(before + "=" * (-len(before) % 4), altchars=b"-_", validate=True)
            cursor = HistoryCursor.model_validate_json(raw)
            if cursor.created_at.tzinfo is None:
                raise ValueError("Missing timezone.")
        except ValueError as exc:
            raise ValueError("Invalid history cursor.") from exc
        clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
        stamp = cursor.created_at.astimezone(UTC).isoformat()
        values.extend([stamp, stamp, str(cursor.id)])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT id,substr(objective,1,500) AS objective,state,model,workspace,created_at,updated_at FROM runs"
            + where
            + " ORDER BY created_at DESC,id DESC LIMIT ?",
            (*values, limit + 1),
        ).fetchall()
    items = [dict(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        anchor = HistoryCursor.model_validate(
            {
                "created_at": datetime.fromisoformat(items[-1]["created_at"]),
                "id": UUID(items[-1]["id"]),
            }
        )
        next_cursor = (
            base64.urlsafe_b64encode(anchor.model_dump_json().encode()).decode().rstrip("=")
        )
    return {"items": items, "next_cursor": next_cursor}


def latest_unfinished_run(store: RunStore) -> dict[str, Any] | None:
    terminal = tuple(sorted(state.value for state in TERMINAL_STATES))
    terminal_sql = ",".join(f"'{state}'" for state in terminal)
    with store.connection() as connection:
        row = connection.execute(
            "SELECT id,substr(objective,1,500) AS objective,state,model,workspace,created_at,updated_at "
            f"FROM runs WHERE state NOT IN ({terminal_sql}) ORDER BY updated_at DESC,created_at DESC,id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row is not None else None

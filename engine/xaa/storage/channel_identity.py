"""Owner-managed identity links for explicitly shared channel conversation history."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from xaa.storage.runs import RunStore, utc_now


class ChannelIdentityLinks:
    def __init__(self, store: RunStore) -> None:
        self.store = store

    def list_bindings(self) -> list[dict[str, Any]]:
        with self.store.connection() as connection:
            rows = connection.execute(
                """SELECT b.id,b.channel,b.account_id,b.external_identity,b.session_id,
                          s.channel AS session_channel,
                          b.identity_link_id,b.authorized,s.title,l.created_at AS link_created_at
                   FROM channel_bindings b
                   JOIN sessions s ON s.id=b.session_id
                   LEFT JOIN channel_identity_links l
                     ON l.id=b.identity_link_id AND l.revoked_at IS NULL
                   WHERE b.authorized=1 OR (b.identity_link_id IS NOT NULL AND l.revoked_at IS NULL)
                   ORDER BY b.channel,b.account_id,b.external_identity"""
            ).fetchall()
        return [dict(row) for row in rows]

    def create_link(self, binding_ids: tuple[str, str], shared_history_binding_id: str) -> str:
        if binding_ids[0] == binding_ids[1] or shared_history_binding_id not in binding_ids:
            raise ValueError("Choose two different identities and choose whose history to share.")
        now = utc_now()
        link_id = uuid4().hex
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in binding_ids)
            rows = connection.execute(
                f"""SELECT id,channel,session_id,identity_link_id,authorized
                    FROM channel_bindings WHERE id IN ({placeholders})""",
                binding_ids,
            ).fetchall()
            if len(rows) != 2 or any(row["authorized"] != 1 for row in rows):
                raise ValueError("Both channel identities must be currently authorized.")
            if len({row["channel"] for row in rows}) != 2:
                raise ValueError("Cross-channel links require identities from different channels.")
            if any(row["identity_link_id"] is not None for row in rows):
                raise ValueError(
                    "An identity is already linked. Unlink it before changing the link."
                )
            selected = next(row for row in rows if row["id"] == shared_history_binding_id)
            session_id = selected["session_id"]
            if session_id is None:
                raise ValueError("The selected identity has no saved conversation history.")
            connection.execute(
                "INSERT INTO channel_identity_links(id,shared_session_id,created_at,revoked_at) VALUES(?,?,?,NULL)",
                (link_id, session_id, now),
            )
            connection.execute(
                f"UPDATE channel_bindings SET identity_link_id=?,session_id=? WHERE id IN ({placeholders})",
                (link_id, session_id, *binding_ids),
            )
            connection.execute(
                "INSERT INTO channel_identity_link_events(id,link_id,action,details,created_at) VALUES(?,?,'created',?,?)",
                (
                    uuid4().hex,
                    link_id,
                    json.dumps(
                        {
                            "binding_ids": sorted(binding_ids),
                            "shared_history_binding_id": shared_history_binding_id,
                        },
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
        return link_id

    def add_member(self, link_id: str, binding_id: str) -> str:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            link = connection.execute(
                "SELECT shared_session_id FROM channel_identity_links WHERE id=? AND revoked_at IS NULL",
                (link_id,),
            ).fetchone()
            if link is None:
                raise ValueError("The cross-channel identity link is no longer active.")
            binding = connection.execute(
                "SELECT id,channel,authorized,identity_link_id FROM channel_bindings WHERE id=?",
                (binding_id,),
            ).fetchone()
            if binding is None or binding["authorized"] != 1:
                raise ValueError("The additional channel identity must be currently authorized.")
            if binding["identity_link_id"] is not None:
                raise ValueError("The additional identity is already linked.")
            existing_channel = connection.execute(
                "SELECT 1 FROM channel_bindings WHERE identity_link_id=? AND channel=? LIMIT 1",
                (link_id, binding["channel"]),
            ).fetchone()
            if existing_channel is not None:
                raise ValueError("A link can include only one identity from each channel.")
            session_id = str(link["shared_session_id"])
            changed = connection.execute(
                "UPDATE channel_bindings SET identity_link_id=?,session_id=? WHERE id=? AND identity_link_id IS NULL AND authorized=1",
                (link_id, session_id, binding_id),
            ).rowcount
            if changed != 1:
                raise ValueError(
                    "The additional identity changed while the link was being updated."
                )
            connection.execute(
                "INSERT INTO channel_identity_link_events(id,link_id,action,details,created_at) VALUES(?,?,'member_added',?,?)",
                (
                    uuid4().hex,
                    link_id,
                    json.dumps(
                        {"binding_id": binding_id, "channel": binding["channel"]},
                        separators=(",", ":"),
                    ),
                    now,
                ),
            )
        return session_id

    def revoke_link(self, link_id: str) -> int:
        now = utc_now()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            link = connection.execute(
                "SELECT id FROM channel_identity_links WHERE id=? AND revoked_at IS NULL",
                (link_id,),
            ).fetchone()
            if link is None:
                return 0
            bindings = connection.execute(
                """SELECT b.id,b.channel,b.account_id,b.external_identity,s.title
                   FROM channel_bindings b JOIN sessions s ON s.id=b.session_id
                   WHERE b.identity_link_id=? ORDER BY b.id""",
                (link_id,),
            ).fetchall()
            for binding in bindings:
                session_id = uuid4().hex
                title = f"{binding['channel'].title()} conversation after unlink"
                connection.execute(
                    "INSERT INTO sessions(id,channel,account_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (session_id, binding["channel"], binding["account_id"], title, now, now),
                )
                connection.execute(
                    "UPDATE channel_bindings SET identity_link_id=NULL,session_id=? WHERE id=? AND identity_link_id=?",
                    (session_id, binding["id"], link_id),
                )
            connection.execute(
                "UPDATE channel_identity_links SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (now, link_id),
            )
            connection.execute(
                "INSERT INTO channel_identity_link_events(id,link_id,action,details,created_at) VALUES(?,?,'revoked',?,?)",
                (
                    uuid4().hex,
                    link_id,
                    json.dumps(
                        {"binding_ids": [row["id"] for row in bindings]}, separators=(",", ":")
                    ),
                    now,
                ),
            )
        return len(bindings)

from __future__ import annotations

from email.message import EmailMessage
from uuid import uuid4

import pytest
from xaa.channels.discord import DiscordIdentity, DiscordMessage, DiscordStore
from xaa.channels.email import EmailConfig, EmailStore
from xaa.channels.telegram import TelegramIdentity, TelegramStore
from xaa.storage.channel_identity import ChannelIdentityLinks
from xaa.storage.runs import RunStore, utc_now


def _binding(store: RunStore, channel: str, account: str, external: str) -> tuple[str, str]:
    now = utc_now()
    session_id = uuid4().hex
    binding_id = uuid4().hex
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO sessions(id,channel,account_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (session_id, channel, account, f"{channel} history", now, now),
        )
        connection.execute(
            "INSERT INTO channel_bindings(id,channel,account_id,external_identity,session_id,identity_link_id,authorized,created_at) VALUES(?,?,?,?,?,NULL,1,?)",
            (binding_id, channel, account, external, session_id, now),
        )
    return binding_id, session_id


def test_link_shares_selected_session_and_unlink_separates_future_history(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "identity-links.sqlite3")
    telegram_binding, telegram_session = _binding(store, "telegram", "bot-1", "user-1")
    email_binding, email_session = _binding(store, "email", "mailbox-1", "person@example.test")
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO messages(id,session_id,run_id,sequence,role,content,created_at) VALUES(?,?,NULL,1,'user','selected history',?)",
            (uuid4().hex, telegram_session, utc_now()),
        )
        connection.execute(
            "INSERT INTO messages(id,session_id,run_id,sequence,role,content,created_at) VALUES(?,?,NULL,1,'user','private email history',?)",
            (uuid4().hex, email_session, utc_now()),
        )

    links = ChannelIdentityLinks(store)
    link_id = links.create_link(
        (telegram_binding, email_binding), shared_history_binding_id=telegram_binding
    )
    linked = {item["id"]: item for item in links.list_bindings()}
    assert linked[telegram_binding]["identity_link_id"] == link_id
    assert linked[email_binding]["identity_link_id"] == link_id
    assert linked[email_binding]["session_id"] == telegram_session
    assert linked[email_binding]["session_id"] != email_session
    with store.connection() as connection:
        shared_history = connection.execute(
            "SELECT content FROM messages WHERE session_id=? ORDER BY sequence",
            (linked[email_binding]["session_id"],),
        ).fetchall()
        assert [row["content"] for row in shared_history] == ["selected history"]
        connection.execute("UPDATE channel_bindings SET authorized=0 WHERE id=?", (email_binding,))
    listed_members = {item["id"]: item for item in links.list_bindings()}
    assert set(listed_members) == {telegram_binding, email_binding}
    assert listed_members[email_binding]["authorized"] == 0

    assert links.revoke_link(link_id) == 2
    unlinked = {item["id"]: item for item in links.list_bindings()}
    assert unlinked[telegram_binding]["identity_link_id"] is None
    assert unlinked[telegram_binding]["session_id"] != telegram_session
    with store.connection() as connection:
        email_after_unlink = connection.execute(
            "SELECT identity_link_id,session_id FROM channel_bindings WHERE id=?",
            (email_binding,),
        ).fetchone()
    assert email_after_unlink["identity_link_id"] is None
    assert email_after_unlink["session_id"] != telegram_session
    assert email_after_unlink["session_id"] != unlinked[telegram_binding]["session_id"]
    assert links.revoke_link(link_id) == 0
    with store.connection() as connection:
        retained = connection.execute(
            "SELECT content FROM messages WHERE session_id=?", (telegram_session,)
        ).fetchall()
        events = connection.execute(
            "SELECT action FROM channel_identity_link_events WHERE link_id=? ORDER BY created_at,id",
            (link_id,),
        ).fetchall()
    assert {row["action"] for row in events} == {"created", "revoked"}
    assert [row["content"] for row in retained] == ["selected history"]


def test_linked_telegram_and_email_ingress_use_the_selected_shared_session(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "identity-link-ingress.sqlite3")
    telegram_binding, telegram_session = _binding(store, "telegram", "bot-1", "1001")
    email_binding, _ = _binding(store, "email", "agent@example.test", "owner@example.test")
    ChannelIdentityLinks(store).create_link((telegram_binding, email_binding), telegram_binding)

    telegram = TelegramStore(store).process_update(
        TelegramIdentity("bot-1", "bot"),
        {
            "update_id": 10,
            "message": {
                "from": {"id": 1001},
                "chat": {"id": 1001, "type": "private"},
                "text": "message from Telegram",
            },
        },
        model="fixture/model",
    )
    email = EmailStore(store)
    mailbox_id = email.save_config(
        EmailConfig.parse(
            {
                "address": "agent@example.test",
                "password": "fixture-secret",
                "imap_host": "imap.example.test",
                "smtp_host": "smtp.example.test",
                "allowed_senders": "owner@example.test",
                "authserv_id": "mx.example.test",
            }
        )
    )
    message = EmailMessage()
    message["From"] = "owner@example.test"
    message["To"] = "agent@example.test"
    message["Subject"] = "Linked identity test"
    message["Message-ID"] = "<identity-link@example.test>"
    message["Authentication-Results"] = "mx.example.test; dmarc=pass header.from=example.test"
    message.set_content("message from email")
    email_run_id = email.accept_message(mailbox_id, "1", "1", message.as_bytes(), "fixture/model")

    assert telegram["state"] == "queued"
    assert email_run_id is not None
    with store.connection() as connection:
        rows = connection.execute(
            "SELECT content FROM messages WHERE session_id=? ORDER BY sequence",
            (telegram_session,),
        ).fetchall()
        sessions = connection.execute(
            "SELECT DISTINCT session_id FROM messages WHERE run_id IN (?,?)",
            (telegram["run_id"], email_run_id),
        ).fetchall()
    assert [row["content"] for row in rows] == ["message from Telegram", "message from email"]
    assert {row["session_id"] for row in sessions} == {telegram_session}


def test_linked_discord_ingress_uses_the_selected_shared_session(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "identity-link-discord.sqlite3")
    telegram_binding, telegram_session = _binding(store, "telegram", "bot-1", "1001")
    discord_binding, _ = _binding(store, "discord", "2001", "3001")
    ChannelIdentityLinks(store).create_link((telegram_binding, discord_binding), telegram_binding)

    result = DiscordStore(store).process_message(
        DiscordIdentity("2001", "fixture-bot"),
        DiscordMessage("10", "3001", "4001", "message from Discord", True),
        model="fixture/model",
    )

    assert result["state"] == "queued"
    with store.connection() as connection:
        message = connection.execute(
            "SELECT session_id,content FROM messages WHERE run_id=?", (result["run_id"],)
        ).fetchone()
    assert message["session_id"] == telegram_session
    assert message["content"] == "message from Discord"


@pytest.mark.parametrize(
    "channels,authorized,linked,same_channel",
    [
        (("telegram", "email"), (True, False), (None, None), False),
        (("telegram", "telegram"), (True, True), (None, None), True),
        (("telegram", "email"), (True, True), ("existing", None), False),
    ],
)
def test_link_rejects_untrusted_or_ambiguous_bindings(
    workspace_tmp_path, channels, authorized, linked, same_channel
):
    store = RunStore(workspace_tmp_path / "identity-links-rejected.sqlite3")
    first, _ = _binding(store, channels[0], "account-1", "identity-1")
    second, _ = _binding(store, channels[1], "account-2", "identity-2")
    if not authorized[1]:
        with store.connection() as connection:
            connection.execute("UPDATE channel_bindings SET authorized=0 WHERE id=?", (second,))
    if linked[0] is not None:
        now = utc_now()
        with store.connection() as connection:
            connection.execute(
                "INSERT INTO channel_identity_links(id,shared_session_id,created_at) VALUES('existing',(SELECT session_id FROM channel_bindings WHERE id=?),?)",
                (first, now),
            )
            connection.execute(
                "UPDATE channel_bindings SET identity_link_id='existing' WHERE id=?", (first,)
            )
    if same_channel or not all(authorized) or linked[0] is not None:
        with pytest.raises(ValueError):
            ChannelIdentityLinks(store).create_link((first, second), first)
    else:
        pytest.fail("test case must represent an invalid link")


def test_link_requires_selected_history_binding_to_be_a_member(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "identity-links-history.sqlite3")
    first, _ = _binding(store, "telegram", "bot", "one")
    second, _ = _binding(store, "email", "mailbox", "two")
    with pytest.raises(ValueError, match="choose whose history"):
        ChannelIdentityLinks(store).create_link((first, second), "unrelated")


def test_schema_20_database_migrates_identity_link_tables_without_losing_bindings(
    workspace_tmp_path,
):
    database = workspace_tmp_path / "identity-links-upgrade.sqlite3"
    store = RunStore(database)
    binding_id, _ = _binding(store, "telegram", "bot-1", "user-1")
    with store.connection() as connection:
        connection.execute("DROP TABLE channel_identity_link_events")
        connection.execute("DROP TABLE channel_identity_links")
        connection.execute("PRAGMA user_version=20")

    upgraded = RunStore(database)
    with upgraded.connection() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        binding = connection.execute(
            "SELECT id FROM channel_bindings WHERE id=?", (binding_id,)
        ).fetchone()
    assert version == 21
    assert {"channel_identity_links", "channel_identity_link_events"} <= tables
    assert binding["id"] == binding_id

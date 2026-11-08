from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from xaa.channels.discord import (
    DiscordApiError,
    DiscordBotApi,
    DiscordIdentity,
    DiscordMessage,
    DiscordStore,
)
from xaa.storage.runs import RunStore

IDENTITY = DiscordIdentity("100000000000000001", "xaa-test")


def test_discord_reply_request_suppresses_mentions_and_bounds_content(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "900000000000000005"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, json=json)
            return Response()

    monkeypatch.setattr("xaa.channels.discord.httpx.AsyncClient", lambda **_kwargs: Client())
    sent_id = asyncio.run(
        DiscordBotApi("test-token").send_message("200000000000000002", "x" * 2050, nonce="n" * 24)
    )
    assert sent_id == "900000000000000005"
    assert captured["url"] == "https://discord.com/api/v10/channels/200000000000000002/messages"
    assert captured["headers"] == {"Authorization": "Bot test-token"}
    assert captured["json"] == {
        "content": "x" * 2000,
        "nonce": "n" * 24,
        "enforce_nonce": True,
        "allowed_mentions": {"parse": []},
    }


@pytest.mark.parametrize(("status", "definitive"), [(429, True), (503, False)])
def test_discord_reply_classifies_http_rejections(status, definitive, monkeypatch) -> None:
    class Response:
        status_code = status

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr("xaa.channels.discord.httpx.AsyncClient", lambda **_kwargs: Client())
    with pytest.raises(DiscordApiError) as error:
        asyncio.run(
            DiscordBotApi("test-token").send_message("200000000000000002", "hello", nonce="n" * 24)
        )
    assert error.value.definitive is definitive


def message(
    message_id: int,
    author_id: int,
    content: str,
    *,
    direct: bool = True,
    channel_id: int = 200000000000000002,
) -> DiscordMessage:
    return DiscordMessage(str(message_id), str(author_id), str(channel_id), content, direct)


def test_discord_pairing_is_one_time_and_private_only(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord.sqlite3")
    channel = DiscordStore(runs)
    channel.save_bot(IDENTITY)
    channel.create_pairing(IDENTITY.account_id, "X" * 32)

    guild = channel.process_message(
        IDENTITY,
        message(1, 300000000000000003, "/pair " + "X" * 32, direct=False),
        model="test/model",
    )
    paired = channel.process_message(
        IDENTITY, message(2, 300000000000000003, "/pair " + "X" * 32), model="test/model"
    )
    reused = channel.process_message(
        IDENTITY, message(3, 400000000000000004, "/pair " + "X" * 32), model="test/model"
    )

    assert guild["state"] == "ignored"
    assert paired["state"] == "paired"
    assert reused["state"] == "pairing_rejected"
    assert channel.linked_users(IDENTITY.account_id) == ["300000000000000003"]


def test_discord_message_is_idempotent_and_runs_observe_only(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord-idempotency.sqlite3")
    channel = DiscordStore(runs)
    channel.save_bot(IDENTITY)
    channel.create_pairing(IDENTITY.account_id, "Y" * 32, ttl=timedelta(minutes=5))
    channel.process_message(
        IDENTITY, message(10, 300000000000000003, "/pair " + "Y" * 32), model="test/model"
    )

    inbound = message(11, 300000000000000003, "Read my notes")
    first = channel.process_message(IDENTITY, inbound, model="test/model")
    duplicate = channel.process_message(IDENTITY, inbound, model="test/model")

    assert first["state"] == "queued"
    assert first["run_id"]
    assert duplicate == {"state": "queued", "run_id": first["run_id"], "duplicate": True}
    run = runs.get_run(first["run_id"])
    assert run is not None
    assert run["mode"] == "observe"
    assert run["workspace"] is None
    assert run["command_grant"] is None
    assert run["approved_web_hosts"] == []
    assert run["approved_mcp_tools"] == []
    assert channel.profile() == {
        "configured": True,
        "bot_id": IDENTITY.account_id,
        "bot_username": IDENTITY.username,
        "linked_users": 1,
        "processed_messages": 2,
        "deliveries": {"pending": 1},
        "unknown_deliveries": [],
    }


def test_discord_unlinked_user_and_model_unavailable_cannot_start_runs(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord-deny.sqlite3")
    channel = DiscordStore(runs)
    channel.save_bot(IDENTITY)
    unlinked = channel.process_message(
        IDENTITY, message(20, 300000000000000003, "Do work"), model="test/model"
    )
    assert unlinked["state"] == "ignored"
    channel.create_pairing(IDENTITY.account_id, "Z" * 32)
    channel.process_message(
        IDENTITY, message(21, 300000000000000003, "/pair " + "Z" * 32), model="test/model"
    )
    no_model = channel.process_message(
        IDENTITY, message(22, 300000000000000003, "Do work"), model=None
    )
    assert no_model["state"] == "model_unavailable"
    with runs.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_discord_revocation_disables_existing_binding(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord-revoke.sqlite3")
    channel = DiscordStore(runs)
    channel.save_bot(IDENTITY)
    channel.create_pairing(IDENTITY.account_id, "Q" * 32)
    channel.process_message(
        IDENTITY, message(30, 300000000000000003, "/pair " + "Q" * 32), model="test/model"
    )

    assert channel.revoke_users(IDENTITY.account_id) == 1
    revoked_message = channel.process_message(
        IDENTITY, message(31, 300000000000000003, "Do work"), model="test/model"
    )
    assert revoked_message["state"] == "ignored"
    assert channel.profile()["linked_users"] == 0


def test_discord_pairing_code_is_bound_to_bot_account(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord-account-scope.sqlite3")
    channel = DiscordStore(runs)
    second_bot = DiscordIdentity("100000000000000002", "xaa-other")
    channel.save_bot(IDENTITY)
    channel.create_pairing(IDENTITY.account_id, "R" * 32)

    wrong_bot = channel.process_message(
        second_bot,
        message(40, 300000000000000003, "/pair " + "R" * 32),
        model="test/model",
    )
    right_bot = channel.process_message(
        IDENTITY,
        message(41, 300000000000000003, "/pair " + "R" * 32),
        model="test/model",
    )

    assert wrong_bot["state"] == "pairing_rejected"
    assert right_bot["state"] == "paired"
    assert channel.linked_users(IDENTITY.account_id) == ["300000000000000003"]
    assert channel.linked_users(second_bot.account_id) == []


def test_discord_rejects_malformed_event_identifiers(tmp_path) -> None:
    channel = DiscordStore(RunStore(tmp_path / "discord-malformed.sqlite3"))
    malformed = DiscordMessage("message-id", "300000000000000003", "200000000000000002", "hi", True)

    assert channel.process_message(IDENTITY, malformed, model="test/model") == {
        "state": "invalid",
        "run_id": None,
    }


def make_pending_discord_run(channel: DiscordStore, runs: RunStore) -> str:
    channel.save_bot(IDENTITY)
    channel.create_pairing(IDENTITY.account_id, "S" * 32)
    channel.process_message(
        IDENTITY,
        message(50, 300000000000000003, "/pair " + "S" * 32),
        model="test/model",
    )
    result = channel.process_message(
        IDENTITY, message(51, 300000000000000003, "Do work"), model="test/model"
    )
    assert isinstance(result["run_id"], str)
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET state='completed',updated_at=? WHERE id=?",
            ("2026-11-08T12:00:00+00:00", result["run_id"]),
        )
        RunStore.record_event(
            connection,
            result["run_id"],
            "model.message",
            {"message": {"role": "assistant", "content": "A saved final answer."}},
        )
    return result["run_id"]


def test_discord_outbox_claim_recovery_and_sent_receipt(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord-outbox.sqlite3")
    channel = DiscordStore(runs)
    run_id = make_pending_discord_run(channel, runs)

    delivery = channel.claim_delivery(IDENTITY.account_id)
    assert delivery is not None
    assert delivery["message_id"] == "51"
    assert delivery["channel_id"] == "200000000000000002"
    assert isinstance(delivery["session_id"], str)
    assert delivery["run_id"] == run_id
    assert delivery["text"] == "A saved final answer."
    with runs.connection() as connection:
        assert (
            connection.execute(
                "SELECT delivery_state FROM discord_messages WHERE account_id=? AND message_id='51'",
                (IDENTITY.account_id,),
            ).fetchone()[0]
            == "sending"
        )

    assert channel.reconcile_abandoned_sends() == 1
    unknown = channel.profile(IDENTITY.account_id)["unknown_deliveries"]
    assert len(unknown) == 1
    assert unknown[0]["message_id"] == "51"
    assert unknown[0]["user_id"] == "300000000000000003"
    assert unknown[0]["run_id"] == run_id
    assert isinstance(unknown[0]["updated_at"], str)
    assert channel.reconcile_delivery(IDENTITY.account_id, "51", "retry") is True
    retry = channel.claim_delivery(IDENTITY.account_id)
    assert retry is not None and retry["text"] == "A saved final answer."
    channel.finish_delivery(
        IDENTITY.account_id,
        "51",
        state="sent",
        delivery_message_id="900000000000000005",
    )
    assert channel.profile(IDENTITY.account_id)["deliveries"] == {"sent": 1}
    assert channel.profile(IDENTITY.account_id)["unknown_deliveries"] == []
    with runs.connection() as connection:
        assert connection.execute(
            "SELECT delivery_state,delivery_message_id FROM discord_messages WHERE account_id=? AND message_id='51'",
            (IDENTITY.account_id,),
        ).fetchone()[:] == ("sent", "900000000000000005")


def test_discord_delivery_retry_is_bounded_and_revocation_cancels_pending(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord-outbox-retry.sqlite3")
    channel = DiscordStore(runs)
    make_pending_discord_run(channel, runs)

    for _ in range(3):
        delivery = channel.claim_delivery(IDENTITY.account_id)
        assert delivery is not None
        channel.finish_delivery(IDENTITY.account_id, "51", state="unknown")
        if _ < 2:
            assert channel.reconcile_delivery(IDENTITY.account_id, "51", "retry") is True
    assert channel.reconcile_delivery(IDENTITY.account_id, "51", "retry") is False
    assert channel.reconcile_delivery(IDENTITY.account_id, "51", "delivered") is True

    revoked_runs = RunStore(tmp_path / "discord-outbox-revoke.sqlite3")
    revoked_channel = DiscordStore(revoked_runs)
    make_pending_discord_run(revoked_channel, revoked_runs)
    assert revoked_channel.revoke_users(IDENTITY.account_id) == 1
    assert revoked_channel.claim_delivery(IDENTITY.account_id) is None
    assert revoked_channel.profile(IDENTITY.account_id)["deliveries"]["revoked"] == 1


def test_discord_store_upgrades_pre_outbox_schema(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord-migration.sqlite3")
    with runs.connection() as connection:
        connection.executescript(
            """
            CREATE TABLE discord_messages (
                account_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                user_id TEXT,
                channel_id TEXT,
                session_id TEXT,
                run_id TEXT,
                state TEXT NOT NULL,
                received_at TEXT NOT NULL,
                PRIMARY KEY(account_id,message_id)
            );
            INSERT INTO discord_messages VALUES ('100000000000000001','60',NULL,NULL,NULL,NULL,'ignored','2026-11-08T12:00:00+00:00');
            """
        )

    channel = DiscordStore(runs)
    with runs.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(discord_messages)")}
        row = connection.execute(
            "SELECT updated_at FROM discord_messages WHERE message_id='60'"
        ).fetchone()
    assert {"delivery_state", "delivery_message_id", "updated_at"} <= columns
    assert row["updated_at"] == "2026-11-08T12:00:00+00:00"
    assert channel.reconcile_abandoned_sends() == 0

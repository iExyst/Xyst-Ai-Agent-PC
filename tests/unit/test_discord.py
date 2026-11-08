from __future__ import annotations

from datetime import timedelta

from xaa.channels.discord import DiscordIdentity, DiscordMessage, DiscordStore
from xaa.storage.runs import RunStore

IDENTITY = DiscordIdentity("100000000000000001", "xaa-test")


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
    channel.create_pairing("X" * 32)

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
    channel.create_pairing("Y" * 32, ttl=timedelta(minutes=5))
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
    }


def test_discord_unlinked_user_and_model_unavailable_cannot_start_runs(tmp_path) -> None:
    runs = RunStore(tmp_path / "discord-deny.sqlite3")
    channel = DiscordStore(runs)
    channel.save_bot(IDENTITY)
    unlinked = channel.process_message(
        IDENTITY, message(20, 300000000000000003, "Do work"), model="test/model"
    )
    assert unlinked["state"] == "ignored"
    channel.create_pairing("Z" * 32)
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
    channel.create_pairing("Q" * 32)
    channel.process_message(
        IDENTITY, message(30, 300000000000000003, "/pair " + "Q" * 32), model="test/model"
    )

    assert channel.revoke_users(IDENTITY.account_id) == 1
    revoked_message = channel.process_message(
        IDENTITY, message(31, 300000000000000003, "Do work"), model="test/model"
    )
    assert revoked_message["state"] == "ignored"
    assert channel.profile()["linked_users"] == 0

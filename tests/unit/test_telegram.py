from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.channels.telegram import (
    TelegramApiError,
    TelegramBotApi,
    TelegramIdentity,
    TelegramService,
    TelegramStore,
)
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.runs import RunStore, utc_now

TOKEN = "a-test-token-that-is-long-enough-to-pass-validation"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
IDENTITY = TelegramIdentity("bot-42", "xaa_test_bot")
PAIR_CODE = "A_very_long_secure_pairing_code_987654321"


class MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.values.get(profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        self.values[profile] = token

    def delete(self, profile: str = "lm-studio") -> None:
        self.values.pop(profile, None)


class QuietProvider(LMStudioProvider):
    async def close(self) -> None:
        return None


class FakeTelegramBotApi:
    def __init__(self, token: str) -> None:
        self.token = token

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get_me(self) -> TelegramIdentity:
        return IDENTITY


def private_update(update_id: int, user_id: int, text: str) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "text": text,
            "from": {"id": user_id, "is_bot": False},
            "chat": {"id": user_id, "type": "private"},
        },
    }


def pair_user(store: TelegramStore, user_id: int = 123) -> str:
    store.create_pairing(PAIR_CODE)
    result = store.process_update(
        IDENTITY, private_update(1, user_id, f"/pair {PAIR_CODE}"), model="fixture/model"
    )
    assert result["state"] == "paired"
    return str(user_id)


def test_pairing_authorizes_private_user_and_duplicate_updates_create_one_run(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "telegram.sqlite3")
    with runs.connection() as connection:
        connection.execute(
            "UPDATE provider_profiles SET selected_model='fixture/model' WHERE id='lm-studio'"
        )
    channel = TelegramStore(runs)
    channel.save_bot(IDENTITY)
    pair_user(channel)

    message = private_update(2, 123, "Inspect the project and report its status")
    first = channel.process_update(IDENTITY, message, model="fixture/model")
    duplicate = channel.process_update(IDENTITY, message, model="fixture/model")

    assert first["state"] == "queued"
    assert first["run_id"] == duplicate["run_id"]
    assert duplicate["duplicate"] is True
    run = runs.get_run(str(first["run_id"]))
    assert run is not None
    assert (run["mode"], run["model"], run["workspace"]) == (
        "observe",
        "fixture/model",
        None,
    )
    with runs.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
        assert connection.execute("SELECT next_offset FROM telegram_offsets").fetchone()[0] == 3


def test_unpaired_users_and_group_chats_cannot_create_runs(workspace_tmp_path: Path) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-auth.sqlite3")
    channel = TelegramStore(runs)
    unpaired = channel.process_update(
        IDENTITY, private_update(1, 555, "Do something"), model="fixture/model"
    )
    group = {
        "update_id": 2,
        "message": {
            "text": "Do something",
            "from": {"id": 555},
            "chat": {"id": -100555, "type": "supergroup"},
        },
    }
    group_result = channel.process_update(IDENTITY, group, model="fixture/model")
    assert unpaired["state"] == "ignored"
    assert group_result["state"] == "ignored"
    with runs.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM channel_bindings").fetchone()[0] == 0


def test_pairing_is_single_use_and_requires_the_selected_model(workspace_tmp_path: Path) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-pairing.sqlite3")
    channel = TelegramStore(runs)
    channel.create_pairing(PAIR_CODE)
    paired = channel.process_update(
        IDENTITY, private_update(1, 123, f"/pair {PAIR_CODE}"), model=None
    )
    rejected = channel.process_update(
        IDENTITY, private_update(2, 456, f"/pair {PAIR_CODE}"), model="fixture/model"
    )
    no_model = channel.process_update(IDENTITY, private_update(3, 123, "Run a task"), model=None)
    assert paired["state"] == "paired"
    assert rejected["state"] == "pairing_rejected"
    assert no_model["state"] == "model_unavailable"
    with runs.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        saved_hash = connection.execute("SELECT code_hash FROM telegram_pairing_codes").fetchone()[
            0
        ]
    assert PAIR_CODE not in saved_hash


def test_delivery_outbox_claims_once_and_persists_reply_to_session(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-delivery.sqlite3")
    channel = TelegramStore(runs)
    pair_user(channel)
    queued = channel.process_update(
        IDENTITY, private_update(2, 123, "Tell me a fact"), model="fixture/model"
    )
    run_id = str(queued["run_id"])
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET state='completed',updated_at=? WHERE id=?", (utc_now(), run_id)
        )
        RunStore.record_event(
            connection,
            run_id,
            "model.message",
            {"message": {"role": "assistant", "content": "A grounded final answer."}},
        )
    delivery = channel.claim_delivery(IDENTITY.account_id)
    assert delivery is not None
    assert delivery["text"] == "A grounded final answer."
    assert channel.claim_delivery(IDENTITY.account_id) is None
    channel.finish_delivery(
        IDENTITY.account_id,
        delivery["update_id"],
        state="sent",
        message_id=88,
        text=delivery["text"],
    )
    assert channel.claim_delivery(IDENTITY.account_id) is None
    with runs.connection() as connection:
        recorded_delivery = connection.execute(
            "SELECT delivery_state,delivery_message_id FROM telegram_updates WHERE update_id=2"
        ).fetchone()
        reply = connection.execute(
            "SELECT role,content FROM messages ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
    assert tuple(recorded_delivery) == ("sent", 88)
    assert tuple(reply) == ("assistant", "A grounded final answer.")


def test_unknown_delivery_is_never_automatically_retried(workspace_tmp_path: Path) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-unknown.sqlite3")
    channel = TelegramStore(runs)
    pair_user(channel)
    queued = channel.process_update(
        IDENTITY, private_update(2, 123, "Tell me something"), model="fixture/model"
    )
    run_id = str(queued["run_id"])
    with runs.connection() as connection:
        connection.execute("UPDATE runs SET state='completed' WHERE id=?", (run_id,))
        RunStore.record_event(
            connection,
            run_id,
            "model.message",
            {"message": {"role": "assistant", "content": "Reply."}},
        )
    delivery = channel.claim_delivery(IDENTITY.account_id)
    assert delivery is not None
    channel.finish_delivery(IDENTITY.account_id, delivery["update_id"], state="unknown")
    assert channel.claim_delivery(IDENTITY.account_id) is None


def test_abandoned_send_intent_becomes_unknown_after_restart(workspace_tmp_path: Path) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-recovery.sqlite3")
    channel = TelegramStore(runs)
    pair_user(channel)
    queued = channel.process_update(
        IDENTITY, private_update(2, 123, "Tell me something"), model="fixture/model"
    )
    with runs.connection() as connection:
        connection.execute("UPDATE runs SET state='completed' WHERE id=?", (queued["run_id"],))
        RunStore.record_event(
            connection,
            str(queued["run_id"]),
            "model.message",
            {"message": {"role": "assistant", "content": "Reply."}},
        )
    delivery = channel.claim_delivery(IDENTITY.account_id)
    assert delivery is not None
    reopened = TelegramStore(RunStore(runs.path))
    assert reopened.reconcile_abandoned_sends() == 1
    assert reopened.claim_delivery(IDENTITY.account_id) is None
    assert reopened.profile(IDENTITY.account_id)["deliveries"] == {"unknown": 1}


def test_old_poll_cursor_resets_before_telegram_randomizes_update_ids(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-offset.sqlite3")
    with runs.connection() as connection:
        connection.execute(
            "INSERT INTO telegram_offsets VALUES (?,?,?)",
            (IDENTITY.account_id, 500, (datetime.now(UTC) - timedelta(days=7)).isoformat()),
        )
    assert TelegramStore(runs).current_offset(IDENTITY.account_id) is None
    with runs.connection() as connection:
        assert connection.execute("SELECT count(*) FROM telegram_offsets").fetchone()[0] == 0


def test_terminal_run_sends_a_generic_failure_notice_to_the_paired_chat(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-failure.sqlite3")
    channel = TelegramStore(runs)
    pair_user(channel)
    queued = channel.process_update(
        IDENTITY, private_update(2, 123, "Analyze this"), model="fixture/model"
    )
    with runs.connection() as connection:
        connection.execute("UPDATE runs SET state='failed' WHERE id=?", (queued["run_id"],))
    delivery = channel.claim_delivery(IDENTITY.account_id)
    assert delivery is not None
    assert "could not complete" in delivery["text"]
    assert channel.claim_delivery(IDENTITY.account_id) is None


def test_telegram_api_uses_https_and_sanitizes_errors() -> None:
    token = "123456:super-secret-token"
    paths: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        paths.append(str(request.url))
        return httpx.Response(200, json={"ok": False, "description": "invalid token"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = TelegramBotApi(token, client=client)
            try:
                await api.get_me()
            except TelegramApiError as failure:
                assert token not in str(failure)
            else:
                raise AssertionError("Invalid token response was accepted")

    import asyncio

    asyncio.run(scenario())
    assert paths[0].startswith("https://api.telegram.org/bot")
    assert token in paths[0]  # Telegram Bot API authenticates in its HTTPS URL path.


def test_telegram_service_routes_paired_input_through_coordinator_and_delivers_once(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-service.sqlite3")
    create_app(QuietProvider(), SecretStr(TOKEN), store=runs)
    with runs.connection() as connection:
        connection.execute(
            "UPDATE provider_profiles SET selected_model='fixture/model' WHERE id='lm-studio'"
        )
    store = TelegramStore(runs)
    store.create_pairing(PAIR_CODE)
    credentials = MemoryCredentials()
    credentials.set("fixture-token", "telegram-bot")
    updates = [
        private_update(10, 123, f"/pair {PAIR_CODE}"),
        private_update(11, 123, "Summarize the project"),
    ]
    sent: list[tuple[str, str]] = []
    submitted: list[str] = []

    class FakeChannelApi:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_me(self) -> TelegramIdentity:
            return IDENTITY

        async def get_updates(self, offset: int | None) -> list[dict]:
            batch, updates[:] = list(updates), []
            return batch

        async def send_message(self, chat_id: str, text: str) -> int:
            sent.append((chat_id, text))
            return 91

    service = TelegramService(
        runs,
        credentials,
        submitted.append,
        api_factory=lambda _: FakeChannelApi(),
    )

    import asyncio

    async def scenario() -> None:
        await service.cycle()
        assert len(submitted) == 1
        with runs.connection() as connection:
            run_id = submitted[0]
            connection.execute(
                "UPDATE runs SET state='completed',updated_at=? WHERE id=?",
                (utc_now(), run_id),
            )
            RunStore.record_event(
                connection,
                run_id,
                "model.message",
                {"message": {"role": "assistant", "content": "Project summary."}},
            )
        await service.cycle()
        await service.cycle()

    asyncio.run(scenario())
    assert sent == [("123", "Project summary.")]
    assert store.profile(IDENTITY.account_id)["deliveries"] == {"sent": 1}
    with runs.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assistant_messages = connection.execute(
            "SELECT role,content FROM messages WHERE role='assistant'"
        ).fetchall()
    assert [tuple(row) for row in assistant_messages] == [("assistant", "Project summary.")]


def test_telegram_credential_and_pairing_api_never_returns_saved_token(
    workspace_tmp_path: Path, monkeypatch
) -> None:
    import xaa.api.app as app_module

    credentials = MemoryCredentials()
    monkeypatch.setattr(app_module, "TelegramBotApi", FakeTelegramBotApi)
    runs = RunStore(workspace_tmp_path / "telegram-api.sqlite3")
    telegram_store = TelegramStore(runs)
    app = create_app(
        QuietProvider(),
        SecretStr(TOKEN),
        store=runs,
        credential_store=credentials,
    )
    client = TestClient(app)
    assert client.get("/channels/telegram").status_code == 401
    saved = client.put(
        "/channels/telegram/credential",
        headers=HEADERS,
        json={"token": "telegram-secret-value"},
    )
    assert saved.status_code == 200
    assert saved.json()["bot_username"] == "xaa_test_bot"
    assert "telegram-secret-value" not in saved.text
    assert credentials.get("telegram-bot") == "telegram-secret-value"
    pairing = client.post("/channels/telegram/pairing", headers=HEADERS)
    assert pairing.status_code == 201
    assert pairing.json()["code"]
    telegram_store.process_update(
        IDENTITY,
        private_update(10, 456, f"/pair {pairing.json()['code']}"),
        model="fixture/model",
    )
    assert client.get("/channels/telegram", headers=HEADERS).json()["linked_users"] == 1
    revoked = client.delete("/channels/telegram/links", headers=HEADERS)
    assert revoked.json() == {"revoked_users": 1, "linked_users": 0}
    assert (
        telegram_store.process_update(
            IDENTITY, private_update(11, 456, "new request"), model="fixture/model"
        )["state"]
        == "ignored"
    )
    cleared = client.delete("/channels/telegram/credential", headers=HEADERS)
    assert cleared.json()["configured"] is False
    assert credentials.get("telegram-bot") is None

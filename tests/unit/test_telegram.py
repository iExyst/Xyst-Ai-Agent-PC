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
IDENTITY = TelegramIdentity("42", "xaa_test_bot")
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


def queued_scheduled_delivery(runs: RunStore) -> tuple[TelegramStore, str]:
    from xaa.scheduler.service import ScheduleService
    from xaa.storage.schedules import ScheduleStore

    store = TelegramStore(runs)
    pair_user(store, 123)
    current = datetime.now(UTC)
    schedule = ScheduleStore(runs).create(
        objective="Scheduled report",
        acceptance_criteria=[],
        workspace=None,
        model="fixture/model",
        start_at=current.replace(second=0, microsecond=0, tzinfo=None),
        frequency="once",
        timezone="UTC",
        missed_run_policy="skip",
        max_iterations=4,
        max_seconds=60,
        delivery_destination="telegram:42:123",
    )
    run_id = ScheduleService(runs, lambda _run_id: None).tick(current)[0]
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET state='completed',updated_at=? WHERE id=?", (utc_now(), run_id)
        )
        RunStore.record_event(
            connection,
            run_id,
            "model.message",
            {"message": {"role": "assistant", "content": "Scheduled report complete."}},
        )
    assert schedule["delivery_destination"] == "telegram:42:123"
    return store, schedule["id"]


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


def test_unknown_delivery_reconciliation_is_account_scoped_atomic_and_audited(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "telegram-reconcile.sqlite3")
    channel = TelegramStore(runs)
    pair_user(channel)
    queued = channel.process_update(
        IDENTITY, private_update(2, 123, "Tell me something"), model="fixture/model"
    )
    with runs.connection() as connection:
        connection.execute(
            "UPDATE telegram_updates SET delivery_state='unknown' WHERE account_id=? AND update_id=2",
            (IDENTITY.account_id,),
        )
    uncertain = channel.profile(IDENTITY.account_id)["unknown_deliveries"]
    assert len(uncertain) == 1
    assert uncertain[0]["update_id"] == 2
    assert uncertain[0]["user_id"] == "123"
    assert uncertain[0]["run_id"] == queued["run_id"]

    assert channel.reconcile_delivery(IDENTITY.account_id, 2, "retry")
    assert not channel.reconcile_delivery("another-account", 2, "delivered")
    assert not channel.reconcile_delivery(IDENTITY.account_id, 2, "delivered")
    profile = channel.profile(IDENTITY.account_id)
    assert profile["unknown_deliveries"] == []
    assert profile["deliveries"]["pending"] == 1
    with runs.connection() as connection:
        row = connection.execute(
            "SELECT resolution FROM telegram_delivery_reconciliations WHERE account_id=? AND update_id=2",
            (IDENTITY.account_id,),
        ).fetchone()
        state = connection.execute(
            "SELECT delivery_state FROM telegram_updates WHERE account_id=? AND update_id=2",
            (IDENTITY.account_id,),
        ).fetchone()[0]
    assert row["resolution"] == "retry"
    assert state == "pending"


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


def test_scheduled_delivery_unknown_after_restart_is_never_retried(
    workspace_tmp_path: Path,
) -> None:
    from xaa.storage.schedules import ScheduleStore

    database = workspace_tmp_path / "scheduled-delivery-recovery.sqlite3"
    runs = RunStore(database)
    store, schedule_id = queued_scheduled_delivery(runs)
    claimed = store.claim_schedule_delivery(IDENTITY.account_id)
    assert claimed is not None
    assert claimed["text"] == "Scheduled report complete."

    reopened = RunStore(database)
    recovered = TelegramStore(reopened)
    assert recovered.reconcile_abandoned_sends() == 1
    assert recovered.claim_schedule_delivery(IDENTITY.account_id) is None
    schedule = ScheduleStore(reopened).get(schedule_id)
    assert schedule is not None
    assert schedule["executions"][0]["delivery_evidence"] == "unknown"
    with reopened.connection() as connection:
        assert (
            connection.execute("SELECT state FROM schedule_deliveries").fetchone()[0] == "unknown"
        )


def test_scheduled_delivery_reconciliation_is_scoped_audited_and_bounded(
    workspace_tmp_path: Path,
) -> None:
    from xaa.storage.schedules import ScheduleStore

    runs = RunStore(workspace_tmp_path / "scheduled-delivery-reconcile.sqlite3")
    channel, schedule_id = queued_scheduled_delivery(runs)
    delivery = channel.claim_schedule_delivery(IDENTITY.account_id)
    assert delivery is not None
    channel.finish_schedule_delivery(delivery["execution_id"], state="unknown")
    schedule_store = ScheduleStore(runs)
    schedule = schedule_store.get(schedule_id)
    assert schedule is not None
    execution_id = schedule["executions"][0]["id"]
    with runs.connection() as connection:
        for index in range(21):
            connection.execute(
                "INSERT INTO schedule_executions (id,schedule_id,occurrence_id,run_id,state,delivery_evidence,created_at,completed_at) VALUES (?,?,?,NULL,'overlap_skipped',NULL,?,?)",
                (
                    f"later-{index}",
                    schedule_id,
                    f"later-occurrence-{index}",
                    utc_now(),
                    utc_now(),
                ),
            )
    schedule = schedule_store.get(schedule_id)
    assert schedule is not None
    assert execution_id in {item["id"] for item in schedule["executions"]}

    assert schedule_store.reconcile_delivery(schedule_id, execution_id, "42", "retry") == "resolved"
    assert (
        schedule_store.reconcile_delivery(schedule_id, execution_id, "another-account", "delivered")
        == "not_unknown"
    )
    with runs.connection() as connection:
        audit = connection.execute(
            "SELECT resolution FROM schedule_delivery_reconciliations WHERE execution_id=?",
            (execution_id,),
        ).fetchone()
        state = connection.execute(
            "SELECT state FROM schedule_deliveries WHERE execution_id=?", (execution_id,)
        ).fetchone()[0]
        connection.execute(
            "UPDATE schedule_deliveries SET state='unknown',attempt_count=3 WHERE execution_id=?",
            (execution_id,),
        )
    assert audit["resolution"] == "retry"
    assert state == "pending"
    assert (
        schedule_store.reconcile_delivery(schedule_id, execution_id, "42", "retry")
        == "attempt_limit"
    )
    assert (
        schedule_store.reconcile_delivery(schedule_id, execution_id, "42", "delivered")
        == "resolved"
    )


def test_service_cycle_reconciles_interrupted_scheduled_http_send_after_restart(
    workspace_tmp_path: Path,
) -> None:
    import asyncio

    database = workspace_tmp_path / "scheduled-http-send-restart.sqlite3"
    original_runs = RunStore(database)
    original_store, schedule_id = queued_scheduled_delivery(original_runs)
    claimed = original_store.claim_schedule_delivery(IDENTITY.account_id)
    assert claimed is not None
    sends: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getMe"):
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 42, "username": "xaa_test_bot"}}
            )
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": []})
        if request.url.path.endswith("/sendMessage"):
            sends.append(request.url.path)
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
        raise AssertionError(f"Unexpected Telegram API endpoint: {request.url.path}")

    transport = httpx.MockTransport(handler)

    class Credentials(MemoryCredentials):
        def __init__(self) -> None:
            super().__init__()
            self.values["telegram-bot"] = "fixture-token"

    class FixtureApi(TelegramBotApi):
        def __init__(self, token: str) -> None:
            super().__init__(token, client=httpx.AsyncClient(transport=transport))

    reopened = RunStore(database)
    service = TelegramService(reopened, Credentials(), lambda _run_id: None, api_factory=FixtureApi)

    asyncio.run(service.cycle())

    from xaa.storage.schedules import ScheduleStore

    saved = ScheduleStore(reopened).get(schedule_id)
    assert saved is not None
    assert saved["executions"][0]["delivery_evidence"] == "unknown"
    assert sends == []
    with reopened.connection() as connection:
        assert (
            connection.execute("SELECT state FROM schedule_deliveries").fetchone()[0] == "unknown"
        )


def test_revoked_telegram_identity_cannot_receive_scheduled_delivery(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "scheduled-delivery-revoked.sqlite3")
    store, _schedule_id = queued_scheduled_delivery(runs)
    assert store.revoke_users(IDENTITY.account_id) == 1
    assert store.claim_schedule_delivery(IDENTITY.account_id) is None
    with runs.connection() as connection:
        assert (
            connection.execute("SELECT state FROM schedule_deliveries").fetchone()[0] == "revoked"
        )


def test_scheduled_telegram_delivery_waits_until_local_quiet_hours_end(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "scheduled-delivery-quiet-hours.sqlite3")
    store, schedule_id = queued_scheduled_delivery(runs)
    with runs.connection() as connection:
        connection.execute(
            "UPDATE schedules SET quiet_hours='22:00-07:00',timezone='America/New_York' WHERE id=?",
            (schedule_id,),
        )

    # 23:00 local is quiet; the outbox remains pending and no send intent is written.
    assert (
        store.claim_schedule_delivery(
            IDENTITY.account_id, now=datetime(2026, 11, 5, 4, 0, tzinfo=UTC)
        )
        is None
    )
    with runs.connection() as connection:
        assert (
            connection.execute("SELECT state FROM schedule_deliveries").fetchone()[0] == "pending"
        )

    # 08:00 local is outside the window and the same occurrence becomes deliverable.
    delivery = store.claim_schedule_delivery(
        IDENTITY.account_id, now=datetime(2026, 11, 5, 13, 0, tzinfo=UTC)
    )
    assert delivery is not None
    assert delivery["execution_id"]


def test_scheduled_delivery_retries_only_explicit_rate_limits_with_bounded_attempts(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "scheduled-delivery-rate-limit.sqlite3")
    from xaa.storage.schedules import ScheduleStore

    store, schedule_id = queued_scheduled_delivery(runs)
    first_at = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)

    first = store.claim_schedule_delivery(IDENTITY.account_id, now=first_at)
    assert first is not None
    store.finish_schedule_delivery(
        first["execution_id"],
        state="failed",
        retry_after_seconds=10,
        now=first_at,
    )
    assert (
        store.claim_schedule_delivery(IDENTITY.account_id, now=first_at + timedelta(seconds=9))
        is None
    )
    with runs.connection() as connection:
        row = connection.execute(
            "SELECT state,attempt_count,next_attempt_at FROM schedule_deliveries"
        ).fetchone()
        assert row["state"] == "pending"
        assert row["attempt_count"] == 1
        assert row["next_attempt_at"] == (first_at + timedelta(seconds=10)).isoformat()
        assert (
            connection.execute("SELECT delivery_evidence FROM schedule_executions").fetchone()[0]
            == "retrying"
        )
    history = ScheduleStore(runs).get(schedule_id)
    assert history is not None
    assert history["executions"][0]["delivery_attempt_count"] == 1
    assert (
        history["executions"][0]["delivery_next_attempt_at"]
        == (first_at + timedelta(seconds=10)).isoformat()
    )

    # A desktop restart preserves the due time and attempt count, and cannot
    # make the notification eligible before Telegram's requested delay.
    reopened_runs = RunStore(runs.path)
    reopened = TelegramStore(reopened_runs)
    assert (
        reopened.claim_schedule_delivery(IDENTITY.account_id, now=first_at + timedelta(seconds=9))
        is None
    )
    second = reopened.claim_schedule_delivery(
        IDENTITY.account_id, now=first_at + timedelta(seconds=10)
    )
    assert second is not None
    reopened.finish_schedule_delivery(
        second["execution_id"],
        state="failed",
        retry_after_seconds=10,
        now=first_at + timedelta(seconds=10),
    )
    third = store.claim_schedule_delivery(IDENTITY.account_id, now=first_at + timedelta(seconds=20))
    assert third is not None
    store.finish_schedule_delivery(
        third["execution_id"],
        state="failed",
        retry_after_seconds=10,
        now=first_at + timedelta(seconds=20),
    )
    assert (
        store.claim_schedule_delivery(IDENTITY.account_id, now=first_at + timedelta(seconds=40))
        is None
    )
    with runs.connection() as connection:
        row = connection.execute(
            "SELECT state,attempt_count,next_attempt_at FROM schedule_deliveries"
        ).fetchone()
        assert row["state"] == "failed"
        assert row["attempt_count"] == 3
        assert row["next_attempt_at"] is None


def test_revoking_telegram_identity_suppresses_a_pending_rate_limit_retry(
    workspace_tmp_path: Path,
) -> None:
    runs = RunStore(workspace_tmp_path / "scheduled-delivery-retry-revoked.sqlite3")
    store, _schedule_id = queued_scheduled_delivery(runs)
    first_at = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
    first = store.claim_schedule_delivery(IDENTITY.account_id, now=first_at)
    assert first is not None
    store.finish_schedule_delivery(
        first["execution_id"],
        state="failed",
        retry_after_seconds=10,
        now=first_at,
    )

    assert store.revoke_users(IDENTITY.account_id) == 1
    assert (
        store.claim_schedule_delivery(IDENTITY.account_id, now=first_at + timedelta(seconds=10))
        is None
    )
    with runs.connection() as connection:
        row = connection.execute("SELECT state,attempt_count FROM schedule_deliveries").fetchone()
        assert row["state"] == "revoked"
        assert row["attempt_count"] == 1


def test_telegram_rate_limit_exposes_only_bounded_retry_delay() -> None:
    async def scenario() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "description": "Too Many Requests",
                    "parameters": {"retry_after": 3},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = TelegramBotApi("fixture-token", client=client)
            try:
                await api.send_message("123", "Scheduled result")
            except TelegramApiError as failure:
                assert failure.definitive
                assert failure.retry_after_seconds == 3
            else:
                raise AssertionError("Telegram rate limit was accepted as a successful send")

    import asyncio

    asyncio.run(scenario())


def test_telegram_rate_limit_above_policy_bound_is_not_retryable() -> None:
    async def scenario() -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                json={
                    "ok": False,
                    "error_code": 429,
                    "parameters": {"retry_after": 301},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = TelegramBotApi("fixture-token", client=client)
            try:
                await api.send_message("123", "Scheduled result")
            except TelegramApiError as failure:
                assert failure.definitive
                assert failure.retry_after_seconds is None
            else:
                raise AssertionError("Telegram rate limit was accepted as a successful send")

    import asyncio

    asyncio.run(scenario())


def test_failed_scheduled_run_sends_generic_notice(workspace_tmp_path: Path) -> None:
    runs = RunStore(workspace_tmp_path / "scheduled-delivery-failed.sqlite3")
    store, _schedule_id = queued_scheduled_delivery(runs)
    with runs.connection() as connection:
        run_id = connection.execute("SELECT run_id FROM schedule_executions").fetchone()[0]
        connection.execute("UPDATE runs SET state='failed' WHERE id=?", (run_id,))
    delivery = store.claim_schedule_delivery(IDENTITY.account_id)
    assert delivery is not None
    assert delivery["text"] == (
        "X.A.A. could not complete this scheduled objective. "
        "Open the desktop app to review the saved run status."
    )
    store.finish_schedule_delivery(delivery["execution_id"], state="sent", message_id=73)


def test_schedule_uses_separate_failure_notification_destination(workspace_tmp_path: Path) -> None:
    runs = RunStore(workspace_tmp_path / "scheduled-distinct-destinations.sqlite3")
    store = TelegramStore(runs)
    pair_user(store, 123)
    second_code = PAIR_CODE + "_second"
    store.create_pairing(second_code)
    assert (
        store.process_update(
            IDENTITY, private_update(2, 456, f"/pair {second_code}"), model="fixture/model"
        )["state"]
        == "paired"
    )
    from xaa.scheduler.service import ScheduleService
    from xaa.storage.schedules import ScheduleStore

    now = datetime.now(UTC)
    schedule = ScheduleStore(runs).create(
        objective="Distinct notification test",
        acceptance_criteria=[],
        workspace=None,
        model="fixture/model",
        start_at=now.replace(second=0, microsecond=0, tzinfo=None),
        frequency="once",
        timezone="UTC",
        missed_run_policy="skip",
        max_iterations=4,
        max_seconds=60,
        delivery_destination="telegram:42:123",
        failure_notification_destination="telegram:42:456",
    )
    run_id = ScheduleService(runs, lambda _run_id: None).tick(now)[0]
    with runs.connection() as connection:
        connection.execute("UPDATE runs SET state='failed' WHERE id=?", (run_id,))
    assert store.linked_users(IDENTITY.account_id) == ["123", "456"]
    delivery = store.claim_schedule_delivery(IDENTITY.account_id)
    assert delivery is not None
    assert delivery["chat_id"] == "456"
    assert "could not complete" in delivery["text"]
    assert schedule["failure_notification_destination"] == "telegram:42:456"


def test_failure_only_schedule_skips_success_delivery(workspace_tmp_path: Path) -> None:
    runs = RunStore(workspace_tmp_path / "scheduled-failure-only.sqlite3")
    store = TelegramStore(runs)
    second_code = PAIR_CODE + "_only"
    store.create_pairing(second_code)
    assert (
        store.process_update(
            IDENTITY, private_update(2, 456, f"/pair {second_code}"), model="fixture/model"
        )["state"]
        == "paired"
    )
    from xaa.scheduler.service import ScheduleService
    from xaa.storage.schedules import ScheduleStore

    now = datetime.now(UTC)
    ScheduleStore(runs).create(
        objective="Failure-only notification test",
        acceptance_criteria=[],
        workspace=None,
        model="fixture/model",
        start_at=now.replace(second=0, microsecond=0, tzinfo=None),
        frequency="once",
        timezone="UTC",
        missed_run_policy="skip",
        max_iterations=4,
        max_seconds=60,
        failure_notification_destination="telegram:42:456",
    )
    run_id = ScheduleService(runs, lambda _run_id: None).tick(now)[0]
    with runs.connection() as connection:
        connection.execute("UPDATE runs SET state='completed' WHERE id=?", (run_id,))
        RunStore.record_event(
            connection,
            run_id,
            "model.message",
            {"message": {"role": "assistant", "content": "All done."}},
        )
    assert store.claim_schedule_delivery(IDENTITY.account_id) is None
    with runs.connection() as connection:
        assert (
            connection.execute(
                "SELECT delivery_evidence FROM schedule_executions WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            == "not_required"
        )


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

        from xaa.storage.schedules import ScheduleStore

        current = datetime.now(UTC)
        schedule = ScheduleStore(runs).create(
            objective="Scheduled report",
            acceptance_criteria=[],
            workspace=None,
            model="fixture/model",
            start_at=current.replace(second=0, microsecond=0, tzinfo=None),
            frequency="once",
            timezone="UTC",
            missed_run_policy="skip",
            max_iterations=4,
            max_seconds=60,
            delivery_destination="telegram:42:123",
        )
        from xaa.scheduler.service import ScheduleService

        scheduled_runs = ScheduleService(runs, submitted.append).tick(current)
        assert len(scheduled_runs) == 1
        queued_schedule = ScheduleStore(runs).get(schedule["id"])
        assert queued_schedule["executions"][0]["delivery_evidence"] == "pending"
        scheduled_run = scheduled_runs[0]
        with runs.connection() as connection:
            connection.execute(
                "UPDATE runs SET state='completed',updated_at=? WHERE id=?",
                (utc_now(), scheduled_run),
            )
            RunStore.record_event(
                connection,
                scheduled_run,
                "model.message",
                {"message": {"role": "assistant", "content": "Scheduled report complete."}},
            )
        await service.cycle()
        saved_schedule = ScheduleStore(runs).get(schedule["id"])
        assert saved_schedule["executions"][0]["delivery_evidence"] == "sent"

    asyncio.run(scenario())
    assert sent == [("123", "Project summary."), ("123", "Scheduled report complete.")]
    assert store.profile(IDENTITY.account_id)["deliveries"] == {"sent": 1}
    with runs.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
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
    assert client.get("/channels/telegram/linked-users", headers=HEADERS).json() == [
        {"user_id": "456"}
    ]
    uncertain = telegram_store.process_update(
        IDENTITY, private_update(12, 456, "uncertain reply"), model="fixture/model"
    )
    with runs.connection() as connection:
        connection.execute(
            "UPDATE telegram_updates SET delivery_state='unknown' WHERE account_id=? AND update_id=12",
            (IDENTITY.account_id,),
        )
    status = client.get("/channels/telegram", headers=HEADERS).json()
    assert status["unknown_deliveries"][0]["update_id"] == 12
    assert status["unknown_deliveries"][0]["run_id"] == uncertain["run_id"]
    rejected = client.post(
        "/channels/telegram/deliveries/12/reconcile",
        headers=HEADERS,
        json={"resolution": "retry", "confirmed_destination_checked": False},
    )
    assert rejected.status_code == 422
    reconciled = client.post(
        "/channels/telegram/deliveries/12/reconcile",
        headers=HEADERS,
        json={"resolution": "delivered", "confirmed_destination_checked": True},
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["unknown_deliveries"] == []
    assert reconciled.json()["deliveries"]["sent"] == 1
    assert (
        client.post(
            "/channels/telegram/deliveries/12/reconcile",
            headers=HEADERS,
            json={"resolution": "retry", "confirmed_destination_checked": True},
        ).status_code
        == 409
    )
    from xaa.scheduler.service import ScheduleService
    from xaa.storage.schedules import ScheduleStore

    current = datetime.now(UTC)
    schedule = ScheduleStore(runs).create(
        objective="Scheduled status",
        acceptance_criteria=[],
        workspace=None,
        model="fixture/model",
        start_at=current.replace(second=0, microsecond=0, tzinfo=None),
        frequency="once",
        timezone="UTC",
        missed_run_policy="skip",
        max_iterations=4,
        max_seconds=60,
        delivery_destination="telegram:42:456",
    )
    scheduled_run = ScheduleService(runs, lambda _run_id: None).tick(current)[0]
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET state='completed',updated_at=? WHERE id=?",
            (utc_now(), scheduled_run),
        )
        RunStore.record_event(
            connection,
            scheduled_run,
            "model.message",
            {"message": {"role": "assistant", "content": "Scheduled result."}},
        )
    schedule_execution = ScheduleStore(runs).get(schedule["id"])["executions"][0]["id"]
    scheduled_delivery = telegram_store.claim_schedule_delivery(IDENTITY.account_id)
    assert scheduled_delivery is not None
    telegram_store.finish_schedule_delivery(scheduled_delivery["execution_id"], state="unknown")
    schedule_response = client.post(
        f"/schedules/{schedule['id']}/executions/{schedule_execution}/delivery/reconcile",
        headers=HEADERS,
        json={"resolution": "delivered", "confirmed_destination_checked": True},
    )
    assert schedule_response.status_code == 200
    assert schedule_response.json()["executions"][0]["delivery_evidence"] == "sent"
    with runs.connection() as connection:
        connection.execute(
            "UPDATE schedule_deliveries SET state='unknown' WHERE execution_id=?",
            (scheduled_delivery["execution_id"],),
        )
        connection.execute(
            "UPDATE schedule_executions SET delivery_evidence='unknown' WHERE id=?",
            (scheduled_delivery["execution_id"],),
        )
    schedule_retry = client.post(
        f"/schedules/{schedule['id']}/executions/{schedule_execution}/delivery/reconcile",
        headers=HEADERS,
        json={"resolution": "retry", "confirmed_destination_checked": True},
    )
    assert schedule_retry.status_code == 200
    retried_execution = next(
        item for item in schedule_retry.json()["executions"] if item["id"] == schedule_execution
    )
    assert retried_execution["delivery_evidence"] == "pending"
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

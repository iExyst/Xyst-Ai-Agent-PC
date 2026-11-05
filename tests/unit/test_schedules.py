from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.scheduler.service import ScheduleService, due_occurrence
from xaa.storage.runs import RunStore
from xaa.storage.schedules import ScheduleStore, local_instant, parse_local_time

TOKEN = "a-test-token-that-is-long-enough-to-pass-validation"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class ScheduleProvider(LMStudioProvider):
    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fixture/model")]

    async def health(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def create_schedule(
    store: RunStore,
    *,
    start_at: str,
    frequency: Literal["once", "daily", "weekly"] = "once",
    policy: Literal["skip", "run_once"] = "skip",
) -> dict[str, Any]:
    return ScheduleStore(store).create(
        objective="Inspect the workspace",
        acceptance_criteria=["Report evidence"],
        workspace=None,
        model="fixture/model",
        start_at=parse_local_time(start_at),
        frequency=frequency,
        timezone="UTC",
        missed_run_policy=policy,
        max_iterations=4,
        max_seconds=60,
    )


def test_local_timezone_resolves_dst_gap_and_ambiguous_time() -> None:
    assert local_instant(datetime(2025, 3, 9, 2, 30), "America/New_York") is None
    assert local_instant(datetime(2025, 11, 2, 1, 30), "America/New_York") == datetime(
        2025, 11, 2, 5, 30, tzinfo=UTC
    )


def test_daily_schedule_skips_nonexistent_wall_time_and_advances() -> None:
    recurrence = {"next_at": "2025-03-08T02:30", "frequency": "daily"}
    due = due_occurrence(recurrence, "America/New_York", datetime(2025, 3, 9, 8, tzinfo=UTC))
    assert due == (datetime(2025, 3, 9, 2, 30), None, 2)


def test_dst_gap_is_persisted_as_skipped_and_recurrence_advances(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "dst.sqlite3")
    schedule = ScheduleStore(store).create(
        objective="Inspect the workspace",
        acceptance_criteria=[],
        workspace=None,
        model="fixture/model",
        start_at=parse_local_time("2025-03-08T02:30"),
        frequency="daily",
        timezone="America/New_York",
        missed_run_policy="skip",
        max_iterations=4,
        max_seconds=60,
    )
    submitted: list[str] = []

    assert (
        ScheduleService(store, submitted.append).tick(datetime(2025, 3, 9, 7, 0, tzinfo=UTC)) == []
    )
    saved = ScheduleStore(store).get(schedule["id"])
    assert saved is not None
    assert saved["executions"][0]["state"] == "dst_skipped"
    assert saved["recurrence"]["next_at"] == "2025-03-10T02:30"
    assert submitted == []


def test_run_once_uses_latest_missed_occurrence_and_overlap_is_skipped(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "overlap.sqlite3")
    schedule = create_schedule(
        store, start_at="2025-01-01T12:00", frequency="daily", policy="run_once"
    )
    submitted: list[str] = []
    service = ScheduleService(store, submitted.append)

    first_ids = service.tick(datetime(2025, 1, 4, 12, 0, 2, tzinfo=UTC))
    assert len(first_ids) == 1
    assert service.tick(datetime(2025, 1, 5, 12, 0, 2, tzinfo=UTC)) == []
    saved = ScheduleStore(store).get(schedule["id"])
    assert saved is not None
    assert [item["state"] for item in saved["executions"]] == ["overlap_skipped", "queued"]
    assert saved["executions"][0]["occurrence_id"].endswith("2025-01-05T12:00")
    assert saved["executions"][1]["occurrence_id"].endswith("2025-01-04T12:00")
    assert submitted == first_ids


def test_one_time_occurrence_is_unique_across_ticks_and_store_reopen(
    workspace_tmp_path: Path,
) -> None:
    database = workspace_tmp_path / "schedules.sqlite3"
    store = RunStore(database)
    schedule = create_schedule(store, start_at="2025-01-02T12:00")
    submitted: list[str] = []
    now = datetime(2025, 1, 2, 12, 0, 2, tzinfo=UTC)

    assert len(ScheduleService(store, submitted.append).tick(now)) == 1
    assert ScheduleService(store, submitted.append).tick(now) == []
    reopened = RunStore(database)
    assert ScheduleService(reopened, submitted.append).tick(now) == []
    assert len(submitted) == 1
    with reopened.connection() as connection:
        assert connection.execute("SELECT count(*) FROM schedule_executions").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        row = connection.execute(
            "SELECT state,run_id FROM schedule_executions WHERE schedule_id=?", (schedule["id"],)
        ).fetchone()
    assert row["state"] == "queued"
    assert row["run_id"] == submitted[0]
    assert ScheduleStore(reopened).get(schedule["id"])["enabled"] is False


def test_missed_daily_occurrences_are_recorded_once_without_duplicate_runs(
    workspace_tmp_path: Path,
) -> None:
    store = RunStore(workspace_tmp_path / "missed.sqlite3")
    schedule = create_schedule(store, start_at="2025-01-01T12:00", frequency="daily")
    submitted: list[str] = []
    now = datetime(2025, 1, 4, 12, 0, 2, tzinfo=UTC)

    assert ScheduleService(store, submitted.append).tick(now) == []
    assert ScheduleService(store, submitted.append).tick(now) == []
    saved = ScheduleStore(store).get(schedule["id"])
    assert saved["executions"][0]["state"] == "missed"
    assert saved["recurrence"]["next_at"] == "2025-01-05T12:00"
    with store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM schedule_executions").fetchone()[0] == 1


def test_schedule_api_auth_model_selection_workspace_and_pause(workspace_tmp_path: Path) -> None:
    store = RunStore(workspace_tmp_path / "schedule-api.sqlite3")
    app = create_app(ScheduleProvider(), SecretStr(TOKEN), store=store)
    with store.connection() as connection:
        connection.execute(
            "UPDATE provider_profiles SET selected_model='fixture/model' WHERE id='lm-studio'"
        )
    with TestClient(app) as client:
        assert client.get("/schedules").status_code == 401
        payload = {
            "objective": "Check project status",
            "acceptance_criteria": [],
            "workspace": str(workspace_tmp_path),
            "model": "fixture/model",
            "start_at": "2030-01-02T12:00",
            "frequency": "weekly",
            "timezone": "UTC",
            "missed_run_policy": "run_once",
            "max_iterations": 5,
            "max_seconds": 120,
        }
        created = client.post("/schedules", headers=HEADERS, json=payload)
        assert created.status_code == 201, created.text
        record = created.json()
        assert record["recurrence"]["frequency"] == "weekly"
        assert record["recurrence"]["workspace"] == str(workspace_tmp_path.resolve())
        assert record["missed_run_policy"] == "run_once"
        paused = client.put(
            f"/schedules/{record['id']}/enabled", headers=HEADERS, json={"enabled": False}
        )
        assert paused.status_code == 200
        assert paused.json()["enabled"] is False
        assert client.get("/schedules", headers=HEADERS).json()[0]["id"] == record["id"]
        bad_workspace = {**payload, "workspace": str(workspace_tmp_path / "missing")}
        assert client.post("/schedules", headers=HEADERS, json=bad_workspace).status_code == 409
        bad_model = {**payload, "model": "other/model"}
        assert client.post("/schedules", headers=HEADERS, json=bad_model).status_code == 409

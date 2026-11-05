import json

import pytest
from xaa.storage.restore import restore_database
from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunStore, utc_now


def test_v1_tool_journal_migrates_without_changing_approval_references(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "legacy.sqlite3")
    run_id = store.create_run(
        objective="Legacy", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    with store.connection() as connection:
        connection.execute("DROP INDEX tool_calls_run_provider_idx")
        connection.execute("ALTER TABLE tool_calls DROP COLUMN provider_call_id")
        connection.execute("PRAGMA user_version=1")
        now = utc_now()
        connection.execute(
            "INSERT INTO tool_calls VALUES (?,NULL,?,?,?,?,?,'completed',?,NULL,?,?)",
            (
                "legacy-call",
                run_id,
                "read",
                "1",
                "{}",
                "legacy-action",
                json.dumps({"content": "legacy evidence"}),
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO approvals VALUES (?,?,?,'workspace.read','Legacy action',NULL,NULL,NULL,?)",
            ("approval", run_id, "legacy-call", now),
        )
        store.record_event(
            connection, run_id, "tool.completed", {"call_id": "legacy-call", "internal": False}
        )
    snapshot = store.backup()
    destination = workspace_tmp_path / "restored" / "runs.sqlite3"
    restore_database(snapshot, destination)
    for database in (store.path, destination):
        migrated = RunStore(database)
        assert migrated.conversation_messages(run_id) == [
            {
                "role": "tool",
                "tool_call_id": "legacy-call",
                "content": '{"content": "legacy evidence"}',
            }
        ]
        with migrated.connection() as connection:
            assert (
                connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
            )
            row = connection.execute("SELECT id,provider_call_id FROM tool_calls").fetchone()
            assert tuple(row) == ("legacy-call", "legacy-call")
            assert (
                connection.execute("SELECT tool_call_id FROM approvals").fetchone()[0]
                == "legacy-call"
            )
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        second = migrated.create_run(
            objective="New run", acceptance_criteria=[], workspace=None, mode="observe", model=None
        )["id"]
        token = migrated.acquire_lease(second, "worker")
        assert migrated.record_tool_intent(
            second,
            token,
            call_id="legacy-call",
            tool="read",
            version="1",
            arguments={},
            idempotency_key="new-action",
        )
        assert migrated.unresolved_tool_calls(second)[0]["id"] == "legacy-call"


def test_newer_database_is_refused_without_resetting_version(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "future.sqlite3")
    with store.connection() as connection:
        connection.execute(f"PRAGMA user_version={DATABASE_SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer"):
        RunStore(store.path)
    with store.connection() as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION + 1
        )


def test_schema_seven_migrates_additive_telegram_tables_without_losing_runs(
    workspace_tmp_path,
):
    database = workspace_tmp_path / "schema-seven.sqlite3"
    store = RunStore(database)
    run_id = store.create_run(
        objective="Preserve this run",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )["id"]
    events = store.list_events(run_id)
    with store.connection() as connection:
        connection.execute("DROP TABLE telegram_offsets")
        connection.execute("DROP TABLE telegram_updates")
        connection.execute("DROP TABLE telegram_bots")
        connection.execute("DROP TABLE telegram_pairing_codes")
        connection.execute("PRAGMA user_version=7")

    migrated = RunStore(database)
    assert migrated.list_events(run_id) == events
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "telegram_pairing_codes",
        "telegram_bots",
        "telegram_updates",
        "telegram_offsets",
    }.issubset(tables)


@pytest.mark.parametrize("old_version", [4, 5, 6])
def test_prior_records_are_preserved_when_enabling_private_workspace_semantics(
    workspace_tmp_path,
    old_version,
):
    from xaa.runtime.task_graph import TaskDefinition
    from xaa.storage.tasks import TaskStore

    store = RunStore(workspace_tmp_path / "schema-four.sqlite3")
    run_id = store.create_run(
        objective="Existing read-only plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )["id"]
    owner = store.acquire_lease(run_id, "fixture")
    TaskStore(store).save_plan(
        run_id, owner, [TaskDefinition(id="reader", objective="Read existing evidence")]
    )
    store.release_lease(run_id, owner)
    before = store.get_run(run_id)
    events = store.list_events(run_id)
    tasks = TaskStore(store).records(run_id)
    with store.connection() as c:
        c.execute(f"PRAGMA user_version={old_version}")
    reopened = RunStore(store.path)
    assert reopened.get_run(run_id) == before
    assert reopened.list_events(run_id) == events
    assert TaskStore(reopened).records(run_id) == tasks
    with reopened.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


@pytest.mark.parametrize("old_limit", [4, 5, 6])
def test_older_reader_limit_refuses_current_schema_before_access(
    workspace_tmp_path, monkeypatch, old_limit
):
    from xaa.storage import runs as module

    store = RunStore(workspace_tmp_path / "private-workspaces.sqlite3")
    with store.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
    monkeypatch.setattr(module, "DATABASE_SCHEMA_VERSION", old_limit)
    with pytest.raises(RuntimeError, match="newer"):
        RunStore(store.path)
    with store.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION

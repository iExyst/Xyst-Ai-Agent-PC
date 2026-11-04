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

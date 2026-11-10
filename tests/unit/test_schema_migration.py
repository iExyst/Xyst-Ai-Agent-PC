import json

import pytest
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.restore import restore_database
from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunStore, utc_now
from xaa.storage.tasks import TaskStore


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


def test_schema_twenty_five_adds_specialist_input_and_output_contracts(workspace_tmp_path):
    database = workspace_tmp_path / "schema-twenty-five.sqlite3"
    store = RunStore(database)
    run_id = store.create_run(
        objective="Preserve prior tasks",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "planner")
    TaskStore(store).save_plan(run_id, token, [TaskDefinition(id="old", objective="Old task")])
    with store.connection() as connection:
        connection.execute("ALTER TABLE tasks DROP COLUMN input_artifacts")
        connection.execute("ALTER TABLE tasks DROP COLUMN expected_outputs")
        connection.execute("PRAGMA user_version=25")

    migrated = RunStore(database)
    record = TaskStore(migrated).records(run_id)[0]
    assert record["input_artifacts"] == []
    assert record["expected_outputs"] == []
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


def test_schema_thirteen_adds_durable_cli_chat_session_configuration(workspace_tmp_path):
    database = workspace_tmp_path / "schema-thirteen.sqlite3"
    store = RunStore(database)
    with store.connection() as connection:
        connection.execute("DROP TABLE cli_chat_config")
        connection.execute("PRAGMA user_version=13")

    migrated = RunStore(database)
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='cli_chat_config'"
            ).fetchone()[0]
            == "cli_chat_config"
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


def test_schema_eight_adds_schedule_delivery_outbox_without_losing_history(workspace_tmp_path):
    database = workspace_tmp_path / "schema-eight.sqlite3"
    store = RunStore(database)
    run = store.create_run(
        objective="Keep scheduled result",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture/model",
    )
    now = utc_now()
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO schedules VALUES ('schedule-1','Preserve schedule','{}','UTC','skip','skip',NULL,NULL,0,?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO schedule_executions VALUES ('execution-1','schedule-1','occurrence-1',?,'completed','sent',?,?)",
            (run["id"], now, now),
        )
        connection.execute("DROP TABLE schedule_deliveries")
        connection.execute("PRAGMA user_version=8")

    migrated = RunStore(database)
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert (
            connection.execute("SELECT objective FROM schedules").fetchone()[0]
            == "Preserve schedule"
        )
        assert (
            connection.execute("SELECT delivery_evidence FROM schedule_executions").fetchone()[0]
            == "sent"
        )
        assert connection.execute("SELECT count(*) FROM schedule_deliveries").fetchone()[0] == 0
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema_nine_adds_bounded_delivery_retry_fields_without_losing_outbox(
    workspace_tmp_path,
):
    database = workspace_tmp_path / "schema-nine.sqlite3"
    store = RunStore(database)
    now = utc_now()
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO schedules VALUES ('schedule-1','Preserve schedule','{}','UTC','skip','skip',NULL,'telegram:42:123',0,?,?)",
            (now, now),
        )
        connection.execute(
            "INSERT INTO schedule_executions VALUES ('execution-1','schedule-1','occurrence-1',NULL,'completed','pending',?,?)",
            (now, now),
        )
        connection.execute("ALTER TABLE schedule_deliveries RENAME TO schedule_deliveries_v10")
        connection.execute(
            "CREATE TABLE schedule_deliveries (execution_id TEXT PRIMARY KEY REFERENCES schedule_executions(id) ON DELETE CASCADE, account_id TEXT NOT NULL, external_identity TEXT NOT NULL, chat_id TEXT NOT NULL, state TEXT NOT NULL, message_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schedule_deliveries VALUES ('execution-1','42','123','123','pending',NULL,?,?)",
            (now, now),
        )
        connection.execute("DROP TABLE schedule_deliveries_v10")
        connection.execute("PRAGMA user_version=9")

    migrated = RunStore(database)
    with migrated.connection() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(schedule_deliveries)")
        }
        delivery = connection.execute("SELECT * FROM schedule_deliveries").fetchone()
        indexes = {
            row["name"] for row in connection.execute("PRAGMA index_list(schedule_deliveries)")
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert {"attempt_count", "next_attempt_at"}.issubset(columns)
        assert "schedule_deliveries_retry" in indexes
        assert delivery["state"] == "pending"
        assert delivery["attempt_count"] == 0
        assert delivery["next_attempt_at"] is None
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


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


def test_schema_eleven_adds_durable_specialist_profiles(workspace_tmp_path):
    from xaa.storage.specialist_profiles import SpecialistProfileStore

    store = RunStore(workspace_tmp_path / "specialist-profile-migration.sqlite3")
    with store.connection() as connection:
        connection.execute("DROP TABLE specialist_profiles")
        connection.execute("PRAGMA user_version=11")

    migrated = RunStore(store.path)
    roles = SpecialistProfileStore(migrated).list()
    assert {row["role"] for row in roles} == {
        "planner",
        "researcher",
        "developer",
        "reviewer",
        "tester",
        "executor",
    }
    assert all(row["model_route"] is None for row in roles)
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_schema_ten_adds_mcp_configuration_tables_without_losing_run_history(workspace_tmp_path):
    database = workspace_tmp_path / "schema-ten-mcp.sqlite3"
    store = RunStore(database)
    run_id = store.create_run(
        objective="Preserve MCP-era history",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )["id"]
    events = store.list_events(run_id)
    with store.connection() as connection:
        connection.execute("DROP TABLE mcp_server_tools")
        connection.execute("DROP TABLE mcp_servers")
        connection.execute("PRAGMA user_version=10")

    migrated = RunStore(database)
    assert migrated.list_events(run_id) == events
    with migrated.connection() as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"mcp_servers", "mcp_server_tools"}.issubset(tables)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_schema_fourteen_adds_streamable_http_mcp_fields(workspace_tmp_path):
    from xaa.storage.mcp import MCPRegistry, MCPServerSpec

    database = workspace_tmp_path / "schema-fourteen-mcp-transport.sqlite3"
    store = RunStore(database)
    server = MCPRegistry(store).create_server(MCPServerSpec(name="Legacy", command="python"))
    with store.connection() as connection:
        connection.execute("ALTER TABLE mcp_servers DROP COLUMN endpoint")
        connection.execute("ALTER TABLE mcp_servers DROP COLUMN transport")
        connection.execute("PRAGMA user_version=14")

    migrated = RunStore(database)
    restored = MCPRegistry(migrated).get_server(server.id)
    assert restored is not None
    assert restored.transport == "stdio"
    assert restored.endpoint is None
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_schema_twenty_three_adds_agent_plugin_mcp_paths(workspace_tmp_path):
    from xaa.storage.mcp import MCPRegistry, MCPServerSpec

    database = workspace_tmp_path / "schema-twenty-three-plugin-mcp.sqlite3"
    store = RunStore(database)
    registry = MCPRegistry(store)
    original = registry.create_server(MCPServerSpec(name="Legacy Plugin", command="python"))
    with store.connection() as connection:
        connection.execute("ALTER TABLE mcp_servers DROP COLUMN plugin_root")
        connection.execute("ALTER TABLE mcp_servers DROP COLUMN plugin_data")
        connection.execute("PRAGMA user_version=22")

    migrated = RunStore(database)
    restored = MCPRegistry(migrated).get_server(original.id)
    assert restored is not None
    assert restored.plugin_root is None
    assert restored.plugin_data is None
    with migrated.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(mcp_servers)")}
        assert {"plugin_root", "plugin_data"}.issubset(columns)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


def test_schema_twenty_four_adds_agent_plugin_http_header_mapping(workspace_tmp_path):
    from xaa.storage.mcp import MCPRegistry, MCPServerSpec

    database = workspace_tmp_path / "schema-twenty-four-plugin-http.sqlite3"
    store = RunStore(database)
    registry = MCPRegistry(store)
    original = registry.create_server(
        MCPServerSpec(
            name="Legacy HTTP", transport="streamable_http", endpoint="https://example.test/mcp"
        )
    )
    with store.connection() as connection:
        connection.execute("ALTER TABLE mcp_servers DROP COLUMN plugin_headers")
        connection.execute("PRAGMA user_version=23")

    migrated = RunStore(database)
    restored = MCPRegistry(migrated).get_server(original.id)
    assert restored is not None
    assert restored.plugin_headers == {}
    with migrated.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(mcp_servers)")}
        assert "plugin_headers" in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


def test_schema_twenty_five_adds_agent_plugin_source_fingerprint(workspace_tmp_path):
    from xaa.storage.mcp import MCPRegistry, MCPServerSpec

    database = workspace_tmp_path / "schema-twenty-five-plugin-source.sqlite3"
    store = RunStore(database)
    registry = MCPRegistry(store)
    root = workspace_tmp_path / "legacy-plugin"
    root.mkdir()
    server = registry.create_server(
        MCPServerSpec(
            name="Legacy package server",
            command="python",
            plugin_root=str(root),
            plugin_data=str(workspace_tmp_path / "plugin-data"),
            plugin_sha256="a" * 64,
        )
    )
    with store.connection() as connection:
        connection.execute("ALTER TABLE mcp_servers DROP COLUMN plugin_sha256")
        connection.execute("PRAGMA user_version=24")

    migrated = RunStore(database)
    restored = MCPRegistry(migrated).get_server(server.id)
    assert restored is not None
    assert restored.plugin_sha256 is None
    with migrated.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(mcp_servers)")}
        assert "plugin_sha256" in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


def test_schema_twelve_adds_snapshotted_specialist_instructions(workspace_tmp_path):
    from xaa.storage.specialist_profiles import SpecialistProfileStore

    store = RunStore(workspace_tmp_path / "specialist-instructions-migration.sqlite3")
    SpecialistProfileStore(store).set_route("researcher", "saved/model")
    with store.connection() as connection:
        connection.execute("ALTER TABLE specialist_profiles DROP COLUMN system_instructions")
        connection.execute("ALTER TABLE tasks DROP COLUMN identity_instructions")
        connection.execute("PRAGMA user_version=12")

    migrated = RunStore(store.path)
    researcher = next(
        row for row in SpecialistProfileStore(migrated).list() if row["role"] == "researcher"
    )
    assert researcher["model_route"] == "saved/model"
    assert "evidence" in researcher["system_instructions"]
    with migrated.connection() as connection:
        assert "identity_instructions" in {
            row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


def test_schema_seventeen_adds_durable_specialist_identity_snapshots(workspace_tmp_path):
    from xaa.runtime.task_graph import TaskDefinition
    from xaa.storage.specialist_profiles import SpecialistProfileStore
    from xaa.storage.tasks import TaskStore

    store = RunStore(workspace_tmp_path / "specialist-identity-migration.sqlite3")
    profiles = SpecialistProfileStore(store)
    profiles.set_display_name("researcher", "Evidence Scout")
    run_id = store.create_run(
        objective="Research",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
    )["id"]
    owner = store.acquire_lease(run_id, "identity-migration")
    TaskStore(store).save_plan(
        run_id, owner, [TaskDefinition(id="research", objective="Research", role="researcher")]
    )
    with store.connection() as connection:
        connection.execute("ALTER TABLE tasks DROP COLUMN identity_name")
        connection.execute("PRAGMA user_version=16")

    migrated = RunStore(store.path)
    assert TaskStore(migrated).records(run_id)[0]["identity_name"] == "Evidence Scout"
    profiles = SpecialistProfileStore(migrated)
    profiles.set_display_name("researcher", "New Name")
    assert TaskStore(migrated).records(run_id)[0]["identity_name"] == "Evidence Scout"
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION


def test_schema_seventeen_adds_email_channel_deduplication_tables(workspace_tmp_path):
    database = workspace_tmp_path / "schema-seventeen-email.sqlite3"
    store = RunStore(database)
    with store.connection() as connection:
        connection.execute("DROP TABLE email_messages")
        connection.execute("DROP TABLE email_mailboxes")
        connection.execute("PRAGMA user_version=17")

    migrated = RunStore(database)
    with migrated.connection() as connection:
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"email_mailboxes", "email_messages"}.issubset(tables)
        assert {"uid_validity", "last_uid"}.issubset(
            {row["name"] for row in connection.execute("PRAGMA table_info(email_mailboxes)")}
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

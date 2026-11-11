import sqlite3

from xaa.storage.memory import MemoryStore
from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunStore


def test_explicit_preference_edit_delete_and_scope_search(workspace_tmp_path):
    runs = RunStore(workspace_tmp_path / "memory.sqlite3")
    memories = MemoryStore(runs)
    record = memories.save_preference("user-one", "Use concise Python explanations")
    assert record["provenance"] == "user"
    assert memories.search("user-one", "Python")[0]["id"] == record["id"]
    assert memories.search("user-two", "Python") == []
    memories.save_preference(
        "user-one", "Use detailed TypeScript explanations", memory_id=record["id"]
    )
    assert memories.search("user-one", "Python") == []
    assert MemoryStore(RunStore(runs.path)).search("user-one", "TypeScript")
    assert not memories.delete("user-two", record["id"])
    assert memories.delete("user-one", record["id"])
    assert memories.search("user-one", "TypeScript") == []


def test_sourced_knowledge_keeps_reference_across_edit_and_recall_and_cleans_index(
    workspace_tmp_path,
):
    runs = RunStore(workspace_tmp_path / "sourced-memory.sqlite3")
    memories = MemoryStore(runs)
    item = memories.save_sourced_knowledge(
        "local-user", "SQLite uses WAL for concurrent readers", "https://example.test/sqlite"
    )
    assert item["kind"] == "knowledge"
    assert item["provenance"] == "user_source"
    assert (
        memories.search("local-user", "concurrent")[0]["source_uri"]
        == "https://example.test/sqlite"
    )
    assert memories.recall_sourced_knowledge("local-user")["items"] == [
        {"id": item["id"], "content": item["content"], "source_uri": item["source_uri"]}
    ]
    assert memories.recall_sourced_knowledge("another-user")["items"] == []
    edited = memories.save_sourced_knowledge(
        "local-user",
        "SQLite readers can coexist with a WAL writer",
        "https://sqlite.org/wal.html",
        memory_id=item["id"],
    )
    assert edited["id"] == item["id"]
    assert edited["source_uri"] == "https://sqlite.org/wal.html"
    assert memories.search("local-user", "concurrent") == []
    assert memories.delete("local-user", item["id"])
    assert memories.search("local-user", "WAL") == []
    assert memories.recall_sourced_knowledge("local-user")["items"] == []


def test_sourced_knowledge_requires_source_reference(workspace_tmp_path):
    memories = MemoryStore(RunStore(workspace_tmp_path / "invalid-source.sqlite3"))
    for content, source in (("Claim", " "), (" ", "https://example.test")):
        try:
            memories.save_sourced_knowledge("local-user", content, source)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid sourced knowledge was accepted")


def test_run_recall_ranks_by_objective_terms_and_keeps_record_types_separate(
    workspace_tmp_path,
):
    memories = MemoryStore(RunStore(workspace_tmp_path / "query-recall.sqlite3"))
    python_preference = memories.save_preference("local-user", "Use Python for automation examples")
    memories.save_preference("local-user", "Prefer Rust for systems examples")
    sqlite_knowledge = memories.save_sourced_knowledge(
        "local-user", "SQLite WAL supports concurrent readers", "https://example.test/wal"
    )
    memories.save_sourced_knowledge(
        "local-user", "Python packaging uses project metadata", "https://example.test/python"
    )

    preference_result = memories.recall_preferences("local-user", query="Python automation")
    knowledge_result = memories.recall_sourced_knowledge("local-user", query="SQLite WAL")

    assert [item["id"] for item in preference_result["items"]] == [python_preference["id"]]
    assert [item["id"] for item in knowledge_result["items"]] == [sqlite_knowledge["id"]]
    assert knowledge_result["items"][0]["source_uri"] == "https://example.test/wal"
    assert memories.recall_preferences("local-user", query="---")["items"] == []


def test_schema_18_memory_rows_migrate_without_losing_data(workspace_tmp_path):
    database = workspace_tmp_path / "schema-18-memory.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE memories (id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope TEXT NOT NULL, content TEXT NOT NULL, provenance TEXT NOT NULL, confidence REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1)"
        )
        connection.execute(
            "INSERT INTO memories VALUES ('legacy', 'preference', 'local-user', 'Keep answers concise', 'user', 1.0, '2026-11-01T00:00:00Z', '2026-11-01T00:00:00Z', 1)"
        )
        connection.execute("PRAGMA user_version=18")
        connection.commit()
    finally:
        connection.close()
    runs = RunStore(database)
    with runs.connection() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(memories)")}
        record = connection.execute("SELECT * FROM memories WHERE id='legacy'").fetchone()
        schema = connection.execute("PRAGMA user_version").fetchone()[0]
        idempotency_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='idempotent_run_requests'"
        ).fetchone()
    assert "source_uri" in columns
    assert record["content"] == "Keep answers concise"
    assert record["source_uri"] is None
    assert schema == DATABASE_SCHEMA_VERSION
    assert idempotency_table is not None

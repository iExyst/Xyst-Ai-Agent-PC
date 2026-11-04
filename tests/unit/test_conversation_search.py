import json

import pytest
from xaa.storage.history import list_runs
from xaa.storage.restore import restore_database
from xaa.storage.runs import DATABASE_SCHEMA_VERSION, RunStore


def create(store, objective="Ordinary objective"):
    return store.create_run(
        objective=objective, acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]


def search(store, phrase, **options):
    return list_runs(store, query=phrase, scope="conversation", **options)


def raw_message(store, run, content, metadata="PRIVATE_HIDDEN_MARKER", sequence=2):
    payload = json.dumps(
        {
            "message": {
                "role": "assistant",
                "content": content,
                "reasoning_content": metadata,
                "tool_calls": [{"function": {"arguments": "TOOL_HIDDEN_MARKER"}}],
            }
        }
    )
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO run_events VALUES (?,?, 'model.message', ?, '2026-11-01T00:00:00Z')",
            (run, sequence, payload),
        )
    return payload


def test_legacy_backfill_public_text_only_and_no_row_rewrite(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "legacy.sqlite3")
    run = create(store)
    with store.connection() as connection:
        for name in [
            "conversation_run_insert",
            "conversation_run_update",
            "conversation_run_delete",
            "conversation_event_insert",
            "conversation_event_update",
            "conversation_event_delete",
        ]:
            connection.execute(f"DROP TRIGGER {name}")
        connection.execute("DROP TABLE conversation_fts")
        connection.execute("PRAGMA user_version=2")
    original = raw_message(store, run, "Café Orchid repair")
    store = RunStore(store.path)
    assert search(store, "CAFE orchid")["items"][0]["id"] == run
    for phrase in [
        "PRIVATE_HIDDEN_MARKER",
        "TOOL_HIDDEN_MARKER",
        "repair OR everything",
        '" OR 1=1 --',
        "_%_",
    ]:
        assert search(store, phrase)["items"] == []
    with store.connection() as connection:
        assert (
            connection.execute("SELECT payload FROM run_events WHERE sequence=2").fetchone()[0]
            == original
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DATABASE_SCHEMA_VERSION
    assert len(search(RunStore(store.path), "orchid")["items"]) == 1
    restored = workspace_tmp_path / "restored" / "runs.sqlite3"
    restore_database(store.backup(), restored)
    assert search(RunStore(restored), "orchid")["items"][0]["id"] == run


def test_live_updates_deletes_and_cascade_remove_fts_entries(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "live.sqlite3")
    run = create(store, "Objective rhubarb")
    raw_message(store, run, "Answer marigold")
    assert search(store, "marigold")["items"]
    with store.connection() as connection:
        connection.execute(
            "UPDATE run_events SET payload=? WHERE run_id=? AND sequence=2",
            (json.dumps({"message": {"content": "Answer violet"}}), run),
        )
    assert search(store, "marigold")["items"] == []
    assert search(store, "violet")["items"]
    with store.connection() as connection:
        connection.execute("DELETE FROM run_events WHERE run_id=? AND sequence=2", (run,))
    assert search(store, "violet")["items"] == []
    with store.connection() as connection:
        connection.execute("DELETE FROM runs WHERE id=?", (run,))
        assert (
            connection.execute(
                "SELECT count(*) FROM conversation_fts WHERE run_id=?", (run,)
            ).fetchone()[0]
            == 0
        )


def test_conversation_keyset_deduplicates_multiple_matching_answers(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "pages.sqlite3")
    runs = [create(store) for _ in range(3)]
    for run in runs:
        raw_message(store, run, "Shared jasmine", sequence=2)
        raw_message(store, run, "Another jasmine", sequence=3)
    first = search(store, "jasmine", limit=1)
    results = [first["items"][0]["id"]]
    cursor = first["next_cursor"]
    while cursor:
        page = search(store, "jasmine", limit=1, before=cursor)
        results.extend(row["id"] for row in page["items"])
        cursor = page["next_cursor"]
    assert len(results) == 3 and set(results) == set(runs)
    with pytest.raises(ValueError):
        search(store, "bad\x00phrase")

"""FTS5 index of run objectives and public assistant text; no tool/private metadata."""

import sqlite3


def install_index(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5(
            run_id UNINDEXED, source_key UNINDEXED, content,
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER IF NOT EXISTS conversation_run_insert AFTER INSERT ON runs BEGIN
            INSERT INTO conversation_fts VALUES(new.id,'objective',substr(new.objective,1,65536));
        END;
        CREATE TRIGGER IF NOT EXISTS conversation_run_update AFTER UPDATE OF objective ON runs BEGIN
            DELETE FROM conversation_fts WHERE run_id=old.id AND source_key='objective';
            INSERT INTO conversation_fts VALUES(new.id,'objective',substr(new.objective,1,65536));
        END;
        CREATE TRIGGER IF NOT EXISTS conversation_run_delete AFTER DELETE ON runs BEGIN
            DELETE FROM conversation_fts WHERE run_id=old.id;
        END;
        CREATE TRIGGER IF NOT EXISTS conversation_event_insert AFTER INSERT ON run_events
        WHEN new.event_type IN ('model.message','task.model.message')
             AND json_type(new.payload,'$.message.content')='text' BEGIN
            INSERT INTO conversation_fts VALUES(new.run_id,'event:'||new.sequence,
                substr(json_extract(new.payload,'$.message.content'),1,65536));
        END;
        CREATE TRIGGER IF NOT EXISTS conversation_event_update AFTER UPDATE ON run_events BEGIN
            DELETE FROM conversation_fts WHERE run_id=old.run_id AND source_key='event:'||old.sequence;
            INSERT INTO conversation_fts SELECT new.run_id,'event:'||new.sequence,
                substr(json_extract(new.payload,'$.message.content'),1,65536)
                WHERE new.event_type IN ('model.message','task.model.message')
                AND json_type(new.payload,'$.message.content')='text';
        END;
        CREATE TRIGGER IF NOT EXISTS conversation_event_delete AFTER DELETE ON run_events BEGIN
            DELETE FROM conversation_fts WHERE run_id=old.run_id AND source_key='event:'||old.sequence;
        END;
        """
    )
    # Backfill is transactional and repeatable if an interrupted migration is retried.
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("DELETE FROM conversation_fts")
    connection.execute(
        "INSERT INTO conversation_fts SELECT id,'objective',substr(objective,1,65536) FROM runs"
    )
    connection.execute(
        "INSERT INTO conversation_fts SELECT run_id,'event:'||sequence,"
        "substr(json_extract(payload,'$.message.content'),1,65536) FROM run_events "
        "WHERE event_type IN ('model.message','task.model.message') "
        "AND json_type(payload,'$.message.content')='text'"
    )

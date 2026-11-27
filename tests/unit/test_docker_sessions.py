from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from xaa.runtime.docker_execution_policy import (
    DOCKER_CONTAINER_MEMORY_BYTES,
    DOCKER_CONTAINER_NANO_CPUS,
    MAX_REUSABLE_DOCKER_SESSIONS,
)
from xaa.runtime.states import RunState
from xaa.storage.docker_sessions import DockerSessionJournal
from xaa.storage.runs import LeaseLost, RunStore

IMAGE = f"registry.example/xaa@sha256:{'b' * 64}"
PROFILE = "a" * 64


def setup_run(root, *, docker_session_reuse=False):
    project = root / "project"
    project.mkdir()
    store = RunStore(root / "data" / "runs.sqlite3")
    run = store.create_run(
        objective="Use a reusable Docker session",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=None,
        command_grant={
            "backend": "docker-local-v1",
            "distribution": "Docker-local",
            "profile_sha256": PROFILE,
            "image_reference": IMAGE,
            "docker_session_reuse": docker_session_reuse,
        },
    )
    owner = store.acquire_lease(run["id"], "test", seconds=3600)
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    return store, run["id"], owner


def add_running_run(store, workspace):
    run = store.create_run(
        objective="Use a reusable Docker session",
        acceptance_criteria=[],
        workspace=str(workspace),
        mode="workspace",
        model=None,
        command_grant={
            "backend": "docker-local-v1",
            "distribution": "Docker-local",
            "profile_sha256": PROFILE,
            "image_reference": IMAGE,
            "docker_session_reuse": True,
        },
    )
    owner = store.acquire_lease(run["id"], f"test-{uuid4().hex}", seconds=3600)
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    return run["id"], owner


def test_command_reservation_persists_the_generated_session_identity(workspace_tmp_path):
    from xaa.storage.commands import CommandJournal

    store, run_id, owner = setup_run(workspace_tmp_path, docker_session_reuse=True)
    journal = CommandJournal(store)
    intent = journal.reserve(
        run_id,
        owner,
        "session-command",
        {"argv": ["python3", "--version"]},
        idempotency_key="session-command",
    )
    assert intent.docker_session_reuse is True
    assert intent.docker_session_id is not None
    saved = DockerSessionJournal(store).saved_intent(intent.docker_session_id)
    assert saved == intent


def test_session_lifecycle_persists_and_reuses_identity(workspace_tmp_path):
    store, run_id, owner = setup_run(workspace_tmp_path)
    journal = DockerSessionJournal(store)

    reserved = journal.reserve(
        run_id, owner, task_id=None, profile_sha256=PROFILE, image_reference=IMAGE
    )
    assert reserved.state == "reserved"
    creating = journal.transition(reserved.id, owner, expected={"reserved"}, destination="creating")
    ready = journal.transition(
        creating.id,
        owner,
        expected={"creating"},
        destination="ready",
        container_id="c" * 64,
    )
    reused = journal.reserve(
        run_id, owner, task_id=None, profile_sha256=PROFILE, image_reference=IMAGE
    )
    assert reused.id == reserved.id
    assert reused.container_id == "c" * 64

    busy = journal.transition(
        ready.id,
        owner,
        expected={"ready"},
        destination="busy",
        execution_id="d" * 32,
    )
    assert busy.active_execution_id == "d" * 32
    with pytest.raises(ValueError, match="does not match"):
        journal.transition(
            busy.id,
            owner,
            expected={"busy"},
            destination="ready",
            execution_id="e" * 32,
        )
    ready_again = journal.transition(
        busy.id,
        owner,
        expected={"busy"},
        destination="ready",
        execution_id="d" * 32,
    )
    assert ready_again.state == "ready"
    assert ready_again.active_execution_id is None


def test_session_profile_cannot_change_and_unknown_requires_recovery(workspace_tmp_path):
    store, run_id, owner = setup_run(workspace_tmp_path)
    journal = DockerSessionJournal(store)
    session = journal.reserve(
        run_id, owner, task_id=None, profile_sha256=PROFILE, image_reference=IMAGE
    )
    with pytest.raises(PermissionError, match="profile changed"):
        journal.reserve(
            run_id,
            owner,
            task_id=None,
            profile_sha256="f" * 64,
            image_reference=IMAGE,
        )

    unknown = journal.transition(
        session.id,
        owner,
        expected={"reserved"},
        destination="unknown",
    )
    assert unknown.state == "unknown"
    with pytest.raises(RuntimeError, match="needs recovery"):
        journal.reserve(run_id, owner, task_id=None, profile_sha256=PROFILE, image_reference=IMAGE)
    stopping = journal.transition(
        unknown.id,
        owner,
        expected={"unknown"},
        destination="stopping",
    )
    closed = journal.transition(
        stopping.id,
        owner,
        expected={"stopping"},
        destination="closed",
    )
    assert closed.container_id is None
    assert journal.get(closed.id) == closed


def test_session_creation_requires_live_run_lease_and_container_identity(workspace_tmp_path):
    store, run_id, owner = setup_run(workspace_tmp_path)
    journal = DockerSessionJournal(store)
    session = journal.reserve(
        run_id, owner, task_id=None, profile_sha256=PROFILE, image_reference=IMAGE
    )
    with pytest.raises(LeaseLost):
        journal.transition(
            session.id,
            "wrong-owner",
            expected={"reserved"},
            destination="creating",
        )
    creating = journal.transition(session.id, owner, expected={"reserved"}, destination="creating")
    ready = journal.transition(
        creating.id,
        owner,
        expected={"creating"},
        destination="ready",
        container_id="c" * 64,
    )
    with pytest.raises(PermissionError, match="cannot be replaced"):
        journal.transition(
            ready.id,
            owner,
            expected={"ready"},
            destination="stopping",
            container_id="f" * 64,
        )


def test_profile_session_resource_budget_counts_pending_cleanup_until_confirmed(
    workspace_tmp_path,
):
    store, first_run_id, first_owner = setup_run(workspace_tmp_path, docker_session_reuse=True)
    project = workspace_tmp_path / "project"
    owned_runs = [(first_run_id, first_owner)] + [
        add_running_run(store, project) for _ in range(MAX_REUSABLE_DOCKER_SESSIONS)
    ]
    journal = DockerSessionJournal(store)
    sessions = [
        journal.reserve(
            run_id,
            owner,
            task_id=None,
            profile_sha256=PROFILE,
            image_reference=IMAGE,
        )
        for run_id, owner in owned_runs[:MAX_REUSABLE_DOCKER_SESSIONS]
    ]
    assert len(sessions) == MAX_REUSABLE_DOCKER_SESSIONS
    assert MAX_REUSABLE_DOCKER_SESSIONS * DOCKER_CONTAINER_MEMORY_BYTES == 512 * 1024 * 1024
    assert MAX_REUSABLE_DOCKER_SESSIONS * DOCKER_CONTAINER_NANO_CPUS == 2_000_000_000

    overflow_run_id, overflow_owner = owned_runs[MAX_REUSABLE_DOCKER_SESSIONS]
    with pytest.raises(RuntimeError, match="resource budget is full"):
        journal.reserve(
            overflow_run_id,
            overflow_owner,
            task_id=None,
            profile_sha256=PROFILE,
            image_reference=IMAGE,
        )

    stopping = journal.transition(
        sessions[0].id,
        first_owner,
        expected={"reserved"},
        destination="unknown",
    )
    assert stopping.state == "unknown"
    journal.finish_cleanup(
        stopping.id,
        cleanup_confirmed=False,
        owner=first_owner,
    )
    with pytest.raises(RuntimeError, match="resource budget is full"):
        journal.reserve(
            overflow_run_id,
            overflow_owner,
            task_id=None,
            profile_sha256=PROFILE,
            image_reference=IMAGE,
        )

    journal.finish_cleanup(stopping.id, cleanup_confirmed=True, owner=first_owner)
    replacement = journal.reserve(
        overflow_run_id,
        overflow_owner,
        task_id=None,
        profile_sha256=PROFILE,
        image_reference=IMAGE,
    )
    assert replacement.state == "reserved"


def test_concurrent_profile_reservations_cannot_exceed_session_resource_budget(
    workspace_tmp_path,
):
    store, first_run_id, first_owner = setup_run(workspace_tmp_path, docker_session_reuse=True)
    project = workspace_tmp_path / "project"
    owned_runs = [(first_run_id, first_owner)] + [
        add_running_run(store, project) for _ in range(MAX_REUSABLE_DOCKER_SESSIONS * 2 - 1)
    ]
    journal = DockerSessionJournal(store)

    def reserve(item):
        run_id, owner = item
        try:
            return journal.reserve(
                run_id,
                owner,
                task_id=None,
                profile_sha256=PROFILE,
                image_reference=IMAGE,
            )
        except RuntimeError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=len(owned_runs)) as executor:
        results = list(executor.map(reserve, owned_runs))
    accepted = [item for item in results if not isinstance(item, RuntimeError)]
    refused = [item for item in results if isinstance(item, RuntimeError)]
    assert len(accepted) == MAX_REUSABLE_DOCKER_SESSIONS
    assert len(refused) == len(owned_runs) - MAX_REUSABLE_DOCKER_SESSIONS
    assert all("resource budget is full" in str(item) for item in refused)
    with store.connection() as connection:
        active = connection.execute(
            "SELECT COUNT(*) FROM docker_sessions WHERE state!='closed'"
        ).fetchone()[0]
    assert active == MAX_REUSABLE_DOCKER_SESSIONS


def test_stopping_a_run_durably_requests_session_cleanup(workspace_tmp_path):
    store, run_id, owner = setup_run(workspace_tmp_path)
    journal = DockerSessionJournal(store)
    session = journal.reserve(
        run_id, owner, task_id=None, profile_sha256=PROFILE, image_reference=IMAGE
    )
    store.transition(run_id, RunState.CANCELLED, owner=owner)
    requested = journal.get(session.id)
    assert requested is not None and requested.state == "stopping"
    assert [item.id for item in journal.terminal()] == [session.id]


def test_schema_42_database_migrates_docker_journal_and_a2a_peer_storage(workspace_tmp_path):
    store, _run_id, _owner = setup_run(workspace_tmp_path)
    with store.connection() as connection:
        connection.execute("DROP INDEX docker_sessions_state_updated")
        connection.execute("DROP TABLE docker_sessions")
        connection.execute("PRAGMA user_version=42")

    migrated = RunStore(store.path)
    with migrated.connection() as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(docker_sessions)")}
        listener_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='a2a_listener_settings'"
        ).fetchone()
        listener_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(a2a_listener_settings)")
        }
        outbound_peers = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='a2a_outbound_peers'"
        ).fetchone()
        outbound_tasks = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='a2a_outbound_tasks'"
        ).fetchone()
    assert version == 47
    assert {
        "id",
        "run_id",
        "task_scope",
        "profile_sha256",
        "image_reference",
        "state",
        "container_id",
        "active_execution_id",
    } <= columns
    assert listener_table is not None
    assert {"tls_cert_path", "tls_key_path"} <= listener_columns
    assert outbound_peers is not None
    assert outbound_tasks is not None
    with migrated.connection() as connection:
        outbound_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(a2a_outbound_tasks)")
        }
    assert {"cancel_state", "cancel_error"} <= outbound_columns


def test_schema_46_outbound_tasks_gain_manual_cancel_journal(workspace_tmp_path):
    store, _run_id, _owner = setup_run(workspace_tmp_path)
    with store.connection() as connection:
        connection.execute("ALTER TABLE a2a_outbound_tasks DROP COLUMN cancel_error")
        connection.execute("ALTER TABLE a2a_outbound_tasks DROP COLUMN cancel_state")
        connection.execute("PRAGMA user_version=46")

    migrated = RunStore(store.path)
    with migrated.connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 47
        columns = {
            row["name"]: row for row in connection.execute("PRAGMA table_info(a2a_outbound_tasks)")
        }
        assert columns["cancel_state"]["dflt_value"] == "'not_requested'"
        assert columns["cancel_error"]["notnull"] == 0

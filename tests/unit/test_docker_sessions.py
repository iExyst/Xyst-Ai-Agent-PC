import pytest
from xaa.runtime.states import RunState
from xaa.storage.docker_sessions import DockerSessionJournal
from xaa.storage.runs import LeaseLost, RunStore

IMAGE = f"registry.example/xaa@sha256:{'b' * 64}"
PROFILE = "a" * 64


def setup_run(root):
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
        },
    )
    owner = store.acquire_lease(run["id"], "test", seconds=3600)
    store.transition(run["id"], RunState.PLANNING, owner=owner)
    store.transition(run["id"], RunState.RUNNING, owner=owner)
    return store, run["id"], owner


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

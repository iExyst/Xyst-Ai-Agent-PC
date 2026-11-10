import pytest
from xaa.runtime.task_commands import read_task_input_artifact
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore, TaskReadScope
from xaa.storage.tasks import TaskStore


def test_specialist_reads_only_assigned_text_inputs_and_must_reach_eof(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "task-input.sqlite3")
    artifact_root = store.path.parent / "artifacts"
    artifact_root.mkdir(exist_ok=True)
    (artifact_root / "brief.txt").write_text("source material", encoding="utf-8")
    run_id = store.create_run(
        objective="Use supplied evidence",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
    )["id"]
    parent = store.acquire_lease(run_id, "planner")
    artifact = store.register_artifact(run_id, parent, "brief.txt", provenance="user.upload")
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="reader",
                objective="Read the evidence",
                input_artifacts=[artifact["id"]],
                expected_outputs=["Evidence summary"],
            )
        ],
    )
    claim = tasks.claim_ready(run_id, parent)
    assert claim is not None
    scope = TaskReadScope(run_id, parent, "reader", claim["token"])

    with pytest.raises(ValueError, match="Read every assigned input"):
        tasks.record_outcome(
            run_id,
            parent,
            "reader",
            claim["token"],
            state="verifying",
            result={"candidate": "premature"},
        )

    first = read_task_input_artifact(store, scope, artifact["id"], offset=0, limit=6)
    tasks.record_read_worker_event(
        run_id,
        parent,
        "reader",
        claim["token"],
        "task.tool.result",
        {
            "task_id": "reader",
            "call_id": "page-one",
            "read_input_artifact": {
                "artifact_id": artifact["id"],
                "offset": first["offset"],
                "characters_read": first["characters_read"],
                "eof": first["eof"],
            },
            "result": first,
        },
    )
    second = read_task_input_artifact(store, scope, artifact["id"], offset=6, limit=16_384)
    tasks.record_read_worker_event(
        run_id,
        parent,
        "reader",
        claim["token"],
        "task.tool.result",
        {
            "task_id": "reader",
            "call_id": "page-two",
            "read_input_artifact": {
                "artifact_id": artifact["id"],
                "offset": second["offset"],
                "characters_read": second["characters_read"],
                "eof": second["eof"],
            },
            "result": second,
        },
    )
    assert first["content"] + second["content"] == "source material"
    assert second["eof"] is True
    with pytest.raises(PermissionError, match="not assigned"):
        read_task_input_artifact(store, scope, "unassigned", offset=0, limit=10)

    tasks.record_outcome(
        run_id,
        parent,
        "reader",
        claim["token"],
        state="verifying",
        result={"candidate": "Evidence summary"},
    )
    assert tasks.records(run_id)[0]["status"] == "verifying"

import json
import time

import pytest
from xaa.providers.lm_studio import LMStudioProvider, ModelCapabilities, ModelInfo
from xaa.runtime.specialists import SpecialistRunner
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


@pytest.mark.parametrize("vision", [True, False])
async def test_specialist_image_input_is_attached_only_for_vision_capable_model(
    workspace_tmp_path, vision
):
    store = RunStore(workspace_tmp_path / f"task-image-{vision}.sqlite3")
    run = store.create_run(
        objective="Inspect the supplied image",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="vision-fixture",
        image_bytes=b"\x89PNG\r\n\x1a\nfixture-image",
        image_media_type="image/png",
    )
    run_id = run["id"]
    parent = store.acquire_lease(run_id, "planner")
    image = store.list_artifacts(run_id)[0]
    tasks = TaskStore(store)
    tasks.save_plan(
        run_id,
        parent,
        [
            TaskDefinition(
                id="inspect",
                objective="Describe the image",
                input_artifacts=[image["id"]],
                expected_outputs=["A description grounded in visible details"],
            )
        ],
    )

    class VisionProvider(LMStudioProvider):
        calls = 0

        async def models(self):
            return [
                ModelInfo(
                    id="vision-fixture",
                    capabilities=ModelCapabilities(vision=vision),
                )
            ]

        async def chat(self, *, model, messages, tools=None, **kwargs):
            self.calls += 1
            assert model == "vision-fixture"
            assert isinstance(messages[1]["content"], list)
            content = messages[1]["content"]
            assert content[0]["type"] == "text"
            assert "Assigned image artifacts" in content[0]["text"]
            assert content[1]["type"] == "image_url"
            assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
            assert "task_read_input_artifact" not in {
                tool["function"]["name"] for tool in tools or []
            }
            return {
                "choices": [{"message": {"role": "assistant", "content": "A blue square."}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }

    provider = VisionProvider()
    runner = SpecialistRunner(store, provider)
    try:
        statuses = await runner.run_ready(run_id, parent, deadline=time.monotonic() + 30)
    finally:
        await provider.close()
    record = tasks.records(run_id)[0]
    assert record["status"] == ("verifying" if vision else "failed")
    assert provider.calls == (1 if vision else 0)
    assert statuses == [{"task_id": "inspect", "status": record["status"]}]
    if not vision:
        assert "vision_capability_missing" in json.loads(record["result"])["failure_codes"]


def test_specialist_image_input_rejects_exact_text_token_admission(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "task-image-exact.sqlite3")
    run = store.create_run(
        objective="Inspect image",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="vision-fixture",
        max_total_tokens=100,
        pre_inference_token_reservations=True,
        image_bytes=b"\x89PNG\r\n\x1a\nfixture-image",
        image_media_type="image/png",
    )
    parent = store.acquire_lease(run["id"], "planner")
    image = store.list_artifacts(run["id"])[0]
    with pytest.raises(ValueError, match="exact text-token admission"):
        TaskStore(store).validate_plan(
            run["id"],
            [
                TaskDefinition(
                    id="inspect",
                    objective="Describe image",
                    input_artifacts=[image["id"]],
                    expected_outputs=["Description"],
                )
            ],
        )
    assert parent is not None

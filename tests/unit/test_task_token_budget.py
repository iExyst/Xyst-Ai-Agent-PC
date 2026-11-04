import json
import time

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.specialists import SpecialistRunner
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore, TaskTokenBudgetExceeded


@pytest.mark.parametrize("parent_threshold", [None, 100])
@pytest.mark.parametrize("usage,expected", [(None, "blocked"), (3, "verifying"), (4, "blocked")])
async def test_specialist_own_allowance_blocks_before_read(
    workspace_tmp_path, usage, expected, parent_threshold
):
    store = RunStore(workspace_tmp_path / "task-tokens.sqlite3")
    identity = store.create_run(
        objective="Parent",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
        max_total_tokens=parent_threshold,
    )["id"]
    token = store.acquire_lease(identity, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        identity,
        token,
        [
            TaskDefinition(
                id="inspect",
                objective="Inspect",
                max_iterations=1,
                max_total_tokens=3,
                max_output_tokens=5,
            ),
            TaskDefinition(
                id="dependent", objective="Review", dependencies=["inspect"], max_iterations=1
            ),
        ],
    )

    class Provider(LMStudioProvider):
        async def chat(self, *, model, messages, tools=None, max_output_tokens=None):
            assert max_output_tokens == 5
            message = (
                {"role": "assistant", "content": "Candidate"}
                if usage == 3
                else {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "read",
                            "type": "function",
                            "function": {
                                "name": "workspace_read_file",
                                "arguments": json.dumps({"path": "private.txt"}),
                            },
                        }
                    ],
                }
            )
            response = {"choices": [{"message": message}]}
            if usage is not None:
                response["usage"] = {
                    "prompt_tokens": usage - 1,
                    "completion_tokens": 1,
                    "total_tokens": usage,
                }
            return response

    provider = Provider()
    try:
        await SpecialistRunner(store, provider).run_ready(
            identity, token, deadline=time.monotonic() + 10
        )
    finally:
        await provider.close()
    rows = {row["local_id"]: row for row in tasks.records(identity)}
    assert rows["inspect"]["status"] == expected
    assert rows["dependent"]["status"] == ("blocked" if expected == "blocked" else "queued")
    assert not any(e["event_type"] == "tool.intended" for e in store.list_events(identity))
    if expected == "blocked":
        assert tasks.readiness(identity) == ([], [])
        assert "TaskTokenBudgetExceeded" in json.loads(rows["inspect"]["result"])["causes"]
        with pytest.raises(TaskTokenBudgetExceeded):
            TaskStore(RunStore(store.path)).check_token_budget(
                identity, "inspect", before_inference=True
            )


def test_plan_cannot_increase_token_authority(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "authority.sqlite3")
    identity = store.create_run(
        objective="Plan",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_total_tokens=10,
        max_output_tokens=8,
    )["id"]
    token = store.acquire_lease(identity, "parent")
    tasks = TaskStore(store)
    with pytest.raises(PermissionError):
        tasks.save_plan(
            identity, token, [TaskDefinition(id="a", objective="A", max_output_tokens=9)]
        )
    with pytest.raises(ValueError, match="token allowances"):
        tasks.save_plan(
            identity,
            token,
            [
                TaskDefinition(id="a", objective="A", max_total_tokens=6),
                TaskDefinition(id="b", objective="B", max_total_tokens=5),
            ],
        )
    store.append_worker_event(
        identity,
        token,
        "diagnostic.inference.completed",
        {
            "inference_id": "used",
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        },
    )
    with pytest.raises(ValueError, match="token allowances"):
        tasks.save_plan(
            identity, token, [TaskDefinition(id="a", objective="A", max_total_tokens=8)]
        )
    assert tasks.records(identity) == []
    tasks.save_plan(
        identity,
        token,
        [TaskDefinition(id="a", objective="A", max_total_tokens=7, max_output_tokens=4)],
    )
    assert TaskStore(RunStore(store.path)).records(identity)[0]["budgets"]["max_total_tokens"] == 7


async def test_dispatch_with_only_blocked_tasks_blocks_parent(workspace_tmp_path):
    from xaa.runtime.coordinator import Coordinator
    from xaa.runtime.states import RunState

    store = RunStore(workspace_tmp_path / "parent-block.sqlite3")
    identity = store.create_run(
        objective="Inspect",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="fixture",
    )["id"]
    token = store.acquire_lease(identity, "parent")
    tasks = TaskStore(store)
    tasks.save_plan(
        identity,
        token,
        [
            TaskDefinition(id="inspect", objective="Inspect", max_total_tokens=1),
            TaskDefinition(id="review", objective="Review", dependencies=["inspect"]),
        ],
    )
    store.transition(identity, RunState.PLANNING, owner=token)
    store.transition(identity, RunState.RUNNING, owner=token)

    class Provider(LMStudioProvider):
        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None):
            return {
                "choices": [{"message": {"role": "assistant", "content": "Candidate"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
            }

    provider = Provider()
    try:
        await Coordinator(store, provider).continue_plan(identity, token)
    finally:
        await provider.close()
    assert store.get_run(identity)["state"] == "blocked"
    assert all(row["status"] == "blocked" for row in tasks.records(identity))
    assert any(
        e["payload"].get("reason") == "specialist_tasks_blocked"
        for e in store.list_events(identity)
    )

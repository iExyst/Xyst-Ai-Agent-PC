import json

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


@pytest.mark.parametrize(
    "continuation", ["answer", "empty", "next_tool", "forbidden_tool", "reused_call"]
)
async def test_empty_response_continues_saved_work_without_expanding_authority(
    workspace_tmp_path, continuation
):
    project = workspace_tmp_path / "project"
    project.mkdir()
    (project / "input.txt").write_text("fixture result")
    store = RunStore(workspace_tmp_path / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Read input and create output when needed",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model="fixture",
        max_iterations=6,
    )

    class Provider(LMStudioProvider):
        requests = 0

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None, **kwargs):
            self.requests += 1
            call = {
                "id": "read",
                "type": "function",
                "function": {
                    "name": "workspace_read_file",
                    "arguments": json.dumps({"path": "input.txt"}),
                },
            }
            if self.requests == 1:
                message = {"role": "assistant", "tool_calls": [call]}
            elif self.requests in (2, 4):
                message = {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "private fixture must not become a public answer",
                }
            else:
                assert tools and any(
                    item["function"]["name"] == "workspace_create_file" for item in tools
                )
                assert (
                    messages[-1]["role"] == "user" and "saved progress" in messages[-1]["content"]
                )
                assert "fixture result" in next(
                    item["content"] for item in messages if item["role"] == "tool"
                )
                message = {
                    "role": "assistant",
                    "content": "fixture result"
                    if continuation == "answer" or self.requests == 5
                    else "",
                }
                if self.requests == 3 and continuation in (
                    "next_tool",
                    "forbidden_tool",
                    "reused_call",
                ):
                    if continuation == "next_tool":
                        call = {
                            "id": "create",
                            "type": "function",
                            "function": {
                                "name": "workspace_create_file",
                                "arguments": json.dumps(
                                    {"path": "output.txt", "content": "saved once"}
                                ),
                            },
                        }
                    elif continuation == "forbidden_tool":
                        call["id"] = "forbidden"
                        call["function"] = {
                            "name": "workspace_run_command",
                            "arguments": json.dumps({"argv": ["fixture"]}),
                        }
                    message["tool_calls"] = [call]
            return {
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    provider = Provider()
    try:
        if continuation in ("forbidden_tool", "reused_call"):
            with pytest.raises(PermissionError if continuation == "forbidden_tool" else ValueError):
                await Coordinator(store, provider).execute(run["id"])
        else:
            await Coordinator(store, provider).execute(run["id"])
        assert provider.requests == (5 if continuation == "next_tool" else 3)
        assert (
            store.get_run(run["id"])["state"]
            == {
                "answer": "verifying",
                "empty": "blocked",
                "next_tool": "verifying",
                "forbidden_tool": "failed",
                "reused_call": "failed",
            }[continuation]
        )
        events = store.list_events(run["id"])
        assert sum(event["event_type"] == "tool.intended" for event in events) == (
            2 if continuation == "next_tool" else 1
        )
        assert sum(event["event_type"] == "model.response_requested" for event in events) == (
            2 if continuation == "next_tool" else 1
        )
        if continuation == "next_tool":
            assert (project / "output.txt").read_text() == "saved once"
        else:
            assert not (project / "output.txt").exists()
        history = store.conversation_messages(run["id"])
        assert any(
            item["role"] == "user" and "saved progress" in item["content"] for item in history
        )
        assert "private fixture" not in json.dumps(history)
        assert not store.unresolved_tool_calls(run["id"])
    finally:
        await provider.close()


@pytest.mark.parametrize("legacy", [False, True])
async def test_repair_scope_and_pending_budget_survive_reopening(workspace_tmp_path, legacy):
    project = workspace_tmp_path / "project"
    project.mkdir()
    store = RunStore(workspace_tmp_path / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Continue saved work",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model="fixture",
        max_iterations=4,
    )
    owner = store.acquire_lease(run["id"], "fixture", seconds=60)
    store.append_worker_event(
        run["id"],
        owner,
        "model.message",
        {
            "message": {"role": "assistant", "content": ""},
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        },
    )
    kind = "model.answer_requested" if legacy else "model.response_requested"
    store.append_worker_event(
        run["id"],
        owner,
        kind,
        {
            "message": {"role": "user", "content": "Saved engine clarification fixture"},
            "actor": "engine",
        },
    )
    store.release_lease(run["id"], owner)

    class Provider(LMStudioProvider):
        requests = 0

        async def diagnose_tools(self, model, *, request=None):
            return True

        async def chat(self, *, model, messages, tools=None, **kwargs):
            self.requests += 1
            assert (tools == []) == legacy
            assert messages[-1]["content"] == "Saved engine clarification fixture"
            return {
                "choices": [{"message": {"role": "assistant", "content": ""}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    provider = Provider()
    try:
        reopened = RunStore(store.path)
        await Coordinator(reopened, provider).execute(run["id"])
        assert provider.requests == 1
        assert reopened.get_run(run["id"])["state"] == "blocked"
        assert (
            sum(
                event["event_type"] in ("model.answer_requested", "model.response_requested")
                for event in reopened.list_events(run["id"])
            )
            == 1
        )
    finally:
        await provider.close()

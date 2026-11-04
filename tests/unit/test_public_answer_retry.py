import json

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


@pytest.mark.parametrize("continuation", ["answer", "empty", "tool"])
async def test_empty_public_answer_gets_one_durable_reply_only_attempt(
    workspace_tmp_path, continuation
):
    project = workspace_tmp_path / "project"
    project.mkdir()
    (project / "input.txt").write_text("fixture result")
    store = RunStore(workspace_tmp_path / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Read input.txt",
        acceptance_criteria=[],
        workspace=str(project),
        mode="observe",
        model="fixture",
        max_iterations=4,
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
            elif self.requests == 2:
                message = {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "private fixture must not become a public answer",
                }
            else:
                assert self.requests == 3 and tools == []
                assert (
                    messages[-1]["role"] == "user"
                    and "public text reply" in messages[-1]["content"]
                )
                assert "fixture result" in next(
                    item["content"] for item in messages if item["role"] == "tool"
                )
                message = {
                    "role": "assistant",
                    "content": "fixture result" if continuation == "answer" else "",
                }
                if continuation == "tool":
                    call["id"] = "repeat"
                    message["tool_calls"] = [call]
            return {
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    provider = Provider()
    try:
        if continuation == "tool":
            with pytest.raises(PermissionError, match="current catalog"):
                await Coordinator(store, provider).execute(run["id"])
        else:
            await Coordinator(store, provider).execute(run["id"])
        assert provider.requests == 3
        assert (
            store.get_run(run["id"])["state"]
            == {"answer": "verifying", "empty": "blocked", "tool": "failed"}[continuation]
        )
        events = store.list_events(run["id"])
        assert sum(event["event_type"] == "tool.intended" for event in events) == 1
        assert sum(event["event_type"] == "model.answer_requested" for event in events) == 1
        history = store.conversation_messages(run["id"])
        assert any(
            item["role"] == "user" and "public text reply" in item["content"] for item in history
        )
        assert "private fixture" not in json.dumps(history)
    finally:
        await provider.close()

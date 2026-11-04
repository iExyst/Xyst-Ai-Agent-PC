import json

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


class CorrectionProvider(LMStudioProvider):
    def __init__(self):
        super().__init__()
        self.turn = 0

    async def diagnose_tools(self, model, *, request=None):
        return True

    async def chat(self, *, model, messages, tools=None):
        turn = self.turn
        self.turn += 1
        if turn in {2, 5}:
            return {"choices": [{"message": {"role": "assistant", "content": "Candidate ready"}}]}
        if turn in {0, 3}:
            if turn == 3:
                assert messages[-1]["role"] == "user"
                assert "Independent artifact verification failed" in messages[-1]["content"]
            name, arguments = "workspace_read_file", {"path": "config.txt"}
        else:
            prior = json.loads(messages[-1]["content"])
            name, arguments = (
                "workspace_write_file",
                {
                    "path": "config.txt",
                    "content": "value=2" if turn == 1 else "value=2\n",
                    "expected_sha256": prior["sha256"],
                },
            )
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": f"call-{turn}",
                                "type": "function",
                                "function": {"name": name, "arguments": json.dumps(arguments)},
                            }
                        ],
                    }
                }
            ]
        }


async def test_verifier_feedback_drives_a_second_hash_checked_edit(workspace_tmp_path):
    (workspace_tmp_path / "config.txt").write_bytes(b"value=1\n")
    store = RunStore(workspace_tmp_path / "runs.sqlite3")
    run_id = store.create_run(
        objective="Set value=2 with final newline",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model="fixture",
        file_checks=[{"path": "config.txt", "expected_content": "value=2\n"}],
    )["id"]
    provider = CorrectionProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert provider.turn == 6
    assert (workspace_tmp_path / "config.txt").read_bytes() == b"value=2\n"
    assert store.get_run(run_id)["state"] == "verifying"
    events = store.list_events(run_id)
    assert sum(event["event_type"] == "verification.feedback" for event in events) == 1
    assert sum(event["event_type"] == "verification.passed" for event in events) == 1
    assert not store.unresolved_tool_calls(run_id)
    assert len(store.conversation_messages(run_id)) == 11

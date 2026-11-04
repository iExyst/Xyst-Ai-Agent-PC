import json

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.memory import MemoryStore
from xaa.storage.runs import RunStore


class RecallProvider(LMStudioProvider):
    def __init__(self):
        super().__init__()
        self.seen = []

    async def diagnose_tools(self, model, *, request=None):
        return True

    async def chat(self, *, model, messages, tools=None):
        assert not tools
        encoded = json.dumps(messages)
        self.seen.append("USER_PREFERENCE_MARKER" in encoded)
        assert "OTHER_ACCOUNT_MARKER" not in encoded
        assert "TOOL_OUTPUT_MARKER" not in encoded
        return {"choices": [{"message": {"role": "assistant", "content": "Candidate"}}]}


async def test_deleted_preference_is_absent_from_next_run_context(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "recall.sqlite3")
    memories = MemoryStore(store)
    item = memories.save_preference("local-user", "USER_PREFERENCE_MARKER")
    memories.save_preference("other-account", "OTHER_ACCOUNT_MARKER")
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO memories VALUES ('untrusted', 'knowledge', 'local-user', 'TOOL_OUTPUT_MARKER', 'web-tool', 0.2, '2026-11-03T00:00:00Z', '2026-11-03T00:00:00Z', 1)"
        )
    provider = RecallProvider()
    try:
        for index in range(2):
            if index:
                memories.delete("local-user", item["id"])
            run_id = store.create_run(
                objective="Explain",
                acceptance_criteria=[],
                workspace=None,
                mode="observe",
                model="fixture",
            )["id"]
            await Coordinator(RunStore(store.path), provider).execute(run_id)
    finally:
        await provider.close()
    assert provider.seen == [True, False]


def test_preference_recall_reports_omitted_entries_within_budget(workspace_tmp_path):
    memories = MemoryStore(RunStore(workspace_tmp_path / "budget.sqlite3"))
    memories.save_preference("local-user", "oversized " * 500)
    small = memories.save_preference("local-user", "Use concise answers")
    recalled = memories.recall_preferences("local-user", max_bytes=200)
    assert [item["id"] for item in recalled["items"]] == [small["id"]]
    assert recalled["omitted"] == 1
    assert recalled["bytes"] <= 200

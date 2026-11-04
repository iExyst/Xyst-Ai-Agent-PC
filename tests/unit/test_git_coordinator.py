import asyncio
import json
import subprocess

from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.storage.runs import RunStore


class GitProvider(LMStudioProvider):
    async def diagnose_tools(self, model, *, request=None):
        return True

    async def chat(self, *, model, messages, tools=None):
        if messages[-1]["role"] == "user":
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "git-status",
                                    "type": "function",
                                    "function": {
                                        "name": "workspace_git_inspect",
                                        "arguments": '{"operation":"status"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        result = json.loads(messages[-1]["content"])
        assert result["artifact_id"]
        assert "untracked.txt" in result["excerpt"]
        return {"choices": [{"message": {"role": "assistant", "content": "Found untracked.txt"}}]}


async def test_coordinator_returns_journaled_git_artifact_to_model(workspace_tmp_path):
    root = workspace_tmp_path / "repo"
    root.mkdir()
    await asyncio.to_thread(
        subprocess.run, ["git", "init", str(root)], check=True, capture_output=True
    )
    (root / "untracked.txt").write_bytes(b"data")
    store = RunStore(workspace_tmp_path / "db.sqlite3")
    run_id = store.create_run(
        objective="Inspect status",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model="fixture",
    )["id"]
    provider = GitProvider()
    try:
        await Coordinator(store, provider).execute(run_id)
    finally:
        await provider.close()
    assert store.get_run(run_id)["state"] == "verifying"
    assert len(store.list_artifacts(run_id)) == 1

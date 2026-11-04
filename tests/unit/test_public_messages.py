import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.contracts import public_assistant_message, validate_completion
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.states import RunState
from xaa.storage.runs import RunStore

MARKER = "PRIVATE_METADATA_FIXTURE"


def message():
    return {
        "role": "assistant",
        "content": "Public answer",
        "reasoning_content": MARKER,
        "analysis": MARKER,
        "tool_calls": [
            {
                "id": "public-call",
                "type": "function",
                "private_metadata": MARKER,
                "function": {
                    "name": "workspace_read_file",
                    "arguments": ' {"path": "input.txt"} ',
                    "private_metadata": MARKER,
                },
            }
        ],
    }


def test_completion_keeps_only_public_message_fields():
    public = validate_completion({"choices": [{"message": message()}]}).message.model_dump(
        exclude_none=True
    )
    assert public == public_assistant_message(message())
    assert MARKER not in json.dumps(public)
    assert public["tool_calls"][0]["function"]["arguments"] == ' {"path": "input.txt"} '


@pytest.mark.parametrize("kind", ["model.message", "task.model.message"])
def test_storage_boundary_drops_nonpublic_message_fields(workspace_tmp_path, kind):
    store = RunStore(workspace_tmp_path / "new.sqlite3")
    run = store.create_run(
        objective="Public records",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )
    token = store.acquire_lease(run["id"], "worker")
    store.append_worker_event(run["id"], token, kind, {"message": message(), "task_id": "inspect"})
    raw = store.list_events(run["id"])[-1]
    assert raw["payload"]["message"] == public_assistant_message(message())
    assert MARKER not in json.dumps(raw)


def test_legacy_replay_and_public_events_preserve_sequence_without_rewriting_history(
    workspace_tmp_path,
):
    (workspace_tmp_path / "input.txt").write_text("fixture", encoding="utf-8")
    store = RunStore(workspace_tmp_path / "legacy.sqlite3")
    run = store.create_run(
        objective="Replay",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model=None,
    )
    token = store.acquire_lease(run["id"], "worker")
    store.append_worker_event(run["id"], token, "model.message", {"message": message()})
    # Simulate a historical record written by the earlier message schema.
    with store.connection() as connection:
        connection.execute(
            "UPDATE run_events SET payload=? WHERE run_id=? AND event_type='model.message'",
            (json.dumps({"message": message()}), run["id"]),
        )
    WorkspaceExecutor(store).read_file(
        run["id"], token, "public-call", {"path": "input.txt"}, idempotency_key="public-call"
    )
    for state in [RunState.PLANNING, RunState.RUNNING, RunState.VERIFYING, RunState.COMPLETED]:
        store.transition(run["id"], state, owner=token)
    with store.connection() as connection:
        before = connection.execute(
            "SELECT payload FROM run_events WHERE run_id=? AND event_type='model.message'",
            (run["id"],),
        ).fetchone()[0]
    replay = store.conversation_messages(run["id"])
    assert replay[0] == public_assistant_message(message())
    assert replay[1]["role"] == "tool" and replay[1]["tool_call_id"] == "public-call"
    assert json.loads(replay[1]["content"])["text"] == "fixture"
    token_value = "public-event-api-token-" + "x" * 32
    client = TestClient(create_app(LMStudioProvider(), SecretStr(token_value), store=store))
    headers = {"Authorization": f"Bearer {token_value}"}
    response = client.get(f"/runs/{run['id']}/events", headers=headers)
    assert response.status_code == 200 and MARKER not in response.text
    stream = client.get(f"/runs/{run['id']}/events/stream", headers=headers)
    assert stream.status_code == 200 and MARKER not in stream.text
    assert [e["sequence"] for e in response.json()] == [
        e["sequence"] for e in store.list_events(run["id"])
    ]
    with store.connection() as connection:
        after = connection.execute(
            "SELECT payload FROM run_events WHERE run_id=? AND event_type='model.message'",
            (run["id"],),
        ).fetchone()[0]
    assert before == after and MARKER in after


async def test_recovery_does_not_promote_metadata_only_response_to_candidate(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "empty.sqlite3")
    run = store.create_run(
        objective="Recover", acceptance_criteria=[], workspace=None, mode="observe", model="fixture"
    )
    token = store.acquire_lease(run["id"], "seed")
    store.append_worker_event(run["id"], token, "model.message", {"message": {"role": "assistant"}})
    with store.connection() as connection:
        connection.execute(
            "UPDATE run_events SET payload=? WHERE run_id=? AND event_type='model.message'",
            (
                json.dumps({"message": {"role": "assistant", "reasoning_content": MARKER}}),
                run["id"],
            ),
        )
    store.release_lease(run["id"], token)
    original_attempts = store.inference_attempts(run["id"])

    class NoInference(LMStudioProvider):
        async def chat(self, **kwargs):
            raise AssertionError("Recovery must not infer from a metadata-only candidate.")

    provider = NoInference()
    try:
        await Coordinator(store, provider).execute(run["id"])
    finally:
        await provider.close()
    assert store.get_run(run["id"])["state"] == "blocked"
    assert store.inference_attempts(run["id"]) == original_attempts
    assert not any(
        e["event_type"] == "model.inference.started" for e in store.list_events(run["id"])
    )
    assert any(
        e["payload"].get("reason") == "saved_candidate_has_no_public_text"
        for e in store.list_events(run["id"])
    )

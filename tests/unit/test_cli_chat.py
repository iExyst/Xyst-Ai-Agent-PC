from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.cli_chat import CLIChatStore
from xaa.storage.runs import RunStore

TOKEN = "a-cli-chat-test-token-long-enough-to-authenticate"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class QuietProvider(LMStudioProvider):
    async def close(self) -> None:
        return None


def test_acp_session_listing_filters_scope_and_cursor(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "acp-list.sqlite3")
    chat = CLIChatStore(store)
    run_id = store.create_run(
        objective="List fixture",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="observe",
        model="local/model",
    )["id"]
    for session_id, model in (
        ("a" * 32, "local/model"),
        ("b" * 32, "other/model"),
        ("terminal", "local/model"),
        ("c" * 32, "local/model"),
    ):
        chat.append_turn(
            session_id,
            run_id=run_id,
            model=model,
            workspace=str(workspace_tmp_path),
            mode="observe",
            user_message="Hello",
            assistant_message="World",
        )
    first = chat.list_acp(
        model="local/model", workspace=str(workspace_tmp_path), mode="observe", limit=1
    )
    second = chat.list_acp(
        model="local/model", workspace=str(workspace_tmp_path), mode="observe", after=first[0]["id"]
    )
    assert [item["id"] for item in first + second] == ["a" * 32, "c" * 32]


def test_cli_chat_session_persists_bounded_turns_and_rejects_config_change(workspace_tmp_path):
    database = workspace_tmp_path / "cli-chat.sqlite3"
    store = RunStore(database)
    chat = CLIChatStore(store)
    run_id = store.create_run(
        objective="First turn",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="local/model",
    )["id"]
    saved = chat.append_turn(
        "terminal",
        run_id=run_id,
        model="local/model",
        workspace=None,
        mode="observe",
        user_message="What is this project?",
        assistant_message="It is a local agent.",
    )
    assert saved["messages"] == [
        {"role": "user", "content": "What is this project?", "run_id": run_id},
        {"role": "assistant", "content": "It is a local agent.", "run_id": run_id},
    ]
    reopened = CLIChatStore(RunStore(database))
    assert reopened.get("terminal") == saved
    next_run = store.create_run(
        objective="Large reply",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="local/model",
    )["id"]
    bounded = reopened.append_turn(
        "terminal",
        run_id=next_run,
        model="local/model",
        workspace=None,
        mode="observe",
        user_message="Give a long reply",
        assistant_message="😀" * 4000,
    )
    assert bounded["messages"][-1]["content"].endswith(
        "[Prior answer shortened in saved chat context; full candidate remains in run history.]"
    )
    assert sum(len(item["content"].encode("utf-8")) for item in bounded["messages"]) <= 8192
    try:
        reopened.append_turn(
            "terminal",
            run_id=run_id,
            model="different/model",
            workspace=None,
            mode="observe",
            user_message="Continue elsewhere?",
            assistant_message="No.",
        )
    except ValueError as exc:
        assert "settings changed" in str(exc)
    else:
        raise AssertionError("Session accepted a changed model without reset")
    assert reopened.delete("terminal")
    assert reopened.get("terminal") is None


def test_cli_chat_api_authenticates_and_keeps_messages_in_session_scope(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "cli-chat-api.sqlite3")
    run_id = store.create_run(
        objective="A saved run",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="local/model",
    )["id"]
    with TestClient(create_app(QuietProvider(), SecretStr(TOKEN), store=store)) as client:
        assert client.get("/chat/sessions/default").status_code == 401
        assert client.get("/chat/sessions/default", headers=HEADERS).status_code == 404
        missing_run = client.post(
            "/chat/sessions/default/turn",
            headers=HEADERS,
            json={
                "run_id": "missing",
                "model": "local/model",
                "workspace": None,
                "mode": "observe",
                "user_message": "Hello",
                "assistant_message": "Hi",
            },
        )
        assert missing_run.status_code == 404
        saved = client.post(
            "/chat/sessions/default/turn",
            headers=HEADERS,
            json={
                "run_id": run_id,
                "model": "local/model",
                "workspace": None,
                "mode": "observe",
                "user_message": "Hello",
                "assistant_message": "Hi",
            },
        )
        assert saved.status_code == 200
        assert [item["content"] for item in saved.json()["messages"]] == ["Hello", "Hi"]
        assert (
            client.get("/chat/sessions/default", headers=HEADERS).json()["messages"]
            == saved.json()["messages"]
        )
        assert client.get("/chat/sessions/other", headers=HEADERS).status_code == 404
        assert client.delete("/chat/sessions/default", headers=HEADERS).status_code == 204
        assert client.get("/chat/sessions/default", headers=HEADERS).status_code == 404

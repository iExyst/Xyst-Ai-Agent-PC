from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.history import latest_unfinished_run, list_runs
from xaa.storage.runs import RunStore


def test_history_keyset_survives_reopen_ties_and_new_insertions(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "history.sqlite3")
    identities = [
        store.create_run(
            objective=f"Objective {index}",
            acceptance_criteria=[],
            workspace=None,
            mode="observe",
            model=None,
        )["id"]
        for index in range(5)
    ]
    with store.connection() as connection:
        connection.execute("UPDATE runs SET created_at='2026-11-01T00:00:00+00:00'")
    reopened = RunStore(store.path)
    first = list_runs(reopened, limit=2)
    newer = reopened.create_run(
        objective="Newer", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    collected = [item["id"] for item in first["items"]]
    cursor = first["next_cursor"]
    while cursor:
        page = list_runs(reopened, limit=2, before=cursor)
        collected.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
    assert collected == sorted(identities, reverse=True)
    assert newer not in collected


def test_latest_unfinished_skips_newer_terminal_runs(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "latest-unfinished.sqlite3")
    unfinished = store.create_run(
        objective="Interrupted run",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )
    terminal = store.create_run(
        objective="Finished run", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )
    with store.connection() as connection:
        connection.execute("UPDATE runs SET state='blocked' WHERE id=?", (unfinished["id"],))
        connection.execute("UPDATE runs SET state='completed' WHERE id=?", (terminal["id"],))
    latest = latest_unfinished_run(store)
    assert latest is not None
    assert latest["id"] == unfinished["id"]
    assert latest["state"] == "blocked"
    with store.connection() as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT id FROM runs "
            "WHERE state NOT IN ('cancelled','completed','failed') "
            "ORDER BY updated_at DESC,created_at DESC,id DESC LIMIT 1"
        ).fetchall()
    assert any("runs_unfinished_updated" in row["detail"] for row in plan)


def test_history_api_is_bounded_authenticated_and_literal(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "api.sqlite3")
    text = "literal_%_needle " + "x" * 700
    run_id = store.create_run(
        objective=text, acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "owner")
    store.append_worker_event(
        run_id,
        token,
        "model.message",
        {
            "message": {
                "role": "assistant",
                "content": "Distinct Sakura paragraph",
                "reasoning_content": "HIDDEN_METADATA",
            }
        },
    )
    api_token = "x" * 32
    headers = {"Authorization": f"Bearer {api_token}"}
    with TestClient(create_app(LMStudioProvider(), SecretStr(api_token), store=store)) as client:
        assert client.get("/runs").status_code == 401
        assert client.get("/runs/latest-unfinished").status_code == 401
        response = client.get("/runs", headers=headers, params={"q": "_%_"})
        assert response.status_code == 200
        item = response.json()["items"][0]
        assert item["id"] == run_id and len(item["objective"]) == 500
        latest_unfinished = client.get("/runs/latest-unfinished", headers=headers)
        assert latest_unfinished.status_code == 200
        assert latest_unfinished.json()["id"] == run_id
        assert token not in response.text and "lease_owner" not in item
        assert client.get("/runs", headers=headers, params={"q": "LITERAL"}).json()["items"] == []
        assert (
            client.get("/runs", headers=headers, params={"q": "' OR 1=1 --"}).json()["items"] == []
        )
        assert client.get("/runs", headers=headers, params={"limit": 51}).status_code == 422
        assert (
            client.get("/runs", headers=headers, params={"before": "bad-cursor!"}).status_code
            == 400
        )
        assert client.get(f"/runs/{run_id}", headers=headers).json()["objective"] == text
        matches = client.get(
            "/runs", headers=headers, params={"scope": "conversation", "q": "SAKURA"}
        )
        assert matches.status_code == 200 and matches.json()["items"][0]["id"] == run_id
        assert (
            client.get(
                "/runs", headers=headers, params={"scope": "conversation", "q": "HIDDEN_METADATA"}
            ).json()["items"]
            == []
        )
        assert client.get("/runs", headers=headers, params={"scope": "invalid"}).status_code == 422

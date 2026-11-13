from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import time
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.channels.discord import (
    DiscordIdentity,
    DiscordMessage,
    DiscordStore,
    dm_only_intents,
)
from xaa.channels.webhook import sign_webhook
from xaa.providers.lm_studio import LMStudioProvider, ModelCapabilities, ModelInfo, ProviderError
from xaa.runtime.states import RunState
from xaa.runtime.task_graph import TaskDefinition
from xaa.storage.runs import RunStore
from xaa.storage.tasks import TaskStore
from xaa.version import APP_VERSION


class FakeProvider(LMStudioProvider):
    async def diagnose_tools(self, model, *, request=None):
        return True

    async def health(self) -> bool:
        return True

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fixture/model")]

    async def stream(self, *, model: str, messages: list[dict[str, Any]]) -> AsyncIterator[str]:
        yield '{"choices":[{"delta":{"content":"hello"}}]}'
        yield '{"choices":[{"delta":{"content":" world"}}]}'

    async def close(self) -> None:
        return None


TOKEN = "a-test-token-that-is-long-enough-to-pass-validation"


def test_mcp_oauth_credentials_are_private_and_owner_clearable(workspace_tmp_path):
    class Credentials:
        def __init__(self):
            self.values: dict[str, str] = {}

        def get(self, profile="lm-studio"):
            return self.values.get(profile)

        def set(self, token, profile="lm-studio"):
            self.values[profile] = token

        def delete(self, profile="lm-studio"):
            self.values.pop(profile, None)

    credentials = Credentials()
    store = RunStore(workspace_tmp_path / "mcp-oauth-api.sqlite3")
    app = create_app(
        FakeProvider(),
        SecretStr(TOKEN),
        store=store,
        credential_store=credentials,
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        created = client.post(
            "/mcp/servers",
            headers=headers,
            json={
                "name": "OAuth API fixture",
                "transport": "streamable_http",
                "endpoint": "https://mcp.example.test/mcp",
                "command": "",
                "args": [],
                "cwd": None,
                "secret_env": [],
            },
        )
        assert created.status_code == 201, created.text
        server_id = created.json()["id"]
        credentials.set('{"access_token":"private-token"}', f"mcp:{server_id}:oauth-tokens")
        credentials.set('{"client_id":"private-client"}', f"mcp:{server_id}:oauth-client")
        assert client.get("/mcp/servers", headers=headers).json()[0]["oauth_configured"] is True
        assert "private-token" not in client.get("/mcp/servers", headers=headers).text
        assert client.delete(f"/mcp/servers/{server_id}/oauth").status_code == 401
        assert client.delete(f"/mcp/servers/{server_id}/oauth", headers=headers).status_code == 204
        assert credentials.get(f"mcp:{server_id}:oauth-tokens") is None
        assert credentials.get(f"mcp:{server_id}:oauth-client") is None


def test_project_validation_timeout_requires_reconciliation_before_retry(
    workspace_tmp_path, monkeypatch
):
    async def timeout(*_args, **_kwargs):
        raise TimeoutError("deadline expired")

    monkeypatch.setattr("xaa.api.app.validate_project", timeout)
    app = create_app(
        FakeProvider(),
        SecretStr(TOKEN),
        store=RunStore(workspace_tmp_path / "validation-timeout.sqlite3"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/runs/example/validation",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={
                "request_id": "d12a331d-3a3f-40e1-b96d-013be46d405a",
                "expected_revision": 0,
                "argv": ["/usr/bin/true"],
                "max_seconds": 15,
            },
        )

    assert response.status_code == 409
    assert "refresh evidence" in response.json()["detail"]
    assert "reconcile" in response.json()["detail"]


def test_explicit_channel_identity_links_are_authenticated_and_revocable(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "identity-links-api.sqlite3")
    now = "2026-11-08T00:00:00+00:00"
    bindings: list[tuple[str, str]] = []
    with store.connection() as connection:
        for channel, account, identity in (
            ("telegram", "bot-1", "1001"),
            ("email", "mailbox-1", "person@example.test"),
            ("discord", "bot-2", "3001"),
        ):
            session_id = f"session-{channel}"
            binding_id = f"binding-{channel}"
            bindings.append((binding_id, session_id))
            connection.execute(
                "INSERT INTO sessions(id,channel,account_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (session_id, channel, account, f"{channel} history", now, now),
            )
            connection.execute(
                "INSERT INTO channel_bindings(id,channel,account_id,external_identity,session_id,authorized,created_at) VALUES(?,?,?,?,?,1,?)",
                (binding_id, channel, account, identity, session_id, now),
            )

    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        assert client.get("/channels/identity-bindings").status_code == 401
        listed = client.get("/channels/identity-bindings", headers=headers)
        assert listed.status_code == 200
        assert {item["channel"] for item in listed.json()} == {"telegram", "email", "discord"}

        created = client.post(
            "/channels/identity-links",
            headers=headers,
            json={
                "binding_ids": [bindings[0][0], bindings[1][0]],
                "shared_history_binding_id": bindings[0][0],
            },
        )
        assert created.status_code == 201, created.text
        link_id = created.json()["id"]
        linked = client.get("/channels/identity-bindings", headers=headers).json()
        linked_members = [item for item in linked if item["identity_link_id"] == link_id]
        assert len(linked_members) == 2
        assert {item["session_id"] for item in linked_members} == {bindings[0][1]}

        added = client.post(
            f"/channels/identity-links/{link_id}/members",
            headers=headers,
            json={"binding_id": bindings[2][0], "confirmed": True},
        )
        assert added.status_code == 201, added.text
        assert added.json()["shared_session_id"] == bindings[0][1]
        linked = client.get("/channels/identity-bindings", headers=headers).json()
        assert {item["identity_link_id"] for item in linked} == {link_id}
        assert {item["session_id"] for item in linked} == {bindings[0][1]}

        denied = client.request(
            "DELETE",
            f"/channels/identity-links/{link_id}",
            headers=headers,
            json={"confirmed": False},
        )
        assert denied.status_code == 422
        revoked = client.request(
            "DELETE",
            f"/channels/identity-links/{link_id}",
            headers=headers,
            json={"confirmed": True},
        )
        assert revoked.status_code == 200
        assert revoked.json() == {"unlinked_bindings": 3}
        separated = client.get("/channels/identity-bindings", headers=headers).json()
        assert all(item["identity_link_id"] is None for item in separated)
        assert len({item["session_id"] for item in separated}) == 3
        assert (
            client.request(
                "DELETE",
                f"/channels/identity-links/{link_id}",
                headers=headers,
                json={"confirmed": True},
            ).status_code
            == 404
        )


def test_channel_identity_group_merge_requires_confirmation_and_preserves_target_session(
    workspace_tmp_path,
):
    store = RunStore(workspace_tmp_path / "identity-links-merge-api.sqlite3")
    now = "2026-11-08T00:00:00+00:00"
    with store.connection() as connection:
        for binding_id, session_id, channel, account, identity in (
            ("binding-telegram", "session-telegram", "telegram", "bot-1", "1001"),
            ("binding-email", "session-email", "email", "mailbox-1", "person@example.test"),
            ("binding-discord", "session-discord", "discord", "bot-2", "3001"),
            ("binding-webhook", "session-webhook", "webhook", "endpoint-1", "sender-1"),
        ):
            connection.execute(
                "INSERT INTO sessions(id,channel,account_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (session_id, channel, account, f"{channel} history", now, now),
            )
            connection.execute(
                "INSERT INTO channel_bindings(id,channel,account_id,external_identity,session_id,authorized,created_at) VALUES(?,?,?,?,?,1,?)",
                (binding_id, channel, account, identity, session_id, now),
            )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        target = client.post(
            "/channels/identity-links",
            headers=headers,
            json={
                "binding_ids": ["binding-telegram", "binding-email"],
                "shared_history_binding_id": "binding-telegram",
            },
        ).json()["id"]
        source = client.post(
            "/channels/identity-links",
            headers=headers,
            json={
                "binding_ids": ["binding-discord", "binding-webhook"],
                "shared_history_binding_id": "binding-discord",
            },
        ).json()["id"]
        assert (
            client.post(
                f"/channels/identity-links/{target}/merge",
                headers=headers,
                json={"source_link_id": source, "confirmed": False},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/channels/identity-links/{target}/merge",
                json={"source_link_id": source, "confirmed": True},
            ).status_code
            == 401
        )
        merged = client.post(
            f"/channels/identity-links/{target}/merge",
            headers=headers,
            json={"source_link_id": source, "confirmed": True},
        )
        assert merged.status_code == 200, merged.text
        assert merged.json() == {
            "target_link_id": target,
            "source_link_id": source,
            "merged_bindings": 2,
            "shared_session_id": "session-telegram",
            "retained_source_session_id": "session-discord",
        }
        bindings = client.get("/channels/identity-bindings", headers=headers).json()
        assert {item["identity_link_id"] for item in bindings} == {target}
        assert {item["session_id"] for item in bindings} == {"session-telegram"}


def test_discord_channel_credentials_pairing_and_dm_only_intents(workspace_tmp_path, monkeypatch):
    class Credentials:
        def __init__(self):
            self.values: dict[str, str] = {}

        def get(self, profile="lm-studio"):
            return self.values.get(profile)

        def set(self, token, profile="lm-studio"):
            self.values[profile] = token

        def delete(self, profile="lm-studio"):
            self.values.pop(profile, None)

    async def fake_get_me(self):
        return DiscordIdentity("100000000000000001", "xaa-test")

    monkeypatch.setattr("xaa.api.app.DiscordBotApi.get_me", fake_get_me)
    monkeypatch.setattr("xaa.api.app.DiscordGatewayService.start", lambda self: None)
    credentials = Credentials()
    store = RunStore(workspace_tmp_path / "discord-api.sqlite3")
    app = create_app(
        FakeProvider(),
        SecretStr(TOKEN),
        store=store,
        credential_store=credentials,
    )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(app) as client:
        assert client.get("/channels/discord").status_code == 401
        assert client.get("/channels/discord", headers=headers).json() == {
            "configured": False,
            "bot_id": None,
            "bot_username": None,
            "linked_users": 0,
            "processed_messages": 0,
            "deliveries": {},
            "unknown_deliveries": [],
        }
        saved = client.put(
            "/channels/discord/credential",
            headers=headers,
            json={"token": "secret-discord-bot-token"},
        )
        assert saved.status_code == 200
        assert "secret-discord-bot-token" not in saved.text
        assert credentials.values["discord-bot"] == "secret-discord-bot-token"
        pairing = client.post("/channels/discord/pairing", headers=headers)
        assert pairing.status_code == 201
        assert len(pairing.json()["code"]) >= 32

        discord = DiscordStore(store)
        identity = DiscordIdentity("100000000000000001", "xaa-test")
        discord.process_message(
            identity,
            DiscordMessage(
                "1",
                "300000000000000003",
                "200000000000000002",
                f"/pair {pairing.json()['code']}",
                True,
            ),
            model="fixture/model",
        )
        queued = discord.process_message(
            identity,
            DiscordMessage("2", "300000000000000003", "200000000000000002", "hello", True),
            model="fixture/model",
        )
        with store.connection() as connection:
            connection.execute(
                "UPDATE runs SET state='completed',updated_at=? WHERE id=?",
                ("2026-11-08T12:00:00+00:00", queued["run_id"]),
            )
            RunStore.record_event(
                connection,
                queued["run_id"],
                "model.message",
                {"message": {"role": "assistant", "content": "answer"}},
            )
        assert discord.claim_delivery(identity.account_id) is not None
        assert discord.reconcile_abandoned_sends() == 1
        unresolved = "/channels/discord/deliveries/2/reconcile"
        assert (
            client.post(
                unresolved,
                headers=headers,
                json={"resolution": "retry", "confirmed_destination_checked": False},
            ).status_code
            == 422
        )
        assert client.post(
            unresolved,
            headers=headers,
            json={"resolution": "delivered", "confirmed_destination_checked": True},
        ).json()["deliveries"] == {"sent": 1}
        revoked = client.delete("/channels/discord/links", headers=headers)
        assert revoked.json() == {"revoked_users": 1, "linked_users": 0}
        cleared = client.delete("/channels/discord/credential", headers=headers)
        assert cleared.status_code == 200
        assert cleared.json()["configured"] is False

    intents = dm_only_intents()
    assert intents.dm_messages is True
    assert intents.guild_messages is False
    assert intents.message_content is False


def test_openapi_advertises_packaged_engine_version(workspace_tmp_path) -> None:
    app = create_app(
        FakeProvider(), SecretStr(TOKEN), store=RunStore(workspace_tmp_path / "api.sqlite3")
    )
    assert app.version == APP_VERSION


def test_signed_webhook_creates_one_observe_run_and_rejects_replay_changes(workspace_tmp_path):
    class Credentials:
        def __init__(self):
            self.values: dict[str, str] = {}

        def get(self, profile="lm-studio"):
            return self.values.get(profile)

        def set(self, token, profile="lm-studio"):
            self.values[profile] = token

        def delete(self, profile="lm-studio"):
            self.values.pop(profile, None)

    store = RunStore(workspace_tmp_path / "webhook-api.sqlite3")
    credentials = Credentials()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    app = create_app(
        FakeProvider(),
        SecretStr(TOKEN),
        store=store,
        credential_store=credentials,
    )
    with store.connection() as connection:
        connection.execute(
            "UPDATE provider_profiles SET selected_model='fixture/model' WHERE id='lm-studio'"
        )
    with TestClient(app) as client:
        assert client.get("/channels/webhook").status_code == 401
        assert client.get("/channels/webhook", headers=headers).json() == {"configured": False}
        configured = client.post("/channels/webhook/credential", headers=headers)
        assert configured.status_code == 200
        secret = configured.json()["secret"]
        assert len(secret) >= 32
        assert client.get("/channels/webhook", headers=headers).json() == {"configured": True}

        event_id = "fixture-event-1"
        timestamp = str(int(time.time()))
        body = b'{"sender_id":"sender-7","conversation_id":"thread-2","text":"Do this"}'
        signed_headers = {
            **headers,
            "X-XAA-Timestamp": timestamp,
            "X-XAA-Event-Id": event_id,
            "X-XAA-Signature": sign_webhook(secret, timestamp, event_id, body),
            "Content-Type": "application/json",
        }
        assert client.post("/webhooks/inbound", content=body).status_code == 401
        bad_signature = {**signed_headers, "X-XAA-Signature": "sha256=" + "0" * 64}
        assert (
            client.post("/webhooks/inbound", content=body, headers=bad_signature).status_code == 401
        )
        stale_time = str(int(time.time()) - 301)
        stale_headers = {
            **signed_headers,
            "X-XAA-Timestamp": stale_time,
            "X-XAA-Signature": sign_webhook(secret, stale_time, event_id, body),
        }
        assert (
            client.post("/webhooks/inbound", content=body, headers=stale_headers).status_code == 401
        )

        accepted = client.post("/webhooks/inbound", content=body, headers=signed_headers)
        repeated = client.post("/webhooks/inbound", content=body, headers=signed_headers)
        changed_body = b'{"sender_id":"sender-7","conversation_id":"thread-2","text":"Different"}'
        changed_headers = {
            **signed_headers,
            "X-XAA-Signature": sign_webhook(secret, timestamp, event_id, changed_body),
        }
        conflict = client.post("/webhooks/inbound", content=changed_body, headers=changed_headers)
        oversized = client.post(
            "/webhooks/inbound",
            content=b"x" * (64 * 1024 + 1),
            headers=signed_headers,
        )

    assert accepted.status_code == 202, accepted.text
    assert repeated.status_code == 202
    assert accepted.json()["run_id"] == repeated.json()["run_id"]
    assert conflict.status_code == 409
    assert oversized.status_code == 413
    run = store.get_run(accepted.json()["run_id"])
    assert run is not None
    assert run["workspace"] is None
    assert run["mode"] == "observe"
    assert run["model"] == "fixture/model"
    assert run["approved_web_hosts"] == []
    assert run["approved_mcp_tools"] == []
    with store.connection() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM runs WHERE objective LIKE '%Do this'"
        ).fetchone()[0]
    assert count == 1


def test_blocked_lost_specialist_can_be_inspected_and_explicitly_closed(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "task-recovery-api.sqlite3")
    run = store.create_run(
        objective="Recover task",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model="fixture",
        max_iterations=8,
    )
    run_id = run["id"]
    parent = store.acquire_lease(run_id, "setup")
    assert parent
    tasks = TaskStore(store)
    tasks.save_plan(run_id, parent, [TaskDefinition(id="lost", objective="Inspect evidence")])
    claim = tasks.claim_ready(run_id, parent)
    assert claim
    with store.connection() as connection:
        store.record_event(
            connection,
            run_id,
            "task.inference.started",
            {"task_id": "lost", "inference_id": "inference-pending"},
        )
        store.record_event(
            connection,
            run_id,
            "tokens.reserved",
            {
                "task_id": "lost",
                "inference_id": "inference-pending",
                "reserved_tokens": 11,
            },
        )
        store.record_event(
            connection,
            run_id,
            "task.model.message",
            {
                "task_id": "lost",
                "inference_id": "prior-empty-message",
                "message": {"role": "assistant", "content": ""},
                "usage": None,
            },
        )
        connection.execute("UPDATE task_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    assert tasks.recover_expired(run_id, parent) == ["lost"]
    store.transition(run_id, RunState.PLANNING, owner=parent)
    store.transition(run_id, RunState.RUNNING, owner=parent)
    store.transition(run_id, RunState.BLOCKED, reason="lost_worker", owner=parent)

    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        inspected = client.get(f"/runs/{run_id}/tasks/lost/recovery", headers=headers)
        refused = client.post(
            f"/runs/{run_id}/tasks/lost/recovery",
            headers=headers,
            json={"decision": "resume"},
        )
        closed = client.post(
            f"/runs/{run_id}/tasks/lost/recovery",
            headers=headers,
            json={"decision": "fail"},
        )
        unauthenticated = client.get(f"/runs/{run_id}/tasks/lost/recovery")

    assert inspected.status_code == 200
    assert inspected.json()["safe_to_resume"] is False
    assert inspected.json()["reserved_tokens"] == 11
    assert refused.status_code == 409
    assert closed.status_code == 202
    assert closed.json()["tasks"][0]["status"] == "failed"
    assert unauthenticated.status_code == 401


def test_public_tool_result_api_is_authenticated_and_excludes_internal_data(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "tool-result-api.sqlite3")
    run_id = store.create_run(
        objective="Read a file", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    assert token
    store.record_tool_intent(
        run_id,
        token,
        call_id="public-call",
        tool="workspace.read_file",
        version="1",
        arguments={"path": "secret.txt"},
        idempotency_key="public-action",
    )
    store.record_tool_result(run_id, token, "public-call", {"content": "visible result"})
    store.record_tool_intent(
        run_id,
        token,
        call_id="internal-call",
        tool="internal.check",
        version="1",
        arguments={},
        idempotency_key="internal-action",
        internal=True,
    )
    store.record_tool_result(run_id, token, "internal-call", {"content": "private result"})
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        public = client.get(f"/runs/{run_id}/tools/public-call/result", headers=headers)
        private = client.get(f"/runs/{run_id}/tools/internal-call/result", headers=headers)
        unauthorized = client.get(f"/runs/{run_id}/tools/public-call/result")
    assert public.status_code == 200
    assert public.json() == {"result": '{"content": "visible result"}'}
    assert private.status_code == 200
    assert private.json() == {"result": None}
    assert unauthorized.status_code == 401


def test_ambiguous_mcp_write_can_be_owner_reconciled_without_replay(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "mcp-reconcile-api.sqlite3")
    run_id = store.create_run(
        objective="Use MCP", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    assert token
    store.record_tool_intent(
        run_id,
        token,
        call_id="mcp-call",
        tool="mcp.call",
        version="1",
        arguments={"tool": "mcp_fixture_create", "arguments": {"secret": "hidden"}},
        idempotency_key="mcp-effect",
    )
    store.record_tool_result(
        run_id,
        token,
        "mcp-call",
        {"status": "unknown_outcome", "trust": "untrusted_mcp_output", "error": "timeout"},
    )
    store.release_lease(run_id, token)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        listed = client.get(f"/runs/{run_id}/mcp-reconciliations", headers=headers)
        refused = client.post(
            f"/runs/{run_id}/tools/mcp-call/mcp-reconcile",
            headers=headers,
            json={"resolution": "not_applied", "confirmed_external_effect_reviewed": False},
        )
        resolved = client.post(
            f"/runs/{run_id}/tools/mcp-call/mcp-reconcile",
            headers=headers,
            json={"resolution": "not_applied", "confirmed_external_effect_reviewed": True},
        )
        stale = client.post(
            f"/runs/{run_id}/tools/mcp-call/mcp-reconcile",
            headers=headers,
            json={"resolution": "applied", "confirmed_external_effect_reviewed": True},
        )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["call_id"] == "mcp-call"
    assert listed.json()[0]["tool"] == "mcp_fixture_create"
    assert listed.json()[0]["status"] == "unknown_outcome"
    assert "hidden" not in listed.text
    assert refused.status_code == 400
    assert resolved.status_code == 200 and resolved.json() == []
    assert stale.status_code == 409
    assert store.public_tool_result(run_id, "mcp-call") == (
        '{"status": "owner_confirmed_not_applied", '
        '"trust": "owner_reviewed_external_effect", "tool": "mcp_fixture_create", '
        '"instruction": "The owner inspected the external system and confirmed whether the effect '
        'occurred. Do not repeat the original call automatically."}'
    )
    assert any(
        event["event_type"] == "mcp.effect_reconciled" for event in store.list_events(run_id)
    )


def test_interrupted_workspace_write_can_be_owner_reconciled_after_state_check(
    workspace_tmp_path, monkeypatch
):
    from xaa.runtime.executor import WorkspaceExecutor

    store = RunStore(workspace_tmp_path / "workspace-write-reconcile-api.sqlite3")
    run_id = store.create_run(
        objective="Create a file",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    assert token
    store.transition(run_id, RunState.PLANNING, owner=token)
    store.transition(run_id, RunState.RUNNING, owner=token)
    store.record_tool_intent(
        run_id,
        token,
        call_id="create-call",
        tool="workspace.create_file",
        version="1",
        arguments={"path": "new.txt", "content": "private payload"},
        idempotency_key="create-call",
    )
    store.transition(run_id, RunState.BLOCKED, reason="tool_reconciliation_required", owner=token)
    store.release_lease(run_id, token)
    (workspace_tmp_path / "new.txt").write_bytes(b"")

    async def reconcile_in_process(
        _store, recovery_run_id, owner, call_id, _operation, arguments, **_kwargs
    ):
        return WorkspaceExecutor(_store).reconcile_write_not_applied(
            recovery_run_id, owner, arguments["provider_call_id"]
        )

    monkeypatch.setattr("xaa.api.app.execute_filesystem", reconcile_in_process)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        listed = client.get(f"/runs/{run_id}/workspace-write-reconciliations", headers=headers)
        refused = client.post(
            f"/runs/{run_id}/tools/create-call/workspace-write-reconcile",
            headers=headers,
            json={"confirmed_workspace_inspected": False},
        )
        unresolved = client.post(
            f"/runs/{run_id}/tools/create-call/workspace-write-reconcile",
            headers=headers,
            json={"confirmed_workspace_inspected": True},
        )
        (workspace_tmp_path / "new.txt").unlink()
        resolved = client.post(
            f"/runs/{run_id}/tools/create-call/workspace-write-reconcile",
            headers=headers,
            json={"confirmed_workspace_inspected": True},
        )
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "call_id": "create-call",
            "operation": "create",
            "path": "new.txt",
            "expected_sha256": None,
            "desired_sha256": hashlib.sha256(b"private payload").hexdigest(),
        }
    ]
    assert "private payload" not in listed.text
    assert refused.status_code == 400
    assert unresolved.status_code == 409
    assert resolved.status_code == 200 and resolved.json() == [], resolved.text
    assert store.get_run(run_id)["state"] == RunState.BLOCKED.value
    assert store.unresolved_tool_calls(run_id) == []
    assert (
        json.loads(store.public_tool_result(run_id, "create-call"))["effect"]
        == "not_applied_after_review"
    )
    assert not (workspace_tmp_path / "new.txt").exists()


def test_interrupted_reviewed_delete_can_be_owner_reconciled_after_restore(
    workspace_tmp_path, monkeypatch
):
    from xaa.runtime.executor import WorkspaceExecutor

    original = b"private deleted bytes"
    target = workspace_tmp_path / "file.txt"
    target.write_bytes(original)
    store = RunStore(workspace_tmp_path / "workspace-delete-reconcile-api.sqlite3")
    run_id = store.create_run(
        objective="Delete a file",
        acceptance_criteria=[],
        workspace=str(workspace_tmp_path),
        mode="workspace",
        model=None,
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    assert token
    store.transition(run_id, RunState.PLANNING, owner=token)
    store.transition(run_id, RunState.RUNNING, owner=token)
    expected = hashlib.sha256(original).hexdigest()
    store.record_tool_intent(
        run_id,
        token,
        call_id="delete-call",
        tool="workspace.delete_file",
        version="1",
        arguments={"path": "file.txt", "expected_sha256": expected},
        idempotency_key="delete-call",
        internal=True,
    )
    store.append_worker_event(
        run_id, token, "tool.executing", {"call_id": "delete-call", "internal": True}
    )
    store.transition(run_id, RunState.BLOCKED, reason="tool_reconciliation_required", owner=token)
    store.release_lease(run_id, token)
    target.unlink()

    async def reconcile_in_process(
        _store, recovery_run_id, owner, _worker_call_id, _operation, arguments, **_kwargs
    ):
        return WorkspaceExecutor(_store).reconcile_write_not_applied(
            recovery_run_id, owner, arguments["provider_call_id"]
        )

    monkeypatch.setattr("xaa.api.app.execute_filesystem", reconcile_in_process)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        listed = client.get(f"/runs/{run_id}/workspace-write-reconciliations", headers=headers)
        refused = client.post(
            f"/runs/{run_id}/tools/delete-call/workspace-write-reconcile",
            headers=headers,
            json={"confirmed_workspace_inspected": False},
        )
        unresolved = client.post(
            f"/runs/{run_id}/tools/delete-call/workspace-write-reconcile",
            headers=headers,
            json={"confirmed_workspace_inspected": True},
        )
        target.write_bytes(original)
        resolved = client.post(
            f"/runs/{run_id}/tools/delete-call/workspace-write-reconcile",
            headers=headers,
            json={"confirmed_workspace_inspected": True},
        )
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "call_id": "delete-call",
            "operation": "delete",
            "path": "file.txt",
            "expected_sha256": expected,
            "desired_sha256": expected,
        }
    ]
    assert "private deleted bytes" not in listed.text
    assert refused.status_code == 400
    assert unresolved.status_code == 409
    assert resolved.status_code == 200 and resolved.json() == [], resolved.text
    assert store.get_run(run_id)["state"] == RunState.BLOCKED.value
    assert store.unresolved_tool_calls(run_id) == []
    assert target.read_bytes() == original
    failed = next(
        event
        for event in store.list_events(run_id)
        if event["event_type"] == "tool.failed" and event["payload"]["call_id"] == "delete-call"
    )
    assert failed["payload"]["internal"] is True
    assert store.public_tool_result(run_id, "delete-call") is None


class WaitingProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()
        self.stopped = threading.Event()

    async def chat(self, **kwargs):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped.set()


def test_api_dispatches_and_pauses_active_worker(workspace_tmp_path):
    provider = WaitingProvider()
    store = RunStore(workspace_tmp_path / "dispatch.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(provider, SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/runs", headers=headers, json={"objective": "Wait", "model": "fixture/model"}
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert provider.started.wait(2)
        paused = client.post(f"/runs/{run_id}/pause", headers=headers)
        assert paused.status_code == 200
        assert paused.json()["state"] == "paused"
        assert provider.stopped.wait(2)
        assert store.get_run(run_id)["lease_owner"] is None


def test_api_requires_authentication(workspace_tmp_path) -> None:
    client = TestClient(
        create_app(
            FakeProvider(), SecretStr(TOKEN), store=RunStore(workspace_tmp_path / "api.sqlite3")
        )
    )
    assert client.get("/models").status_code == 401
    assert (
        client.get("/models", headers={"Authorization": f"Bearer {TOKEN}"}).json()[0]["id"]
        == "fixture/model"
    )


def test_authenticated_backup_endpoint_returns_verified_artifact_bundle(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "backup.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.post("/backups", headers=headers)
    assert response.status_code == 201
    bundle = response.json()["path"]
    assert bundle.endswith(".xaa-backup")
    with zipfile.ZipFile(bundle) as archive:
        assert archive.testzip() is None
        assert {"manifest.json", "xaa.sqlite3"}.issubset(archive.namelist())


def test_authenticated_backup_restore_creates_a_separate_verified_profile(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "backup-restore.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    destination_parent = workspace_tmp_path / "restored profiles"
    destination_parent.mkdir()
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        backup = client.post("/backups", headers=headers)
        assert backup.status_code == 201
        response = client.post(
            "/backups/restore",
            headers=headers,
            json={
                "bundle_path": backup.json()["path"],
                "destination_parent": str(destination_parent),
            },
        )
    assert response.status_code == 201
    restored = response.json()
    profile_root = Path(restored["data_root"])
    database = Path(restored["database"])
    assert restored["status"] == "verified"
    assert profile_root.parent == destination_parent
    assert database == profile_root / "XAA" / "xaa.sqlite3"
    assert database.is_file()
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
    assert store.path.exists()


def test_raw_chat_stream_endpoint_is_removed(workspace_tmp_path) -> None:
    client = TestClient(
        create_app(
            FakeProvider(), SecretStr(TOKEN), store=RunStore(workspace_tmp_path / "api.sqlite3")
        )
    )
    response = client.post(
        "/chat/stream",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"model": "fixture/model", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 404


def test_api_rejects_short_token() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        create_app(FakeProvider(), SecretStr("short"))


def test_memory_controls_are_authenticated_and_remove_search_results(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "memory-api.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        assert client.get("/memory").status_code == 401
        assert (
            client.post(
                "/memory", headers=headers, json={"content": "Python", "provenance": "system"}
            ).status_code
            == 422
        )
        response = client.post(
            "/memory", headers=headers, json={"content": "Prefer Python examples"}
        )
        assert response.status_code == 201
        memory_id = response.json()["id"]
        assert response.json()["provenance"] == "user"
        assert client.get("/memory?q=Python", headers=headers).json()
        assert (
            client.post(
                f"/memory/{memory_id}",
                headers=headers,
                json={"content": "Prefer TypeScript examples"},
            ).status_code
            == 200
        )
        assert client.get("/memory?q=Python", headers=headers).json() == []
        assert len(client.get("/memory/export", headers=headers).json()) == 1
        assert client.delete(f"/memory/{memory_id}", headers=headers).status_code == 204
        assert client.get("/memory?q=TypeScript", headers=headers).json() == []


def test_run_client_request_id_is_idempotent_and_rejects_payload_reuse(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "run-idempotency.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = {
        "objective": "Inspect the fixture",
        "acceptance_criteria": ["Report the observation"],
        "workspace": None,
        "mode": "observe",
        "model": None,
        "client_request_id": "batch:00112233445566778899aabbccddeeff:case1",
    }
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        first = client.post("/runs", headers=headers, json=payload)
        replay = client.post("/runs", headers=headers, json=payload)
        assert first.status_code == replay.status_code == 201
        assert first.json()["id"] == replay.json()["id"]
        changed = client.post(
            "/runs", headers=headers, json={**payload, "objective": "Different objective"}
        )
        assert changed.status_code == 409
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_run_client_request_id_requires_safe_key(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "run-request-key.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    payload = {
        "objective": "Inspect the fixture",
        "model": None,
        "mode": "observe",
        "client_request_id": "../unsafe",
    }
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        assert client.post("/runs", headers=headers, json=payload).status_code == 422


def test_sourced_knowledge_api_is_authenticated_editable_and_exported(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "sourced-memory-api.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        assert (
            client.post(
                "/memory/knowledge", json={"content": "WAL", "source_uri": "manual"}
            ).status_code
            == 401
        )
        response = client.post(
            "/memory/knowledge",
            headers=headers,
            json={
                "content": "WAL permits concurrent readers",
                "source_uri": "https://sqlite.org/wal.html",
            },
        )
        assert response.status_code == 201
        item = response.json()
        assert item["kind"] == "knowledge"
        assert item["provenance"] == "user_source"
        assert item["source_uri"] == "https://sqlite.org/wal.html"
        assert client.get("/memory?q=concurrent", headers=headers).json()[0]["id"] == item["id"]
        updated = client.put(
            f"/memory/knowledge/{item['id']}",
            headers=headers,
            json={
                "content": "WAL allows concurrent readers",
                "source_uri": "SQLite WAL documentation",
            },
        )
        assert updated.status_code == 200
        assert updated.json()["source_uri"] == "SQLite WAL documentation"
        exported = client.get("/memory/export", headers=headers).json()
        assert len(exported) == 1 and exported[0]["source_uri"] == "SQLite WAL documentation"
        assert client.delete(f"/memory/{item['id']}", headers=headers).status_code == 204
        assert client.get("/memory?q=concurrent", headers=headers).json() == []


def test_artifact_download_requires_auth_and_rejects_tampering(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "artifacts.sqlite3")
    run_id = store.create_run(
        objective="Inspect", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )["id"]
    token = store.acquire_lease(run_id, "worker")
    root = workspace_tmp_path / "artifacts"
    root.mkdir()
    artifact = root / "output.txt"
    artifact.write_bytes(b"verified output")
    metadata = store.register_artifact(run_id, token, artifact.name, provenance="test")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        route = f"/artifacts/{metadata['id']}"
        assert client.get(route).status_code == 401
        assert client.get(route, headers=headers).content == b"verified output"
        assert (
            client.get(f"/runs/{run_id}/artifacts", headers=headers).json()[0]["id"]
            == metadata["id"]
        )
        png = root / "screenshot.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        image = store.register_artifact(
            run_id, token, png.name, provenance="browser.screenshot", media_type="image/png"
        )
        image_response = client.get(f"/artifacts/{image['id']}", headers=headers)
        assert image_response.content == png.read_bytes()
        assert image_response.headers["content-type"] == "image/png"
        assert image["media_type"] == "image/png"
        artifact.write_bytes(b"tampered")
        assert client.get(route, headers=headers).status_code == 409


def test_run_image_is_validated_stored_as_artifact_and_reconstructed(workspace_tmp_path):
    import base64

    store = RunStore(workspace_tmp_path / "run-image.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    png = b"\x89PNG\r\n\x1a\nfixture-image"
    encoded = "data:image/png;base64," + base64.b64encode(png).decode("ascii")

    class VisionProvider(FakeProvider):
        async def models(self) -> list[ModelInfo]:
            return [ModelInfo(id="fixture", capabilities=ModelCapabilities(vision=True))]

    with TestClient(create_app(VisionProvider(), SecretStr(TOKEN), store=store)) as client:
        bad = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Inspect",
                "model": "fixture",
                "image": "data:image/jpeg;base64,SGVsbG8=",
            },
        )
        assert bad.status_code == 409
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Describe this image", "model": "fixture", "image": encoded},
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert store.image_data_url(run_id) == encoded
        artifacts = client.get(f"/runs/{run_id}/artifacts", headers=headers).json()
        assert len(artifacts) == 1
        assert artifacts[0]["media_type"] == "image/png"
        event = next(
            item
            for item in store.public_events(run_id)
            if item["event_type"] == "run.image_attached"
        )
        assert set(event["payload"]) == {"artifact_id", "media_type"}
        metadata = store.get_artifact(artifacts[0]["id"])
        assert metadata
        (workspace_tmp_path / "artifacts" / metadata["storage_key"]).write_bytes(png + b"tamper")
        with pytest.raises(ValueError, match="integrity"):
            store.image_data_url(run_id)

    nonvision_store = RunStore(workspace_tmp_path / "no-vision.sqlite3")
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=nonvision_store)) as client:
        rejected = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Describe image", "model": "fixture/model", "image": encoded},
        )
        assert rejected.status_code == 409


def test_run_text_document_is_extracted_and_persisted_for_recovery(workspace_tmp_path):
    import base64

    store = RunStore(workspace_tmp_path / "run-document.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    source = b"# Field notes\nThe sample valve is marked blue."
    encoded = "data:text/markdown;base64," + base64.b64encode(source).decode("ascii")
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Summarize this attachment", "document": encoded},
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert store.document_text(run_id) == source.decode("utf-8")
        artifacts = client.get(f"/runs/{run_id}/artifacts", headers=headers).json()
        assert {item["provenance"] for item in artifacts} == {
            "user.input_document",
            "user.input_document_text",
        }
        original = next(item for item in artifacts if item["provenance"] == "user.input_document")
        downloaded = client.get(f"/artifacts/{original['id']}", headers=headers)
        assert downloaded.content == source
        assert downloaded.headers["content-type"].split(";", 1)[0] == "text/markdown"
        with store._connection() as connection:
            connection.execute(
                "UPDATE artifacts SET sha256=? WHERE run_id=? AND provenance='user.input_document_text'",
                ("0" * 64, run_id),
            )
        with pytest.raises(ValueError, match="integrity"):
            store.document_text(run_id)


def test_run_docx_is_extracted_and_original_artifact_is_downloadable(workspace_tmp_path):
    import base64
    from io import BytesIO
    from zipfile import ZIP_DEFLATED, ZipFile

    media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            "<w:body><w:p><w:r><w:t>DOCX-API-MARKER-7E93</w:t></w:r></w:p></w:body></w:document>",
        )
    source = output.getvalue()
    encoded = f"data:{media_type};base64," + base64.b64encode(source).decode("ascii")
    store = RunStore(workspace_tmp_path / "run-docx.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Report the DOCX marker", "document": encoded},
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert "DOCX-API-MARKER-7E93" in store.document_text(run_id)
        artifacts = client.get(f"/runs/{run_id}/artifacts", headers=headers).json()
        original = next(item for item in artifacts if item["provenance"] == "user.input_document")
        downloaded = client.get(f"/artifacts/{original['id']}", headers=headers)
        assert downloaded.content == source
        assert downloaded.headers["content-type"].split(";", 1)[0] == media_type


def test_scanned_pdf_requires_vision_and_retains_page_preview_artifact(workspace_tmp_path):
    import base64
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    source = output.getvalue()
    encoded = "data:application/pdf;base64," + base64.b64encode(source).decode("ascii")
    headers = {"Authorization": f"Bearer {TOKEN}"}

    class VisionProvider(FakeProvider):
        ocr_calls = 0

        async def models(self) -> list[ModelInfo]:
            return [ModelInfo(id="fixture", capabilities=ModelCapabilities(vision=True))]

        async def transcribe_scanned_pages(self, *, model, objective, pages):
            assert model == "fixture"
            assert objective == "Read the scan"
            assert len(pages) == 1
            self.ocr_calls += 1
            return "[Scanned PDF OCR — page 1]\nfixture OCR", [
                (
                    "8bd0368a-c3f8-4ca5-aa58-0f6b0eef2676",
                    {
                        "prompt_tokens": 80,
                        "completion_tokens": 4,
                        "total_tokens": 84,
                    },
                )
            ]

    nonvision_store = RunStore(workspace_tmp_path / "scanned-no-vision.sqlite3")
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=nonvision_store)) as client:
        rejected = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Read the scan", "model": "fixture/model", "document": encoded},
        )
        assert rejected.status_code == 409
        assert "vision support" in rejected.json()["detail"]

    store = RunStore(workspace_tmp_path / "scanned-vision.sqlite3")
    vision_provider = VisionProvider()
    with TestClient(create_app(vision_provider, SecretStr(TOKEN), store=store)) as client:
        too_small = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Read the scan",
                "model": "fixture",
                "max_total_tokens": 4607,
                "document": encoded,
            },
        )
        assert too_small.status_code == 409
        assert "at least 4608 tokens" in too_small.json()["detail"]
        assert vision_provider.ocr_calls == 0
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Read the scan", "model": "fixture", "document": encoded},
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert "no embedded text" in store.document_text(run_id)
        assert "fixture OCR" in store.document_text(run_id)
        assert vision_provider.ocr_calls == 1
        usage = store.token_usage(run_id)
        assert usage["prompt_tokens"] == 80
        assert usage["completion_tokens"] == 4
        assert usage["total_tokens"] == 84
        pages = store.document_page_images(run_id)
        assert len(pages) == 1 and pages[0][0] == 1
        page_bytes = base64.b64decode(pages[0][1].split(",", 1)[1])
        assert page_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        artifacts = client.get(f"/runs/{run_id}/artifacts", headers=headers).json()
        assert {item["provenance"] for item in artifacts} == {
            "user.input_document",
            "user.input_document_text",
            "user.input_document_page:1",
        }
        source_record = next(
            item for item in artifacts if item["provenance"] == "user.input_document"
        )
        assert client.get(f"/artifacts/{source_record['id']}", headers=headers).content == source


def test_run_xlsx_extracts_cells_and_retains_original_artifact(workspace_tmp_path):
    import base64
    from io import BytesIO
    from zipfile import ZIP_DEFLATED, ZipFile

    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId0" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            'Target="xl/workbook.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            'Target="worksheets/sheet1.xml"/></Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>XLSX-API-MARKER-361A</t>'
            '</is></c><c r="B1"><f>1+1</f><v>2</v></c>'
            "</row></sheetData></worksheet>",
        )
    source = output.getvalue()
    encoded = f"data:{media_type};base64," + base64.b64encode(source).decode("ascii")
    store = RunStore(workspace_tmp_path / "run-xlsx.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Report the XLSX marker", "document": encoded},
        )
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert "XLSX-API-MARKER-361A" in store.document_text(run_id)
        assert "B1=2 [formula: =1+1]" in store.document_text(run_id)
        artifacts = client.get(f"/runs/{run_id}/artifacts", headers=headers).json()
        original = next(item for item in artifacts if item["provenance"] == "user.input_document")
        downloaded = client.get(f"/artifacts/{original['id']}", headers=headers)
        assert downloaded.content == source
        assert downloaded.headers["content-type"].split(";", 1)[0] == media_type


def test_shutdown_pauses_worker_before_signalling_server(workspace_tmp_path):
    provider = WaitingProvider()
    store = RunStore(workspace_tmp_path / "shutdown.sqlite3")
    stopped = threading.Event()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(provider, SecretStr(TOKEN), store=store, shutdown_callback=stopped.set)
    ) as client:
        assert client.post("/shutdown").status_code == 401
        run_id = client.post(
            "/runs", headers=headers, json={"objective": "Wait", "model": "fixture"}
        ).json()["id"]
        assert provider.started.wait(2)
        assert client.post("/shutdown", headers=headers).json()["status"] == "stopping"
        assert stopped.is_set()
        assert provider.stopped.is_set()
        assert store.get_run(run_id)["state"] == "paused"
        assert store.get_run(run_id)["lease_owner"] is None


def test_public_run_record_does_not_expose_worker_lease(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "private-lease.sqlite3")
    run = store.create_run(
        objective="Inspect", acceptance_criteria=[], workspace=None, mode="observe", model=None
    )
    token = store.acquire_lease(run["id"], "worker")
    assert token
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.get(f"/runs/{run['id']}", headers={"Authorization": f"Bearer {TOKEN}"})
        assert response.status_code == 200
        assert token not in response.text
        assert "lease_owner" not in response.json()
        assert response.json()["limits"]["max_iterations"] == 8


def test_run_trajectory_export_is_authenticated_provenance_rich_and_redacted(workspace_tmp_path):
    secret = "fixture-secret-value-1234"

    class Credentials:
        def get(self, profile="lm-studio"):
            return secret if profile == "brave-search" else None

        def set(self, _token, _profile="lm-studio"):
            return None

        def delete(self, _profile="lm-studio"):
            return None

    store = RunStore(workspace_tmp_path / "trajectory.sqlite3")
    run = store.create_run(
        objective="Check redacted export",
        acceptance_criteria=["secret absent"],
        workspace=None,
        mode="observe",
        model="fixture/model",
    )
    with store.connection() as connection:
        RunStore.record_event(
            connection,
            run["id"],
            "model.message",
            {
                "message": {
                    "role": "assistant",
                    "content": f"stored {secret}; Bearer abc.def; api_key=inline-secret",
                }
            },
        )
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(
            FakeProvider(),
            SecretStr(TOKEN),
            store=store,
            credential_store=Credentials(),
        )
    ) as client:
        route = f"/runs/{run['id']}/export"
        assert client.get(route).status_code == 401
        response = client.get(route, headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment;" in response.headers["content-disposition"]
    assert secret.encode() not in response.content
    exported = response.json()
    assert exported["format"] == "xaa-trajectory"
    assert exported["run"]["id"] == run["id"]
    message_event = next(
        event for event in exported["events"] if event["event_type"] == "model.message"
    )
    event_text = message_event["payload"]["message"]["content"]
    assert "[REDACTED]" in event_text
    assert "inline-secret" not in event_text
    assert "abc.def" not in event_text


def test_run_trajectory_export_enforces_event_and_byte_limits(workspace_tmp_path, monkeypatch):
    from xaa.storage import trajectory

    store = RunStore(workspace_tmp_path / "trajectory-limits.sqlite3")
    run = store.create_run(
        objective="Bound export",
        acceptance_criteria=[],
        workspace=None,
        mode="observe",
        model=None,
    )
    monkeypatch.setattr(trajectory, "MAX_TRAJECTORY_EVENTS", 0)
    with pytest.raises(ValueError, match="event export limit"):
        trajectory.export_trajectory(store, run["id"])
    monkeypatch.setattr(trajectory, "MAX_TRAJECTORY_EVENTS", 10)
    monkeypatch.setattr(trajectory, "MAX_TRAJECTORY_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        trajectory.export_trajectory(store, run["id"])


def test_run_web_host_grant_is_authenticated_validated_and_durable(workspace_tmp_path):
    database = workspace_tmp_path / "web-grant.sqlite3"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(database))
    ) as client:
        created = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Read the docs",
                "approved_web_hosts": ["Docs.Example.com."],
                "approved_web_search": True,
                "approved_browser": True,
            },
        )
        assert created.status_code == 201
        run_id = created.json()["id"]
        assert created.json()["approved_web_hosts"] == ["docs.example.com"]
        assert created.json()["approved_web_search"] is True
        assert created.json()["approved_browser"] is True
        events = client.get(f"/runs/{run_id}/events", headers=headers).json()
        assert events[-1]["event_type"] == "web.access_granted"
        assert events[-1]["payload"] == {
            "hosts": ["docs.example.com"],
            "search": True,
            "browser": True,
            "actor": "local_user",
        }
        invalid = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Bad", "approved_web_hosts": ["https://example.com/path"]},
        )
        assert invalid.status_code == 422
        assert client.get("/runs", headers=headers).status_code == 200

    recovered = RunStore(database).get_run(run_id)
    assert recovered is not None
    assert recovered["approved_web_hosts"] == ["docs.example.com"]
    assert recovered["approved_web_search"] is True
    assert recovered["approved_browser"] is True


def test_run_can_require_pre_inference_token_reservations(workspace_tmp_path):
    database = workspace_tmp_path / "reserved-budget.sqlite3"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(database))
    ) as client:
        invalid = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Invalid", "pre_inference_token_reservations": True},
        )
        assert invalid.status_code == 409
        assert "require a total token threshold" in invalid.json()["detail"]
        created = client.post(
            "/runs",
            headers=headers,
            json={
                "objective": "Count before inference",
                "model": "fixture/model",
                "max_total_tokens": 4000,
                "max_output_tokens": 256,
                "pre_inference_token_reservations": True,
            },
        )
        assert created.status_code == 201
        run_id = created.json()["id"]
        assert created.json()["limits"]["pre_inference_token_reservations"] is True
        queued = client.get(f"/runs/{run_id}/events", headers=headers).json()[0]
        assert queued["payload"]["pre_inference_token_reservations"] is True
    reopened = RunStore(database).get_run(run_id)
    assert reopened["limits"]["pre_inference_token_reservations"] is True


def test_failed_tool_diagnostic_is_reported_without_enabling_tools(workspace_tmp_path):
    class UnsupportedProvider(FakeProvider):
        async def diagnose_tools(self, model, *, request=None):
            raise ProviderError("Selected model did not return a structured tool call.")

    store = RunStore(workspace_tmp_path / "diagnostics.sqlite3")
    with TestClient(create_app(UnsupportedProvider(), SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/providers/lm-studio/diagnostics",
            headers={"Authorization": f"Bearer {TOKEN}"},
            json={"model": "fixture"},
        )
        assert response.status_code == 422
        assert "structured tool call" in response.json()["detail"]


def test_durable_event_stream_reconnects_from_last_event_id(workspace_tmp_path):
    store = RunStore(workspace_tmp_path / "events.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(FakeProvider(), SecretStr(TOKEN), store=store)) as client:
        run_id = client.post("/runs", headers=headers, json={"objective": "Observe"}).json()["id"]
        client.post(f"/runs/{run_id}/cancel", headers=headers)
        response = client.get(
            f"/runs/{run_id}/events/stream", headers={**headers, "Last-Event-ID": "1"}
        )
        assert response.status_code == 200
        assert "id: 1\n" not in response.text
        assert "id: 2\nevent: run_event" in response.text
        assert "cancelled" in response.text
        assert "event: done" in response.text


def test_run_api_replays_durable_events_after_restart(workspace_tmp_path) -> None:
    database = workspace_tmp_path / "api.sqlite3"
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(
        create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(database))
    ) as client:
        response = client.post("/runs", headers=headers, json={"objective": "Do a safe task"})
        assert response.status_code == 201
        run_id = response.json()["id"]
        assert response.json()["state"] == "queued"
        assert client.post(f"/runs/{run_id}/pause", headers=headers).json()["state"] == "paused"
        assert client.get(f"/runs/{run_id}/events", headers=headers).json()[-1]["sequence"] == 2

    with TestClient(
        create_app(FakeProvider(), SecretStr(TOKEN), store=RunStore(database))
    ) as recovered:
        assert recovered.get(f"/runs/{run_id}", headers=headers).json()["state"] == "paused"
        assert recovered.post(f"/runs/{run_id}/resume", headers=headers).json()["state"] == "queued"


def test_token_settings_survive_public_create_read_and_control(workspace_tmp_path):
    provider = WaitingProvider()
    store = RunStore(workspace_tmp_path / "token-contract.sqlite3")
    headers = {"Authorization": f"Bearer {TOKEN}"}
    expected = {
        "max_iterations": 8,
        "max_seconds": 900,
        "max_total_tokens": 5000,
        "max_output_tokens": 128,
        "pre_inference_token_reservations": False,
    }
    with TestClient(create_app(provider, SecretStr(TOKEN), store=store)) as client:
        response = client.post(
            "/runs",
            headers=headers,
            json={"objective": "Wait", "model": "fixture/model", **expected},
        )
        assert response.status_code == 201
        identity = response.json()["id"]
        assert response.json()["limits"] == expected
        assert provider.started.wait(2)
        assert client.get(f"/runs/{identity}", headers=headers).json()["limits"] == expected
        assert client.post(f"/runs/{identity}/pause", headers=headers).json()["limits"] == expected
        for key in ["max_total_tokens", "max_output_tokens"]:
            invalid = client.post(
                "/runs",
                headers=headers,
                json={"objective": "Invalid", "model": "fixture/model", key: True},
            )
            assert invalid.status_code == 422


def test_authenticated_token_count_contract_and_safe_failure(workspace_tmp_path):
    from xaa.providers.tokenization import PromptCount, PromptCountError

    class Counter(FakeProvider):
        async def count_prompt(self, request):
            if request.model == "missing":
                raise PromptCountError("Select an already-loaded model.")
            assert request.messages[0].content == "Count this fixture"
            return PromptCount(
                model=request.model, prompt_tokens=12, context_length=4096, sdk_version="fixture"
            )

    store = RunStore(workspace_tmp_path / "count-api.sqlite3")
    client = TestClient(create_app(Counter(), SecretStr(TOKEN), store=store))
    body = {
        "model": "loaded-instance",
        "messages": [{"role": "user", "content": "Count this fixture"}],
    }
    path = "/providers/lm-studio/token-count"
    assert client.post(path, json=body).status_code == 401
    headers = {"Authorization": f"Bearer {TOKEN}"}
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "model": "loaded-instance",
        "prompt_tokens": 12,
        "context_length": 4096,
        "sdk_version": "fixture",
    }
    failed = client.post(path, json={**body, "model": "missing"}, headers=headers)
    assert failed.status_code == 422
    assert "Count this fixture" not in failed.text
    assert (
        client.post(path, json={**body, "api_token": "not-allowed"}, headers=headers).status_code
        == 422
    )

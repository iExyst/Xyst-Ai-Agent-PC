import hashlib
import json

import pytest
from xaa.providers.lm_studio import LMStudioProvider
from xaa.runtime.command_controller import CommandController
from xaa.runtime.command_recovery import StoppedCommandRecovery
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.states import RunState
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.commands import CommandJournal, CommandOutcome
from xaa.storage.runs import RunStore


def stopped(root, *, model=None, proposal=False, launched=False):
    project = root / "project"
    project.mkdir()
    store = RunStore(root / "data" / "db.sqlite3")
    run = store.create_run(
        objective="Interrupted command",
        acceptance_criteria=[],
        workspace=str(project),
        mode="workspace",
        model=model,
        command_grant={"distribution": "Ubuntu", "profile_sha256": "a" * 64},
    )
    token = store.acquire_lease(run["id"], "lost-worker")
    store.transition(run["id"], RunState.PLANNING, owner=token)
    store.transition(run["id"], RunState.RUNNING, owner=token)
    if proposal:
        store.append_worker_event(
            run["id"],
            token,
            "model.message",
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "command",
                            "type": "function",
                            "function": {
                                "name": "workspace_run_command",
                                "arguments": '{"argv":["fixture"]}',
                            },
                        }
                    ],
                }
            },
        )
    intent = CommandJournal(store).reserve(
        run["id"], token, "command", {"argv": ["fixture"]}, idempotency_key="command"
    )
    if launched:
        store.append_worker_event(
            run["id"],
            token,
            "execution.snapshot",
            {
                "call_id": "command",
                "execution_id": intent.execution_id,
                "sha256": "b" * 64,
            },
        )
        store.append_worker_event(
            run["id"],
            token,
            "tool.executing",
            {"call_id": "command", "execution_id": intent.execution_id},
        )
    with store.connection() as connection:
        connection.execute(
            "UPDATE runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (run["id"],)
        )
    assert store.recover_abandoned_runs() == ([run["id"]] if launched else [])
    return store, run["id"]


def test_launched_command_is_dispatched_for_journal_reconciliation(workspace_tmp_path):
    store, run_id = stopped(workspace_tmp_path, launched=True)

    assert store.get_run(run_id)["state"] == "queued"
    assert store.unresolved_tool_calls(run_id)[0]["id"] == "command"


async def test_model_free_recovery_stops_before_inference(workspace_tmp_path, monkeypatch):
    store, run_id = stopped(workspace_tmp_path, launched=True)
    recovered = False
    unresolved = store.unresolved_tool_calls

    async def reconcile(_run_id, _token):
        nonlocal recovered
        recovered = True
        return ["command"]

    def unresolved_after_recovery(_run_id):
        return [] if recovered else unresolved(_run_id)

    class Provider(LMStudioProvider):
        async def chat(self, **_kwargs):
            raise AssertionError("Model-free command recovery must not request inference.")

    monkeypatch.setattr(store, "unresolved_tool_calls", unresolved_after_recovery)
    provider = Provider()
    coordinator = Coordinator(store, provider)
    monkeypatch.setattr(coordinator, "_recover_commands", reconcile)
    try:
        await coordinator.execute(run_id)
    finally:
        await provider.close()

    assert recovered
    assert store.get_run(run_id)["state"] == "paused"
    assert (
        store.list_events(run_id)[-1]["payload"]["reason"]
        == "command_recovery_complete_model_not_selected"
    )


def test_recovery_can_complete_a_journal_while_run_is_planning(workspace_tmp_path):
    store, run_id = stopped(workspace_tmp_path, launched=True)
    owner = store.acquire_lease(run_id, "recovery-owner")
    assert owner is not None
    store.transition(run_id, RunState.PLANNING, owner=owner)
    journal = CommandJournal(store)
    intent = journal.recovery_intent(run_id, owner, "command")
    controller = CommandController(store, Backend())
    stdout = b"verified\n"
    stderr = b"completed\n"
    stdout_id = controller.artifact(run_id, owner, intent.execution_id, "stdout", stdout)
    stderr_id = controller.artifact(run_id, owner, intent.execution_id, "stderr", stderr)
    outcome = CommandOutcome(
        execution_id=intent.execution_id,
        snapshot_sha256="b" * 64,
        filter_sha256="c" * 64,
        termination_reason="finished",
        exit_code=0,
        supervisor_exit_code=0,
        succeeded=True,
        output_complete=True,
        cleanup_confirmed=True,
        stdout_artifact_id=stdout_id,
        stderr_artifact_id=stderr_id,
        stdout_sha256=hashlib.sha256(stdout).hexdigest(),
        stderr_sha256=hashlib.sha256(stderr).hexdigest(),
        stdout_bytes=len(stdout),
        stderr_bytes=len(stderr),
        elapsed_seconds=0.1,
    )

    result = journal.complete(run_id, owner, "command", outcome, recovery=True)

    assert result["result_reconciled"] is True
    assert store.unresolved_tool_calls(run_id) == []
    assert any(event["event_type"] == "execution.completed" for event in store.list_events(run_id))


class Backend:
    def __init__(self, result=False, on_cleanup=None):
        self.result = result
        self.on_cleanup = on_cleanup
        self.calls = []

    async def control(self, action, intent, *, deadline):
        self.calls.append(action)
        if action == "cleanup" and self.on_cleanup:
            self.on_cleanup()
        return {
            "execution_id": intent.execution_id,
            "cleanup_confirmed": True,
            "closed": True,
            "manager_alive": False,
            "outcome": {"state": "finished", "result": {}} if self.result else None,
        }


@pytest.mark.parametrize("available", [False, True])
async def test_stopped_discovery_cleans_without_restarting_run(workspace_tmp_path, available):
    store, run_id = stopped(workspace_tmp_path)
    revision = store.get_run(run_id)["revision"]
    backend = Backend(result=available)

    async def discover(intent):
        assert intent.distribution == "Ubuntu"
        return backend

    recovery = StoppedCommandRecovery(store, discover)
    assert await recovery.run_once() == 1
    assert backend.calls == ["cleanup", "status"]
    assert store.get_run(run_id)["state"] == "blocked"
    assert store.get_run(run_id)["revision"] == revision
    assert bool(store.unresolved_tool_calls(run_id)) == available
    assert CommandJournal(store).stopped_candidates() == []
    assert await recovery.run_once() == 0
    assert len(store.list_artifacts(run_id)) == 1


async def test_backend_failure_retains_slot_and_backs_off(workspace_tmp_path):
    store, run_id = stopped(workspace_tmp_path)
    calls = []

    async def unavailable(intent):
        calls.append(intent.distribution)
        raise OSError("diagnostic backend unavailable")

    recovery = StoppedCommandRecovery(store, unavailable)
    assert await recovery.run_once() == 0
    assert await recovery.run_once() == 0
    assert calls == ["Ubuntu"]
    assert len(store.unresolved_tool_calls(run_id)) == 1
    assert not store.list_artifacts(run_id)


async def test_resume_race_cannot_commit_stale_stopped_observation(workspace_tmp_path):
    store, run_id = stopped(workspace_tmp_path)
    backend = Backend(on_cleanup=lambda: store.transition(run_id, RunState.QUEUED))

    async def discover(intent):
        assert intent.distribution == "Ubuntu"
        return backend

    assert await StoppedCommandRecovery(store, discover).run_once() == 0
    assert store.get_run(run_id)["state"] == "queued"
    assert len(store.unresolved_tool_calls(run_id)) == 1
    assert not store.list_artifacts(run_id)


def test_resumed_or_owned_run_is_not_a_stopped_candidate(workspace_tmp_path):
    store, run_id = stopped(workspace_tmp_path)
    prior = CommandJournal(store).stopped_candidates()[0]
    store.transition(run_id, RunState.QUEUED)
    assert CommandJournal(store).stopped_candidates() == []
    token = store.acquire_lease(run_id, "new-owner")
    assert token is not None
    assert CommandJournal(store).stopped_candidates() == []
    with pytest.raises(RuntimeError):
        CommandJournal(store).stopped_intent(run_id, prior["revision"], "command")


@pytest.mark.parametrize("unavailable", [False, True])
async def test_resume_recovers_tool_reply_before_provider_inference(
    workspace_tmp_path, monkeypatch, unavailable
):
    store, run_id = stopped(workspace_tmp_path, model="fixture", proposal=True)
    store.transition(run_id, RunState.QUEUED)
    backend = Backend()

    async def discover(distribution, *, deadline):
        if unavailable:
            raise OSError("fixture backend unavailable")
        return backend

    monkeypatch.setattr(WSLTransport, "discover", discover)

    class Provider(LMStudioProvider):
        diagnosed = 0
        requests = 0

        async def diagnose_tools(self, model, *, request=None):
            self.diagnosed += 1
            return True

        async def chat(self, *, model, messages, tools=None, **kwargs):
            self.requests += 1
            reply = next(message for message in messages if message["role"] == "tool")
            assert reply["tool_call_id"] == "command"
            assert json.loads(reply["content"])["verification"] == "not_verified"
            assert messages[-1]["role"] == "user" and "not_verified" in messages[-1]["content"]
            # Explicit grants remain available after reconciliation; recovery itself
            # never dispatches the tool or silently replays the previous call.
            assert any(tool["function"]["name"] == "workspace_run_command" for tool in tools)
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "The interrupted command was not verified.",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

    provider = Provider()
    try:
        await Coordinator(RunStore(store.path), provider).execute(run_id)
    finally:
        await provider.close()
    if unavailable:
        assert store.get_run(run_id)["state"] == "blocked"
        assert provider.diagnosed == provider.requests == 0
        assert len(store.unresolved_tool_calls(run_id)) == 1
    else:
        assert store.get_run(run_id)["state"] == "verifying"
        assert provider.requests == 1
        replies = [
            message for message in store.conversation_messages(run_id) if message["role"] == "tool"
        ]
        assert len(replies) == 1
        assert not store.unresolved_tool_calls(run_id)
        store.transition(run_id, RunState.PAUSED)
        store.transition(run_id, RunState.QUEUED)
        await Coordinator(store, provider).execute(run_id)
        assert provider.requests == 1
        assert (
            sum(
                event["event_type"] == "execution.resume_feedback"
                for event in store.list_events(run_id)
            )
            == 1
        )

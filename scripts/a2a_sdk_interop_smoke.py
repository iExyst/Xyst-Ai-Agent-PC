"""Exercise the local A2A endpoint with the official A2A Python client SDK."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import uvicorn
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.credentials import CredentialStore
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.storage.runs import RunStore

OWNER_TOKEN = "a2a-sdk-owner-token-for-local-acceptance"
PEER_TOKEN = "a2a-sdk-peer-token-for-local-acceptance"


class _Credentials:
    def get(self, profile: str = "lm-studio") -> str | None:
        return {"a2a-inbound": PEER_TOKEN}.get(profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        return None

    def delete(self, profile: str = "lm-studio") -> None:
        return None


class _DeterministicProvider(LMStudioProvider):
    async def diagnose_tools(self, model: str, *, request: Any = None) -> bool:
        return True

    async def health(self) -> bool:
        return True

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fixture/a2a-sdk")]

    async def chat(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "A2A SDK interoperability verified.",
                    },
                    "finish_reason": "stop",
                }
            ]
        }

    async def close(self) -> None:
        return None


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_started(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while not server.started:
        if task.done():
            task.result()
            raise RuntimeError("The local acceptance API exited before starting.")
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("The local acceptance API did not start.")
        await asyncio.sleep(0.025)


async def _report_http_error(response: httpx.Response) -> None:
    if response.status_code >= 500:
        await response.aread()
        print(f"A2A acceptance HTTP error {response.status_code}: {response.text}")


async def _run() -> dict[str, object]:
    sdk_client = cast(Any, import_module("a2a.client"))
    sdk_types = cast(Any, import_module("a2a.types"))
    A2ACardResolver = sdk_client.A2ACardResolver
    ClientConfig = sdk_client.ClientConfig
    create_client = sdk_client.create_client
    GetTaskRequest = sdk_types.GetTaskRequest
    Message = sdk_types.Message
    Part = sdk_types.Part
    Role = sdk_types.Role
    SendMessageRequest = sdk_types.SendMessageRequest
    TaskState = sdk_types.TaskState
    with tempfile.TemporaryDirectory(prefix="xaa-a2a-sdk-") as temporary:
        database = Path(temporary) / "runs.sqlite3"
        store = RunStore(database)
        app = create_app(
            _DeterministicProvider(),
            SecretStr(OWNER_TOKEN),
            store=store,
            credential_store=cast(CredentialStore, _Credentials()),
        )
        with store.connection() as connection:
            connection.execute(
                "UPDATE provider_profiles SET selected_model='fixture/a2a-sdk' WHERE id='lm-studio'"
            )
            connection.execute(
                "UPDATE a2a_listener_settings SET enabled=1,bind_address='127.0.0.1',"
                "port=?,allow_network=0 WHERE id=1",
                (_free_port(),),
            )
        owner_port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=owner_port,
                log_level="error",
                access_log=False,
            )
        )
        server_task = asyncio.create_task(server.serve(), name="a2a-sdk-acceptance-api")
        try:
            await _wait_started(server, server_task)
            listener_row = None
            with store.connection() as connection:
                listener_row = connection.execute(
                    "SELECT port FROM a2a_listener_settings WHERE id=1"
                ).fetchone()
            if listener_row is None:
                raise RuntimeError("A2A listener settings were not available.")
            peer_url = f"http://127.0.0.1:{int(listener_row['port'])}"
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {PEER_TOKEN}"},
                timeout=15,
                event_hooks={"response": [_report_http_error]},
            ) as http_client:
                resolver = A2ACardResolver(http_client, peer_url)
                card = await resolver.get_agent_card()
                if card.supported_interfaces[0].protocol_version != "1.0":
                    raise AssertionError("The Agent Card did not declare A2A protocol 1.0.")
                owner_route = await http_client.get(f"{peer_url}/channels/a2a")
                if owner_route.status_code != 404:
                    raise AssertionError("The peer listener exposed the owner API route.")
                client = await create_client(
                    card,
                    client_config=ClientConfig(streaming=True, httpx_client=http_client),
                )
                request = SendMessageRequest(
                    message=Message(
                        role=Role.ROLE_USER,
                        message_id=uuid4().hex,
                        context_id=uuid4().hex,
                        parts=[Part(text="Return the deterministic acceptance response.")],
                    )
                )
                task_id: str | None = None
                streamed_artifact_id: str | None = None
                streamed_answer: str | None = None
                stream_state: str | None = None
                async for event in client.send_message(request):
                    if event.HasField("task"):
                        task_id = event.task.id
                        stream_state = TaskState.Name(event.task.status.state)
                    elif event.HasField("status_update"):
                        status = event.status_update.status
                        stream_state = TaskState.Name(status.state)
                    elif event.HasField("artifact_update"):
                        artifact = event.artifact_update.artifact
                        streamed_artifact_id = artifact.artifact_id
                        streamed_answer = "".join(part.text for part in artifact.parts)
                if task_id is None:
                    raise AssertionError("Official A2A client received no task event.")
                if streamed_answer != "A2A SDK interoperability verified.":
                    raise AssertionError("Official A2A client did not receive the text artifact.")
                if stream_state != "TASK_STATE_INPUT_REQUIRED":
                    raise AssertionError(f"Unexpected owner-review stream state: {stream_state!r}.")
                task = await client.get_task(GetTaskRequest(id=task_id, history_length=10))
                if task.status.state != TaskState.TASK_STATE_INPUT_REQUIRED:
                    raise AssertionError("The official A2A client parsed the wrong task state.")
                if not task.artifacts or task.artifacts[0].artifact_id != streamed_artifact_id:
                    raise AssertionError("Stream and task polling returned different artifact IDs.")
                if task.artifacts[0].parts[0].text != streamed_answer:
                    raise AssertionError("Task polling did not preserve the candidate response.")
                if len(task.history) != 2:
                    raise AssertionError("Task polling did not return user/agent history.")
                await client.close()
                return {
                    "sdk": "a2a-sdk==1.3.1",
                    "protocol_version": card.supported_interfaces[0].protocol_version,
                    "transport": "JSONRPC",
                    "stream_state": stream_state,
                    "task_id": task_id,
                    "artifact_id": streamed_artifact_id,
                    "history_count": len(task.history),
                    "owner_api_exposed_on_peer_listener": False,
                    "live_model_inference": False,
                }
        finally:
            server.should_exit = True
            await server_task


def main() -> None:
    result = asyncio.run(_run())
    acceptance_id = os.environ.get("GITHUB_RUN_ID") or uuid4().hex
    report_directory = Path(".acceptance") / f"a2a-sdk-interop-{acceptance_id}"
    report_directory.mkdir(parents=True, exist_ok=True)
    report = report_directory / "report.json"
    report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"A2A SDK interoperability acceptance passed: {report}")


if __name__ == "__main__":
    main()

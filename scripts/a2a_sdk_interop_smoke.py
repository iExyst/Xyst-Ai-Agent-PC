"""Exercise the local A2A endpoint with the official A2A Python client SDK."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import socket
import ssl
import tempfile
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.channels.a2a_outbound import A2AOutboundClient
from xaa.credentials import CredentialStore
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.storage.runs import RunStore

OWNER_TOKEN = "a2a-sdk-owner-token-for-local-acceptance"
PEER_TOKEN = "a2a-sdk-peer-token-for-local-acceptance"


class _Credentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {"a2a-inbound": PEER_TOKEN}

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.values.get(profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        self.values[profile] = token

    def delete(self, profile: str = "lm-studio") -> None:
        self.values.pop(profile, None)


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


def _write_test_certificate(certificate_path: Path, private_key_path: Path) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private_key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


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
        peer_database = Path(temporary) / "peer-runs.sqlite3"
        certificate_path = Path(temporary) / "a2a-test-cert.pem"
        private_key_path = Path(temporary) / "a2a-test-key.pem"
        _write_test_certificate(certificate_path, private_key_path)
        tls_context = ssl.create_default_context(cafile=str(certificate_path))
        store = RunStore(database)
        credentials = _Credentials()
        peer_store = RunStore(peer_database)
        peer_credentials = _Credentials()
        peer_listener_port = _free_port()
        peer_app = create_app(
            _DeterministicProvider(),
            SecretStr("a2a-sdk-peer-owner-token-for-local-acceptance"),
            store=peer_store,
            credential_store=cast(CredentialStore, peer_credentials),
        )
        with peer_store.connection() as connection:
            connection.execute(
                "UPDATE provider_profiles SET selected_model='fixture/a2a-sdk' WHERE id='lm-studio'"
            )
            connection.execute(
                "UPDATE a2a_listener_settings SET enabled=1,bind_address='127.0.0.1',"
                "port=?,allow_network=0,tls_cert_path=?,tls_key_path=? WHERE id=1",
                (peer_listener_port, str(certificate_path), str(private_key_path)),
            )
        app = create_app(
            _DeterministicProvider(),
            SecretStr(OWNER_TOKEN),
            store=store,
            credential_store=cast(CredentialStore, credentials),
            a2a_outbound_client=A2AOutboundClient(verify=tls_context),
        )
        owner_listener_port = _free_port()
        with store.connection() as connection:
            connection.execute(
                "UPDATE provider_profiles SET selected_model='fixture/a2a-sdk' WHERE id='lm-studio'"
            )
            connection.execute(
                "UPDATE a2a_listener_settings SET enabled=1,bind_address='127.0.0.1',"
                "port=?,allow_network=0,tls_cert_path=?,tls_key_path=? WHERE id=1",
                (owner_listener_port, str(certificate_path), str(private_key_path)),
            )
        owner_port = _free_port()
        peer_owner_port = _free_port()
        peer_server = uvicorn.Server(
            uvicorn.Config(
                peer_app,
                host="127.0.0.1",
                port=peer_owner_port,
                log_level="error",
                access_log=False,
            )
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=owner_port,
                log_level="error",
                access_log=False,
            )
        )
        peer_server_task = asyncio.create_task(
            peer_server.serve(), name="a2a-sdk-acceptance-peer-api"
        )
        server_task = asyncio.create_task(server.serve(), name="a2a-sdk-acceptance-api")
        try:
            await _wait_started(peer_server, peer_server_task)
            await _wait_started(server, server_task)
            listener_row = None
            with store.connection() as connection:
                listener_row = connection.execute(
                    "SELECT port FROM a2a_listener_settings WHERE id=1"
                ).fetchone()
            if listener_row is None:
                raise RuntimeError("A2A listener settings were not available.")
            peer_url = f"https://127.0.0.1:{int(listener_row['port'])}"
            with peer_store.connection() as connection:
                peer_listener_row = connection.execute(
                    "SELECT port FROM a2a_listener_settings WHERE id=1"
                ).fetchone()
            if peer_listener_row is None:
                raise RuntimeError("The external A2A peer listener settings were not available.")
            remote_peer_url = f"https://127.0.0.1:{int(peer_listener_row['port'])}"
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {PEER_TOKEN}"},
                timeout=15,
                verify=tls_context,
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
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{owner_port}",
                headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
                timeout=15,
            ) as owner_client:
                peer = await owner_client.post(
                    "/channels/a2a/peers",
                    json={
                        "name": "Local SDK acceptance peer",
                        "agent_card_url": f"{remote_peer_url}/.well-known/agent-card.json",
                        "bearer_token": PEER_TOKEN,
                    },
                )
                if peer.status_code != 201:
                    raise AssertionError("The owner API did not save the outbound A2A peer.")
                outbound_body = {
                    "text": "Return the deterministic acceptance response.",
                    "idempotency_key": "sdk-outbound-acceptance-1",
                }
                outbound = await owner_client.post(
                    f"/channels/a2a/peers/{peer.json()['id']}/tasks", json=outbound_body
                )
                if outbound.status_code != 202 or outbound.json()["state"] != "sent":
                    raise AssertionError("The configured A2A peer task was not accepted.")
                duplicate = await owner_client.post(
                    f"/channels/a2a/peers/{peer.json()['id']}/tasks", json=outbound_body
                )
                if duplicate.json()["id"] != outbound.json()["id"]:
                    raise AssertionError("Outbound A2A idempotent replay created a second task.")
                deadline = asyncio.get_running_loop().time() + 30
                polled = None
                while asyncio.get_running_loop().time() < deadline:
                    polled = await owner_client.get(
                        f"/channels/a2a/outbound-tasks/{outbound.json()['id']}"
                    )
                    if polled.status_code != 200:
                        raise AssertionError("The owner API could not poll its outbound A2A task.")
                    poll_body = cast(dict[str, Any], polled.json())
                    remote = cast(dict[str, Any], poll_body.get("remote_task") or {})
                    remote_task_status = cast(dict[str, Any], remote.get("status") or {})
                    remote_status = remote_task_status.get("state")
                    if remote_status not in {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING"}:
                        break
                    await asyncio.sleep(0.05)
                if polled is None:
                    raise AssertionError("The outbound A2A task was not polled.")
                poll_body = cast(dict[str, Any], polled.json())
                remote_task = cast(dict[str, Any], poll_body.get("remote_task") or {})
                remote_task_status = cast(dict[str, Any], remote_task.get("status") or {})
                remote_status = remote_task_status.get("state")
                if remote_status != "TASK_STATE_INPUT_REQUIRED":
                    remote_run = peer_store.get_run(str(outbound.json()["remote_task_id"]))
                    diagnostic = (
                        f"run_state={remote_run['state']},model={remote_run['model']}"
                        if remote_run is not None
                        else "remote run was not found"
                    )
                    raise AssertionError(
                        f"Unexpected outbound A2A peer state: {remote_status!r}; {diagnostic}; "
                        f"poll_error={poll_body.get('error')!r}."
                    )
                remote_history = cast(list[Any], remote_task.get("history") or [])
                if len(remote_history) != 2:
                    raise AssertionError(
                        "Outbound A2A polling did not return durable task history."
                    )
                return {
                    "sdk": "a2a-sdk==1.3.1",
                    "protocol_version": card.supported_interfaces[0].protocol_version,
                    "transport": "JSONRPC",
                    "secure_transport": "HTTPS",
                    "stream_state": stream_state,
                    "task_id": task_id,
                    "artifact_id": streamed_artifact_id,
                    "history_count": len(task.history),
                    "owner_api_exposed_on_peer_listener": False,
                    "outbound_peer_task_id": outbound.json()["remote_task_id"],
                    "outbound_task_poll_state": remote_status,
                    "outbound_task_history_count": len(remote_history),
                    "outbound_idempotent_replay": duplicate.json()["id"] == outbound.json()["id"],
                    "outbound_separate_peer_instance": True,
                    "live_model_inference": False,
                }
        finally:
            server.should_exit = True
            peer_server.should_exit = True
            await server_task
            await peer_server_task


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

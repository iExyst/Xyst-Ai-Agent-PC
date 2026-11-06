"""Exercise ACP tool status updates with an already-loaded local LM Studio model."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import socket
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import uvicorn
from acp import PROTOCOL_VERSION, spawn_agent_process, text_block
from acp.interfaces import Client
from fastapi import FastAPI
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.runs import RunStore

REPOSITORY = Path(__file__).resolve().parents[1]
REQUIRED_TOOL_STATUSES = {"pending", "completed"}


class UpdateSink:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.tool_updates: list[dict[str, str]] = []

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        tool_call_id = getattr(update, "tool_call_id", None)
        if isinstance(tool_call_id, str):
            status = getattr(update, "status", None)
            title = getattr(update, "title", None)
            if isinstance(status, str) and isinstance(title, str):
                self.tool_updates.append({"status": status, "title": title})
            return
        content = getattr(update, "content", None)
        text = getattr(content, "text", None)
        if isinstance(text, str) and text:
            self.messages.append(text)


async def _watch_run(store: RunStore) -> None:
    while True:
        await asyncio.sleep(10)
        with store.connection() as connection:
            run = connection.execute(
                "SELECT id,state FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if run is None:
                snapshot: dict[str, Any] = {"state": "not_started", "recent_events": []}
            else:
                rows = connection.execute(
                    "SELECT event_type FROM run_events WHERE run_id=? "
                    "ORDER BY sequence DESC LIMIT 8",
                    (run["id"],),
                ).fetchall()
                snapshot = {
                    "state": run["state"],
                    "recent_events": [row["event_type"] for row in reversed(rows)],
                }
        print(json.dumps({"run_progress": snapshot}), flush=True)


async def _run(model_id: str, report_path: Path | None) -> dict[str, Any]:
    provider = LMStudioProvider()
    try:
        models = await provider.models()
        selected = next((model for model in models if model.id == model_id), None)
        if selected is None:
            raise RuntimeError(f"LM Studio did not discover the selected model {model_id!r}.")
        if selected.loaded is not True:
            raise RuntimeError(
                f"Model {model_id!r} is not already loaded; this smoke will not load it."
            )
        if not selected.capabilities.trained_for_tool_use:
            raise RuntimeError(f"Model {model_id!r} is not marked as tool-use capable.")
        print(json.dumps({"model_ready": model_id, "loaded": True}), flush=True)

        with tempfile.TemporaryDirectory(prefix="xaa-acp-live-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            secret = uuid4().hex
            fixture = workspace / "live-token.txt"
            fixture.write_text(secret, encoding="utf-8")
            store = RunStore(root / "data" / "xaa.sqlite3")
            token = uuid4().hex + uuid4().hex
            app: FastAPI = create_app(provider, SecretStr(token), store=store)

            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            listener.setblocking(False)
            port = listener.getsockname()[1]
            server = uvicorn.Server(
                uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical")
            )
            server_task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                for _ in range(200):
                    if server.started:
                        break
                    await asyncio.sleep(0.025)
                if not server.started:
                    raise RuntimeError("The temporary authenticated X.A.A. API did not start.")
                print(json.dumps({"temporary_api_ready": True}), flush=True)

                api_url = f"http://127.0.0.1:{port}"
                sink = UpdateSink()
                env = {
                    **os.environ,
                    "XAA_API_TOKEN": token,
                    "XAA_API_URL": api_url,
                }
                async with spawn_agent_process(
                    cast(Client, sink),
                    sys.executable,
                    "-m",
                    "xaa.cli",
                    "--api-url",
                    api_url,
                    "acp",
                    "--model",
                    model_id,
                    "--workspace",
                    str(workspace),
                    "--mode",
                    "observe",
                    env=env,
                    cwd=REPOSITORY,
                ) as (connection, _process):
                    initialized = await connection.initialize(protocol_version=PROTOCOL_VERSION)
                    if initialized.agent_info is None:
                        raise RuntimeError("ACP initialization omitted agent metadata.")
                    session = await connection.new_session(cwd=str(workspace), mcp_servers=[])
                    monitor = asyncio.create_task(_watch_run(store))
                    prompt = (
                        "Use your workspace file reading tool to read live-token.txt from the "
                        "workspace. Then reply with exactly the file contents and nothing else. "
                        "You must read the file; do not infer its contents."
                    )
                    try:
                        response = await asyncio.wait_for(
                            connection.prompt(
                                session_id=session.session_id,
                                prompt=[text_block(prompt)],
                            ),
                            timeout=180,
                        )
                    finally:
                        monitor.cancel()
                        try:
                            await monitor
                        except asyncio.CancelledError:
                            pass
                    await connection.close_session(session_id=session.session_id)
                answer = "\n".join(sink.messages)
                statuses = {update["status"] for update in sink.tool_updates}
                with store.connection() as connection:
                    run = connection.execute(
                        "SELECT state FROM runs ORDER BY created_at DESC LIMIT 1"
                    ).fetchone()
                run_state = run["state"] if run is not None else "missing"
                if response.stop_reason != "end_turn":
                    raise RuntimeError(f"ACP prompt ended with {response.stop_reason!r}.")
                if run_state != "verifying":
                    raise RuntimeError(
                        f"Expected independent review state; received {run_state!r}."
                    )
                if not REQUIRED_TOOL_STATUSES.issubset(statuses):
                    raise RuntimeError(f"ACP tool lifecycle was incomplete: {sorted(statuses)}.")
                if secret not in answer:
                    raise RuntimeError("The model's ACP answer did not contain the file contents.")

                report: dict[str, Any] = {
                    "passed": True,
                    "verified_at": datetime.now(UTC).isoformat(),
                    "model": model_id,
                    "provider": "LM Studio local OpenAI-compatible API",
                    "workspace_mode": "observe",
                    "fixture_sha256": hashlib.sha256(secret.encode("utf-8")).hexdigest(),
                    "read_tool_statuses": [update["status"] for update in sink.tool_updates],
                    "read_tool_titles": [update["title"] for update in sink.tool_updates],
                    "run_state": run_state,
                    "candidate_awaiting_independent_review": True,
                    "answer_matched_fixture": True,
                    "local_inference_only": True,
                    "model_auto_loaded": False,
                    "user_project_modified": False,
                }
                if report_path is not None:
                    destination = report_path.resolve()
                    if destination.exists():
                        raise FileExistsError(
                            f"Refusing to overwrite existing evidence: {destination}"
                        )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
                return report
            finally:
                server.should_exit = True
                try:
                    await asyncio.wait_for(server_task, timeout=15)
                finally:
                    listener.close()
    finally:
        await provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, help="Already-loaded LM Studio model identifier")
    parser.add_argument("--report", type=Path, help="Optional new JSON acceptance report path")
    args = parser.parse_args()
    result = asyncio.run(_run(args.model, args.report))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

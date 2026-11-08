"""Exercise the interactive CLI against a real, locally hosted LM Studio model."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider
from xaa.storage.cli_chat import CLIChatStore
from xaa.storage.runs import RunStore


async def _run(model_id: str) -> dict[str, Any]:
    root = Path(".acceptance") / f"live-cli-chat-{uuid4()}"
    workspace = root / "workspace"
    data = root / "data"
    workspace.mkdir(parents=True)
    data.mkdir()
    marker = f"XAA-{uuid4().hex[:16]}"
    (workspace / "verification.txt").write_text(
        f"Verification marker: {marker}\n", encoding="utf-8"
    )
    store = RunStore(data / "xaa.sqlite3")
    provider = LMStudioProvider(os.environ.get("LM_STUDIO_URL", "http://127.0.0.1:1234"))
    instance: str | None = None
    server: uvicorn.Server | None = None
    server_task: asyncio.Task[None] | None = None
    listener: socket.socket | None = None
    try:
        models = await provider.models()
        model = next((item for item in models if item.id == model_id), None)
        if model is None or not model.capabilities.trained_for_tool_use:
            raise RuntimeError(f"Selected tool-capable model was not discovered: {model_id}")
        instance = await provider.load_model(model_id)

        token = uuid4().hex + uuid4().hex
        app: FastAPI = create_app(provider, SecretStr(token), store=store)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        port = int(listener.getsockname()[1])
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical")
        )
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        for _ in range(400):
            if server.started:
                break
            await asyncio.sleep(0.025)
        if not server.started:
            raise RuntimeError("The disposable authenticated X.A.A. API did not start.")

        session = f"live-{uuid4().hex[:12]}"
        environment = {
            **os.environ,
            "XAA_API_TOKEN": token,
            "XAA_API_URL": f"http://127.0.0.1:{port}",
        }
        prompt = (
            "Use workspace_read_file to read verification.txt from the selected workspace. "
            "Then reply with exactly the verification marker and no other text. "
            "Do not guess or claim a value without reading the file."
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "xaa.cli",
            "chat",
            "--model",
            model_id,
            "--workspace",
            str(workspace.resolve()),
            "--mode",
            "observe",
            "--session",
            session,
            "--reset",
            cwd=Path(__file__).resolve().parents[1],
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=f"{prompt}\n/quit\n".encode()), timeout=300
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise RuntimeError(f"xaa chat exited {process.returncode}: {stderr_bytes[-2000:]!r}")

        saved_session = CLIChatStore(store).get(session)
        with store.connection() as connection:
            run = connection.execute(
                "SELECT id,state FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            tool_rows = connection.execute(
                "SELECT tool_name,state FROM tool_calls WHERE run_id=? ORDER BY created_at",
                (run["id"],) if run else ("",),
            ).fetchall()
        tool_names = [str(row["tool_name"]) for row in tool_rows]
        passed = bool(
            marker in stdout
            and saved_session
            and len(saved_session.get("messages", [])) == 2
            and run
            and run["state"] in {"verifying", "completed"}
            and tool_names
            and any(
                "read_file" in str(row["tool_name"]) and row["state"] == "completed"
                for row in tool_rows
            )
        )
        report = {
            "passed": passed,
            "model": model_id,
            "session_persisted": bool(saved_session),
            "saved_message_count": len(saved_session.get("messages", [])) if saved_session else 0,
            "run_state": run["state"] if run else None,
            "tool_names": tool_names,
            "tool_states": [str(row["state"]) for row in tool_rows],
            "marker_recalled_in_cli_output": marker in stdout,
            "local_inference_only": True,
            "workspace_files": sorted(path.name for path in workspace.iterdir()),
            "user_project_modified": False,
        }
        (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        if not passed:
            raise RuntimeError(f"Live CLI acceptance failed; inspect {root / 'report.json'}.")
        return report
    finally:
        try:
            if instance is not None:
                await provider.unload_model(instance)
        finally:
            try:
                if server is not None:
                    server.should_exit = True
                if server_task is not None:
                    try:
                        await asyncio.wait_for(server_task, timeout=10)
                    except TimeoutError:
                        server_task.cancel()
                        await asyncio.gather(server_task, return_exceptions=True)
            finally:
                if listener is not None:
                    listener.close()
                await provider.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="qwen/qwen3.5-9b")
    args = parser.parse_args()
    asyncio.run(_run(args.model))


if __name__ == "__main__":
    main()

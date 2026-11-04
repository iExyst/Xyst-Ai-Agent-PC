"""Cancel a frozen tokenizer during authentication; verify connection/process cleanup."""

import asyncio
import json
import os
import sys
from contextlib import suppress
from http import HTTPStatus
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from frozen_worker_diagnostic import process_tree
from pydantic import SecretStr
from websockets.asyncio.server import ServerConnection, serve
from websockets.http11 import Request, Response
from xaa.providers.tokenization import PromptCountRequest, count_prompt


async def main() -> None:
    if os.name != "nt":
        raise RuntimeError("Frozen Windows cancellation diagnostic.")
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    arrived = asyncio.Event()
    closed = asyncio.Event()
    processes: list[asyncio.subprocess.Process] = []
    original = asyncio.create_subprocess_exec
    fixture_token = "sk-lm-" + "A" * 8 + ":" + "B" * 20
    env_clean = False

    async def spawn(*command: str, **kwargs: Any) -> asyncio.subprocess.Process:
        nonlocal env_clean
        process = await original(*command, **kwargs)
        if "--tokenizer-worker" in command:
            processes.append(process)
            environment = kwargs["env"]
            env_clean = (
                environment.get("NO_PROXY") == "*"
                and all(
                    key not in environment
                    for key in [
                        "XAA_API_TOKEN",
                        "LM_STUDIO_API_TOKEN",
                        "LM_API_TOKEN",
                        "HTTP_PROXY",
                        "ALL_PROXY",
                    ]
                )
                and all(fixture_token not in value for value in command)
            )
        return process

    def greeting(connection: ServerConnection, request: Request) -> Response | None:
        if request.path == "/lmstudio-greeting":
            return connection.respond(HTTPStatus.OK, '{"lmstudio":true}')
        return None

    async def stall(connection: ServerConnection) -> None:
        frame = json.loads(await connection.recv())
        assert frame["clientIdentifier"] == "A" * 8 and frame["clientPasskey"] == "B" * 20
        arrived.set()
        await connection.wait_closed()
        closed.set()

    report: dict[str, Any] = {
        "passed": False,
        "frozen_worker": True,
        "server": "local synthetic stalled SDK authentication",
    }
    task = None
    try:
        async with serve(stall, "127.0.0.1", 0, process_request=greeting) as server:
            port = server.sockets[0].getsockname()[1]
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(
                    sys, "executable", str(Path("build/native-dev/xaa-engine.exe").resolve())
                ),
                patch.object(asyncio, "create_subprocess_exec", spawn),
                patch.dict(
                    os.environ,
                    {
                        "HTTP_PROXY": "http://127.0.0.1:1",
                        "ALL_PROXY": "http://127.0.0.1:1",
                        "NO_PROXY": "",
                        "XAA_API_TOKEN": "fixture-engine-secret",
                        "LM_STUDIO_API_TOKEN": "fixture-provider-secret",
                        "LM_API_TOKEN": "fixture-ambient-secret",
                    },
                ),
            ):
                request = PromptCountRequest.model_validate(
                    {"model": "fixture", "messages": [{"role": "user", "content": "fixture"}]}
                )
                task = asyncio.create_task(
                    count_prompt(
                        f"http://127.0.0.1:{port}", request, SecretStr(fixture_token), timeout=60
                    )
                )
                await asyncio.wait_for(arrived.wait(), 10)
                tree = await asyncio.to_thread(process_tree)
                assert len(processes) == 1
                descendants = {processes[0].pid}
                while (
                    expanded := {pid for pid, parent in tree.items() if parent in descendants}
                    - descendants
                ):
                    descendants.update(expanded)
                assert len(descendants) >= 2
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(closed.wait(), 3)
                remaining = await asyncio.to_thread(process_tree)
                alive = sorted(descendants & set(remaining))
                report.update(
                    passed=not alive and env_clean,
                    observed_owned_processes=len(descendants),
                    surviving_owned_pids=alive,
                    connection_closed=True,
                    credentials_only_in_pipe=env_clean,
                    build_sha256=json.loads(
                        Path("build/native-dev/manifest.json").read_text(encoding="utf-8")
                    ),
                )
                if not report["passed"]:
                    raise RuntimeError("Frozen tokenizer cleanup failed.")
    finally:
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

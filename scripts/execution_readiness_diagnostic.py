"""Exercise authenticated fixed readiness probes through a real loopback API."""

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from xaa.runtime.processes import stop_owned_process


async def diagnose(distribution: str, engine: Path | None) -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_hex(32)
    environment = dict(os.environ)
    environment.update(XAA_DATA_DIR=str(root / "data"), XAA_API_TOKEN=token, XAA_API_PORT=str(port))
    command = (
        [str(engine.resolve(strict=True))] if engine else [sys.executable, "-I", "-m", "xaa.api"]
    )
    if engine:
        environment["PATH"] = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
        environment.pop("PYTHONPATH", None)
        environment.pop("VIRTUAL_ENV", None)
    headers = {"Authorization": "Bearer " + token}
    with (root / "engine.log").open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            *command,
            env=environment,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
            creationflags=0x08000000,
        )
        try:
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=50) as client:
                deadline = time.monotonic() + 30
                while True:
                    if process.returncode is not None:
                        raise RuntimeError("API exited before startup")
                    try:
                        response = await client.get("/runs", headers=headers)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= deadline:
                        raise TimeoutError("API startup deadline expired")
                    await asyncio.sleep(0.1)
                body = {"distribution": distribution}
                assert (await client.post("/execution/readiness", json=body)).status_code == 401
                assert (
                    await client.post(
                        "/execution/readiness", headers=headers, json={**body, "argv": ["unsafe"]}
                    )
                ).status_code == 422
                ready = await client.post("/execution/readiness", headers=headers, json=body)
                ready.raise_for_status()
                result = ready.json()
                assert result["state"] == "ready", (
                    "Actual selected backend failed its fixed self-test"
                )
                repeated = await client.post("/execution/readiness", headers=headers, json=body)
                assert repeated.json()["checked_at"] == result["checked_at"]
                missing = await client.post(
                    "/execution/readiness",
                    headers=headers,
                    json={"distribution": "XAA-Missing-" + uuid4().hex},
                )
                missing.raise_for_status()
                assert missing.json()["state"] == "unavailable" and missing.json()["checks"] == []
                runs = await client.get("/runs", headers=headers)
                assert runs.json()["items"] == []
                stopped = await client.post("/shutdown", headers=headers)
                stopped.raise_for_status()
                await asyncio.wait_for(process.wait(), 15)
                assert process.returncode == 0
        finally:
            await stop_owned_process(process)
    report = {
        "passed": True,
        "frozen": engine is not None,
        "engine_sha256": hashlib.sha256(engine.read_bytes()).hexdigest() if engine else None,
        "distribution": distribution,
        "readiness": result,
        "cached_check_reused": True,
        "missing_distribution_state": "unavailable",
        "unauthenticated_status": 401,
        "arbitrary_command_rejected": 422,
        "runs_created": 0,
        "api_graceful_exit_code": process.returncode,
        "command_permission_granted": False,
        "scope": "Fixed self-test API; no project code, inference or command grant",
    }
    (root / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Execution readiness API acceptance passed: {root}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", required=True)
    parser.add_argument("--engine", type=Path)
    args = parser.parse_args()
    asyncio.run(diagnose(args.distribution, args.engine))

"""Prove Edge cannot bypass the managed proxy to reach a loopback HTTPS listener."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict


class WorkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None


async def run() -> None:
    accepted_connections = 0

    async def accept_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal accepted_connections
        del reader
        accepted_connections += 1
        writer.close()
        await writer.wait_closed()

    try:
        server = await asyncio.start_server(accept_connection, "127.0.0.1", 443)
    except OSError as exc:
        raise RuntimeError(
            "Could not reserve loopback port 443 for the proxy bypass check."
        ) from exc

    try:
        request = {
            "url": "https://localhost/",
            "approved_hosts": ["localhost"],
            "links": [],
            "screenshot": False,
        }
        completed = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-I", "-m", "xaa.runtime.browser_worker"],
            input=json.dumps(request, separators=(",", ":")).encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=45,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        response = WorkerResponse.model_validate_json(completed.stdout)
        await asyncio.sleep(0.5)
        if completed.returncode != 0 or response.ok:
            raise AssertionError("Loopback page unexpectedly returned through managed browser.")
        if accepted_connections:
            raise AssertionError(
                f"Edge bypassed the managed proxy and opened {accepted_connections} loopback connections."
            )
        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "target": request["url"],
            "managed_worker_rejected_loopback": True,
            "direct_loopback_connections": accepted_connections,
            "worker_error": response.error,
            "model_inference_requested": False,
        }
    finally:
        server.close()
        await server.wait_closed()

    acceptance = Path(".acceptance") / f"browser-proxy-bypass-{uuid4().hex}"
    acceptance.mkdir(parents=True, exist_ok=False)
    report_path = acceptance / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "report": str(report_path)}, indent=2))


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()

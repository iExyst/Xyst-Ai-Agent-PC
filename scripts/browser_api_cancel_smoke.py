"""Cancel a real managed browser tool through the authenticated source API."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType
from typing import Any, Protocol, Self, cast

from fastapi.testclient import TestClient
from pydantic import SecretStr
from xaa.api.app import create_app
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo
from xaa.storage.runs import RunStore

TOKEN = "browser-api-cancel-smoke-token-long-enough"


class JsonResponse(Protocol):
    status_code: int
    text: str

    def json(self) -> dict[str, Any]: ...


class ApiClient(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
    ) -> JsonResponse: ...


class BrowserRequestProvider(LMStudioProvider):
    def __init__(self) -> None:
        super().__init__()
        self.browser_call_returned = threading.Event()

    async def diagnose_tools(self, model: str, *, request: Any = None) -> bool:
        del model, request
        return True

    async def models(self) -> list[ModelInfo]:
        return [ModelInfo(id="fixture/browser-cancel")]

    async def chat(
        self, *, model: str, messages: list[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        del model, messages, kwargs
        self.browser_call_returned.set()
        return {
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "browser-api-cancel-call",
                                "type": "function",
                                "function": {
                                    "name": "browser_run",
                                    "arguments": json.dumps(
                                        {"url": "https://httpbin.org/delay/10"}
                                    ),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }


def main() -> None:
    with TemporaryDirectory(prefix="browser-api-cancel-", dir=".acceptance") as temporary:
        database = Path(temporary) / "runs.sqlite3"
        workspace = Path(temporary) / "workspace"
        workspace.mkdir()
        store = RunStore(database)
        provider = BrowserRequestProvider()
        headers = {"Authorization": f"Bearer {TOKEN}"}
        test_client = cast(
            ApiClient, TestClient(create_app(provider, SecretStr(TOKEN), store=store))
        )
        with test_client as client:
            created = client.post(
                "/runs",
                headers=headers,
                json={
                    "objective": "Verify authenticated cancellation of a managed browser read",
                    "model": "fixture/browser-cancel",
                    "workspace": str(workspace),
                    "approved_browser": True,
                    "approved_web_hosts": ["httpbin.org"],
                },
            )
            if created.status_code != 201:
                raise AssertionError(f"Could not create the browser acceptance run: {created.text}")
            run_id = created.json()["id"]
            if not provider.browser_call_returned.wait(10):
                raise AssertionError("The deterministic provider did not request browser_run.")

            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not store.unresolved_tool_calls(run_id):
                time.sleep(0.05)
            unresolved = store.unresolved_tool_calls(run_id)
            if len(unresolved) != 1 or unresolved[0]["tool_name"] != "browser.run":
                raise AssertionError(f"The browser tool intent was not active: {unresolved!r}")
            # Let the packaged source worker and Edge complete startup before canceling.
            time.sleep(2)

            cancelled_at = time.monotonic()
            cancelled = client.post(f"/runs/{run_id}/cancel", headers=headers)
            cleanup_ms = round((time.monotonic() - cancelled_at) * 1000)
            if cancelled.status_code != 200 or cancelled.json()["state"] != "cancelled":
                raise AssertionError(f"Authenticated run cancellation failed: {cancelled.text}")
            if cleanup_ms >= 8000:
                raise AssertionError(
                    f"API cancellation did not stop the browser promptly: {cleanup_ms} ms"
                )

            with closing(sqlite3.connect(database)) as connection:
                row = connection.execute(
                    "SELECT tool_name,state,result FROM tool_calls WHERE provider_call_id=?",
                    (unresolved[0]["provider_call_id"],),
                ).fetchone()
            if row is None or row[0] != "browser.run" or row[1] != "completed":
                raise AssertionError(f"Browser cancellation was not durably journaled: {row!r}")
            result = json.loads(row[2])
            if result != {"status": "cancelled", "trust": "untrusted_web_content"}:
                raise AssertionError(f"Unexpected browser cancellation result: {result!r}")

        report = {
            "checked_at_utc": datetime.now(UTC).isoformat(),
            "mode": "source API with deterministic fake provider; no model inference",
            "browser": "Microsoft Edge via managed browser worker",
            "run_state": cancelled.json()["state"],
            "tool_state": row[1],
            "tool_result": result,
            "authenticated_cancel_cleanup_ms": cleanup_ms,
        }
        report_id = os.environ.get("GITHUB_RUN_ID", str(int(time.time())))
        destination = Path(".acceptance") / f"browser-api-cancel-{report_id}"
        destination.mkdir(parents=True, exist_ok=False)
        (destination / "report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({**report, "report": str(destination / "report.json")}, indent=2))


if __name__ == "__main__":
    main()

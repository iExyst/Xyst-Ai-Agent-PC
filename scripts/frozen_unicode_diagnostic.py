"""Verify UTF-8 filesystem IPC through the actual frozen worker under legacy encoding."""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from xaa.runtime import tool_worker
from xaa.storage.runs import RunStore


async def main() -> None:
    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    store = RunStore(root / "runs.sqlite3")
    run = store.create_run(
        objective="Frozen Unicode read",
        acceptance_criteria=[],
        workspace=str(root),
        mode="observe",
        model=None,
    )
    token = store.acquire_lease(run["id"], "acceptance")
    assert token is not None
    filename, content = "\u8cc7\u6599.txt", "R\u00e9sum\u00e9 \u3053\u3093\u306b\u3061\u306f\n"
    fixture = root / filename
    fixture.write_bytes(content.encode("utf-8"))
    stamp = fixture.stat().st_mtime_ns
    try:
        with (
            patch.object(tool_worker.sys, "frozen", True, create=True),
            patch.object(
                tool_worker.sys,
                "executable",
                str(Path("build/native-dev/xaa-engine.exe").resolve()),
            ),
            patch.dict(
                os.environ,
                {
                    "PYTHONIOENCODING": "cp1252:strict",
                    "PYTHONUTF8": "0",
                    "PATH": str(Path(os.environ["SystemRoot"]) / "System32"),
                },
            ),
        ):
            result = await tool_worker.execute_filesystem(
                store,
                run["id"],
                token,
                "unicode-read",
                "read",
                {"path": filename},
                idempotency_key="unicode-read",
            )
            listing = await tool_worker.execute_filesystem(
                store,
                run["id"],
                token,
                "unicode-list",
                "list",
                {"path": "."},
                idempotency_key="unicode-list",
            )
        assert result["text"] == content
        assert any(item["name"] == filename for item in listing["entries"])
        assert (
            fixture.read_bytes() == content.encode("utf-8") and fixture.stat().st_mtime_ns == stamp
        )
        assert not store.unresolved_tool_calls(run["id"])
        report = {
            "passed": True,
            "frozen_unicode_read_and_listing": True,
            "forced_text_encoding": "cp1252:strict",
            "restricted_path": True,
            "fixture_unchanged": True,
            "unresolved_intents": 0,
            "build_sha256": json.loads(
                Path("build/native-dev/manifest.json").read_text(encoding="utf-8")
            ),
        }
        (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps({**report, "evidence": str(root)}, indent=2))
    finally:
        store.release_lease(run["id"], token)


if __name__ == "__main__":
    asyncio.run(main())

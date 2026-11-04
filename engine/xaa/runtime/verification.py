"""Independent checks for explicitly supplied artifact requirements."""

import hashlib
import time
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.executor import WorkspaceExecutor
from xaa.runtime.tool_worker import execute_filesystem
from xaa.storage.runs import RunStore


class FileContentCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1, max_length=2048)
    expected_content: str = Field(max_length=65536)


def _failure(check: FileContentCheck, actual: dict[str, Any]) -> str | None:
    expected_hash = hashlib.sha256(check.expected_content.encode("utf-8")).hexdigest()
    if actual["truncated"] or actual["sha256"] != expected_hash:
        return f"{check.path}: expected exact content {check.expected_content!r}; observed {actual['text']!r}"
    return None


async def verify_files_owned(
    store: RunStore,
    run_id: str,
    token: str,
    checks: list[FileContentCheck],
    *,
    timeout: float = 10,
) -> list[str]:
    """Recheck current bytes through owned read workers within one total budget."""
    deadline = time.monotonic() + timeout
    failures: list[str] = []
    for check in checks:
        identity = f"verification-{uuid4()}"
        actual = await execute_filesystem(
            store,
            run_id,
            token,
            identity,
            "read",
            {"path": check.path},
            idempotency_key=identity,
            internal=True,
            timeout=min(10, max(0, deadline - time.monotonic())),
        )
        failure = _failure(check, actual)
        if failure is not None:
            failures.append(failure)
    return failures


def verify_files(
    executor: WorkspaceExecutor, run_id: str, token: str, checks: list[FileContentCheck]
) -> list[str]:
    failures: list[str] = []
    for check in checks:
        identity = f"verification-{uuid4()}"
        actual = executor.read_file(
            run_id, token, identity, {"path": check.path}, idempotency_key=identity, internal=True
        )
        failure = _failure(check, actual)
        if failure is not None:
            failures.append(failure)
    return failures

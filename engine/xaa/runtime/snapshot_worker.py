"""Owned snapshot capture: project paths come only from lease-bound saved intents."""

import sys
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.owned_transport import request_process
from xaa.runtime.workspace_snapshot import MAX_WIRE_BYTES, WorkspaceSnapshot, capture_workspace
from xaa.storage.commands import CommandJournal
from xaa.storage.runs import RunStore


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    database: str = Field(min_length=1, max_length=32768)
    run_id: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=256)
    call_id: str = Field(min_length=1, max_length=256)


async def capture_command_snapshot(
    store: RunStore, run_id: str, owner: str, call_id: str, *, deadline: float
) -> WorkspaceSnapshot:
    journal = CommandJournal(store)
    journal.snapshot_intent(run_id, owner, call_id)
    request = SnapshotRequest(
        database=str(store.path.resolve()), run_id=run_id, owner=owner, call_id=call_id
    )
    command = (
        [sys.executable, "--snapshot-worker"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-I", "-m", "xaa.runtime.snapshot_worker"]
    )
    raw = await request_process(
        command,
        request.model_dump_json().encode("utf-8"),
        timeout=min(10, deadline - time.monotonic()),
        output_limit=MAX_WIRE_BYTES,
    )
    snapshot = WorkspaceSnapshot.from_bytes(raw)
    if time.monotonic() >= deadline:
        raise TimeoutError("Parent deadline expired before snapshot acceptance.")
    journal.snapshot_intent(run_id, owner, call_id)
    return snapshot


def main() -> None:
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError("Snapshot request exceeded its fixed limit.")
    request = SnapshotRequest.model_validate_json(raw)
    journal = CommandJournal(RunStore(Path(request.database)))
    intent = journal.snapshot_intent(request.run_id, request.owner, request.call_id)
    snapshot = capture_workspace(Path(intent.workspace))
    journal.snapshot_intent(request.run_id, request.owner, request.call_id)
    sys.stdout.buffer.write(snapshot.to_bytes())
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()

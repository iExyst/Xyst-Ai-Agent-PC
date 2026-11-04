"""Owned private execution workflow; not a model tool or HTTP permission surface."""

import asyncio
import base64
import hashlib
import os
import time
from typing import Any
from uuid import uuid4

from xaa.runtime.cleanup import finish_cleanup
from xaa.runtime.command_contracts import CommandIntent
from xaa.runtime.snapshot_worker import capture_command_snapshot
from xaa.runtime.wsl_transport import WSLTransport
from xaa.storage.commands import (
    CommandCleanupEvidence,
    CommandJournal,
    CommandOutcome,
    command_artifact_root,
)
from xaa.storage.runs import RunStore


class CommandController:
    def __init__(self, store: RunStore, transport: WSLTransport):
        self.store = store
        self.transport = transport
        self.journal = CommandJournal(store)

    def artifact(self, run_id: str, owner: str, identity: str, kind: str, data: bytes) -> str:
        directory = command_artifact_root(self.store, create=True)
        name = f"{uuid4()}.{kind}.bin"
        with (directory / name).open("xb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        return self.store.register_artifact(
            run_id, owner, name, provenance=f"execution.{kind}:{identity}"
        )["id"]

    async def cleanup(self, intent: CommandIntent) -> CommandCleanupEvidence:
        """Observe remote closure separately from stopping the Windows launcher."""
        deadline = time.monotonic() + 20
        acknowledgement = await self.transport.control("cleanup", intent, deadline=deadline)
        while True:
            status = await self.transport.control("status", intent, deadline=deadline)
            if not status.get("manager_alive"):
                break
            if time.monotonic() >= deadline:
                raise TimeoutError("Manager remains alive; command admission stays reserved.")
            await asyncio.sleep(0.05)
        return CommandCleanupEvidence.model_validate(
            {
                "execution_id": intent.execution_id,
                "distribution": intent.distribution,
                "profile_sha256": intent.profile_sha256,
                "closed": status.get("closed"),
                "unit_stopped": acknowledgement.get("cleanup_confirmed"),
                "cleanup_confirmed": acknowledgement.get("cleanup_confirmed"),
                "manager_alive": status.get("manager_alive"),
                "manager_outcome_available": status.get("outcome") is not None,
            }
        )

    async def reconcile_interrupted(
        self, run_id: str, owner: str, call_id: str, intent: CommandIntent
    ) -> dict[str, Any]:
        proof = await self.cleanup(intent)
        run = self.store.get_run(run_id)
        if run is not None and run["state"] in {"paused", "blocked", "failed", "cancelled"}:
            return self.journal.record_stopped_cleanup(run_id, run["revision"], call_id, proof)
        artifact_id = self.artifact(
            run_id, owner, intent.execution_id, "cleanup", proof.model_dump_json().encode()
        )
        return self.journal.close_interrupted(run_id, owner, call_id, artifact_id)

    async def execute(
        self,
        run_id: str,
        owner: str,
        call_id: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
        deadline: float,
    ) -> dict[str, Any]:
        if deadline <= time.monotonic():
            raise TimeoutError("Parent time budget expired before command reservation.")
        intent = self.journal.reserve(
            run_id, owner, call_id, arguments, idempotency_key=idempotency_key
        )
        try:
            self.transport.require_intent(intent)
            snapshot = await capture_command_snapshot(
                self.store, run_id, owner, call_id, deadline=deadline
            )
            manifest = self.artifact(
                run_id, owner, intent.execution_id, "snapshot", snapshot.manifest_bytes()
            )
            self.journal.snapshot_ready(run_id, owner, call_id, snapshot, manifest)
            intent = self.journal.authorize_launch(run_id, owner, call_id)
            observed = await self.transport.run(intent, snapshot, deadline=deadline)
            output_ids: dict[str, str] = {}
            combined = 0
            for stream in ("stdout", "stderr"):
                encoded = observed.get(stream + "_b64")
                if not isinstance(encoded, str) or len(encoded) > 1_398_104:
                    raise ValueError("Manager returned invalid bounded command output.")
                data = base64.b64decode(encoded, validate=True)
                combined += len(data)
                if combined > 1_048_576 or (
                    len(data) != observed.get(stream + "_bytes")
                    or hashlib.sha256(data).hexdigest() != observed.get(stream + "_sha256")
                ):
                    raise ValueError("Command output does not match its bounded receipt.")
                output_ids[stream + "_artifact_id"] = self.artifact(
                    run_id, owner, intent.execution_id, stream, data
                )
            outcome = CommandOutcome.model_validate(
                {
                    **{
                        key: observed[key] for key in CommandOutcome.model_fields if key in observed
                    },
                    **output_ids,
                }
            )
            return self.journal.complete(run_id, owner, call_id, outcome)
        except (Exception, asyncio.CancelledError):
            # A distinct bounded cleanup allowance never becomes extra execution time.
            await finish_cleanup(self.reconcile_interrupted(run_id, owner, call_id, intent))
            raise

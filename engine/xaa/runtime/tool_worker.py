"""Owned filesystem worker protocol; accepts only fixed application operations."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from xaa.runtime.cleanup import finish_cleanup
from xaa.runtime.directory_listing import list_directory
from xaa.runtime.executor import ToolPreconditionError, WorkspaceExecutor
from xaa.runtime.processes import stop_owned_process
from xaa.runtime.workspace_search import search_workspace
from xaa.storage.runs import RunStore, TaskReadScope

FilesystemOperation = Literal[
    "read", "write", "create", "replace", "patch", "list", "search", "reconcile"
]


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    database: str
    run_id: str
    token: str
    call_id: str
    operation: FilesystemOperation
    arguments: dict[str, Any]
    idempotency_key: str
    internal: bool = False
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    task_token: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def task_reader_only(self) -> "WorkerRequest":
        if (self.task_id is None) != (self.task_token is None):
            raise ValueError("Task worker scope needs both task identity and lease.")
        if self.task_id is not None and (
            not self.internal or self.operation not in {"read", "list"}
        ):
            raise PermissionError(
                "Task workers currently support only scoped internal reads/listing."
            )
        return self


class WorkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ok: bool
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class FilesystemWorkerError(RuntimeError):
    """A worker did not produce a validated result; inspect the durable journal."""


class FilesystemWorkerTimeout(TimeoutError):
    """A filesystem worker exceeded its deadline and was stopped."""


async def execute_filesystem(
    store: RunStore,
    run_id: str,
    token: str,
    call_id: str,
    operation: FilesystemOperation,
    arguments: dict[str, Any],
    *,
    idempotency_key: str,
    timeout: float = 10,
    internal: bool = False,
    task_id: str | None = None,
    task_token: str | None = None,
) -> dict[str, Any]:
    if timeout <= 0:
        raise FilesystemWorkerTimeout("No filesystem operation budget remains.")
    request = WorkerRequest(
        database=str(store.path.resolve()),
        run_id=run_id,
        token=token,
        call_id=call_id,
        operation=operation,
        arguments=arguments,
        idempotency_key=idempotency_key,
        internal=internal,
        task_id=task_id,
        task_token=task_token,
    )
    payload = request.model_dump_json().encode()
    if len(payload) > 1_048_576:
        raise FilesystemWorkerError("Filesystem worker request exceeded its limit.")
    command = (
        [sys.executable, "--tool-worker"]
        if getattr(sys, "frozen", False)
        else [sys.executable, "-I", "-m", "xaa.runtime.tool_worker"]
    )
    # Engine API/provider credentials are not required by a filesystem worker.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"XAA_API_TOKEN", "LM_STUDIO_API_TOKEN", "LM_API_TOKEN"}
    }
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=environment,
        creationflags=0x08000000 if os.name == "nt" else 0,
    )
    try:
        async with asyncio.timeout(timeout):
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            output = bytearray()
            while chunk := await process.stdout.read(16384):
                output.extend(chunk)
                if len(output) > 1_048_576:
                    raise FilesystemWorkerError("Filesystem worker output exceeded its limit.")
            await process.wait()
            if process.returncode != 0:
                raise FilesystemWorkerError(
                    "Filesystem worker exited without a result; inspect its journal."
                )
            try:
                response = WorkerResponse.model_validate_json(output)
            except ValidationError as exc:
                raise FilesystemWorkerError(
                    "Filesystem worker returned an invalid result."
                ) from exc
            if not response.ok:
                raise FilesystemWorkerError(
                    f"Filesystem operation stopped ({response.error}); inspect its journal."
                )
            return response.result
    except TimeoutError as exc:
        raise FilesystemWorkerTimeout(
            "Filesystem operation timed out; inspect its journal."
        ) from exc
    finally:
        await finish_cleanup(stop_owned_process(process))


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("Worker input limit.")
        request = WorkerRequest.model_validate_json(raw)
        scope = (
            TaskReadScope(request.run_id, request.token, request.task_id, request.task_token)
            if request.task_id is not None and request.task_token is not None
            else None
        )
        executor = WorkspaceExecutor(RunStore(Path(request.database), task_scope=scope))
        operations = {
            "read": executor.read_file,
            "write": executor.write_file,
            "create": executor.create_file,
            "replace": executor.replace_text,
            "patch": executor.apply_patch,
        }
        try:
            if request.operation == "reconcile":
                if request.arguments or not request.internal:
                    raise ValueError("Reconciliation is an internal operation without arguments.")
                result = dict(executor.reconcile_writes(request.run_id, request.token))
            elif request.operation == "list":
                result = list_directory(
                    executor.store,
                    request.run_id,
                    request.token,
                    request.call_id,
                    request.arguments,
                    internal=request.internal,
                )
            elif request.operation == "search":
                if request.internal:
                    raise ValueError("Internal search is not supported.")
                result = asyncio.run(
                    search_workspace(
                        executor.store,
                        request.run_id,
                        request.token,
                        request.call_id,
                        request.arguments,
                    )
                )
            elif request.operation == "read":
                result = executor.read_file(
                    request.run_id,
                    request.token,
                    request.call_id,
                    request.arguments,
                    idempotency_key=request.idempotency_key,
                    internal=request.internal,
                )
            else:
                if request.internal:
                    raise ValueError("Internal writes are not supported.")
                result = operations[request.operation](
                    request.run_id,
                    request.token,
                    request.call_id,
                    request.arguments,
                    idempotency_key=request.idempotency_key,
                )
        except ToolPreconditionError as exc:
            result = exc.result
        response = WorkerResponse(ok=True, result=result)
    except Exception as exc:  # noqa: BLE001 - protocol boundary returns only a redacted error class
        response = WorkerResponse(ok=False, error=type(exc).__name__)
    sys.stdout.buffer.write(response.model_dump_json().encode("utf-8"))


if __name__ == "__main__":
    main()

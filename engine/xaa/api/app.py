"""Authenticated loopback API for the desktop client."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi import Path as PathParameter
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.middleware.cors import CORSMiddleware

from xaa.api.contracts import (
    ArtifactRecord,
    MemoryRecord,
    PlanRecord,
    RunEvent,
    RunHistory,
    RunRecord,
    TaskCandidate,
    UsageReport,
)
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo, ProviderError
from xaa.providers.tokenization import PromptCount, PromptCountError, PromptCountRequest
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.file_handles import opened_file_path
from xaa.runtime.run_review import RunReviewDecision, review_run
from xaa.runtime.states import RunState
from xaa.runtime.supervisor import RunSupervisor
from xaa.runtime.task_review import TaskReviewDecision, candidate_evidence, review_candidate
from xaa.runtime.tool_worker import FilesystemWorkerError
from xaa.runtime.verification import FileContentCheck
from xaa.storage.history import list_runs
from xaa.storage.memory import MemoryStore
from xaa.storage.runs import InvalidTransition, LeaseLost, RunStore
from xaa.storage.skills import (
    SkillConflict,
    SkillDraft,
    SkillEvaluation,
    SkillLifecycleDecision,
    SkillLifecycleEvent,
    SkillRevision,
    SkillStore,
    SkillSummary,
    SkillTrial,
    SkillTrialRun,
    SkillVersion,
)
from xaa.storage.tasks import TaskStore


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=256)
    messages: list[dict[str, Any]] = Field(min_length=1)


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=1, max_length=10_000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    workspace: str | None = Field(default=None, max_length=2_048)
    mode: Literal["observe", "workspace", "authorized_automation"] = "observe"
    model: str | None = Field(default=None, max_length=256)
    max_iterations: int = Field(default=8, ge=1, le=100)
    max_seconds: int = Field(default=900, ge=1, le=86400)
    max_total_tokens: int | None = Field(default=None, ge=1, le=100_000_000, strict=True)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000, strict=True)
    procedure_trial: SkillTrial | None = None
    file_checks: list[FileContentCheck] = Field(
        default_factory=lambda: list[FileContentCheck](), max_length=20
    )


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(min_length=1, max_length=256)
    context_length: int | None = Field(default=None, ge=512, le=10_000_000)


class UnloadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instance_id: str = Field(min_length=1, max_length=256)


class PreferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=16384)


def create_app(
    provider: LMStudioProvider,
    api_token: SecretStr,
    *,
    trusted_origins: tuple[str, ...] = ("http://tauri.localhost", "https://tauri.localhost"),
    store: RunStore | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    stream_responses: bool = False,
) -> FastAPI:
    """Create a fail-closed service; caller controls token and provider lifecycle."""
    expected = api_token.get_secret_value()
    if len(expected) < 32:
        raise ValueError("XAA API token must contain at least 32 characters.")

    bearer = HTTPBearer(auto_error=False)
    bearer_dependency = Depends(bearer)

    async def authenticate(
        credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
    ) -> None:
        if credentials is None or not hmac.compare_digest(credentials.credentials, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token."
            )

    run_store = store or RunStore()
    memory_store = MemoryStore(run_store)
    skill_store = SkillStore(run_store)
    supervisor = RunSupervisor(Coordinator(run_store, provider, stream_responses=stream_responses))

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        supervisor.start()
        yield
        await supervisor.close()
        await provider.close()

    app = FastAPI(title="X.A.A. Local API", version="0.1.0", lifespan=lifespan)

    @app.post("/shutdown")
    async def shutdown(_: None = Depends(authenticate)) -> dict[str, str]:
        if shutdown_callback is None:
            raise HTTPException(status_code=503, detail="Shutdown is not configured.")
        await supervisor.close()
        shutdown_callback()
        return {"status": "stopping"}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(trusted_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID"],
    )

    @app.get("/skills", response_model=list[SkillSummary])
    def skills(_: None = Depends(authenticate)) -> list[dict[str, Any]]:
        return skill_store.list()

    @app.post("/skills", status_code=201, response_model=SkillSummary)
    def create_skill(payload: SkillDraft, _: None = Depends(authenticate)) -> dict[str, Any]:
        try:
            return skill_store.create(payload)
        except SkillConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/skills/{skill_id}", response_model=SkillSummary)
    def get_skill(skill_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        try:
            return skill_store.get(skill_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Procedure not found.") from None

    @app.post("/skills/{skill_id}/versions", status_code=201, response_model=SkillSummary)
    def revise_skill(
        skill_id: str, payload: SkillRevision, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            return skill_store.revise(skill_id, payload)
        except KeyError:
            raise HTTPException(status_code=404, detail="Procedure not found.") from None
        except SkillConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/skills/{skill_id}/versions/{version}", response_model=SkillVersion)
    def skill_version(
        skill_id: str, version: int = PathParameter(ge=1, le=128), _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            return skill_store.version(skill_id, version)
        except KeyError:
            raise HTTPException(status_code=404, detail="Procedure version not found.") from None

    @app.get("/skills/{skill_id}/versions/{version}/trials", response_model=list[SkillTrialRun])
    def eligible_skill_trials(
        skill_id: str, version: int = PathParameter(ge=1, le=128), _: None = Depends(authenticate)
    ) -> list[dict[str, Any]]:
        try:
            return skill_store.eligible_trials(skill_id, version)
        except KeyError:
            raise HTTPException(status_code=404, detail="Procedure version not found.") from None

    @app.post(
        "/skills/{skill_id}/versions/{version}/evaluations",
        status_code=201,
        response_model=SkillVersion,
    )
    def evaluate_skill(
        skill_id: str,
        payload: SkillEvaluation,
        version: int = PathParameter(ge=1, le=128),
        _: None = Depends(authenticate),
    ) -> dict[str, Any]:
        try:
            return skill_store.evaluate(skill_id, version, payload)
        except KeyError:
            raise HTTPException(status_code=404, detail="Procedure version not found.") from None
        except SkillConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/skills/{skill_id}/lifecycle", response_model=list[SkillLifecycleEvent])
    def skill_lifecycle(skill_id: str, _: None = Depends(authenticate)) -> list[dict[str, Any]]:
        try:
            return skill_store.lifecycle(skill_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Procedure not found.") from None

    @app.post("/skills/{skill_id}/lifecycle", response_model=SkillSummary)
    def decide_skill_lifecycle(
        skill_id: str, payload: SkillLifecycleDecision, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            return skill_store.decide_lifecycle(skill_id, payload)
        except KeyError:
            raise HTTPException(status_code=404, detail="Procedure not found.") from None
        except SkillConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/skills/{skill_id}", status_code=204)
    def delete_skill(skill_id: str, _: None = Depends(authenticate)) -> Response:
        try:
            skill_store.delete(skill_id)
        except SkillConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError:
            raise HTTPException(status_code=404, detail="Candidate procedure not found.") from None
        return Response(status_code=204)

    @app.get("/memory", response_model=list[MemoryRecord])
    def search_memory(q: str = "", _: None = Depends(authenticate)) -> list[dict[str, Any]]:
        return memory_store.search("local-user", q)

    @app.get("/memory/export", response_model=list[MemoryRecord])
    def export_memory(_: None = Depends(authenticate)) -> list[dict[str, Any]]:
        return memory_store.export("local-user")

    @app.post("/memory", response_model=MemoryRecord, status_code=201)
    def create_memory(
        payload: PreferenceRequest, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            return memory_store.save_preference("local-user", payload.content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/memory/{memory_id}", response_model=MemoryRecord)
    def edit_memory(
        memory_id: str, payload: PreferenceRequest, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            return memory_store.save_preference("local-user", payload.content, memory_id=memory_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Preference not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/memory/{memory_id}", status_code=204)
    def delete_memory(memory_id: str, _: None = Depends(authenticate)) -> Response:
        if not memory_store.delete("local-user", memory_id):
            raise HTTPException(status_code=404, detail="Memory not found.")
        return Response(status_code=204)

    @app.get("/health")
    async def health(_: None = Depends(authenticate)) -> dict[str, str]:
        try:
            await provider.health()
            return {"status": "ok", "provider": "lm-studio"}
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/models", response_model=list[ModelInfo])
    async def models(_: None = Depends(authenticate)) -> list[dict[str, Any]]:
        try:
            return [model.model_dump() for model in await provider.models()]
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/providers")
    async def providers(_: None = Depends(authenticate)) -> list[dict[str, Any]]:
        try:
            await provider.health()
            return [{"id": "lm-studio", "status": "connected"}]
        except ProviderError as exc:
            return [{"id": "lm-studio", "status": "unavailable", "error": str(exc)}]

    @app.post("/backups", status_code=status.HTTP_201_CREATED)
    def create_backup(_: None = Depends(authenticate)) -> dict[str, str]:
        return {"path": str(run_store.backup()), "status": "verified"}

    @app.post("/providers/lm-studio/diagnostics")
    async def diagnose_model(
        payload: ModelRequest, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            passed = await provider.diagnose_tools(payload.model)
            return {"model": payload.model, "tool_round_trip_verified": passed}
        except ProviderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/providers/lm-studio/token-count", response_model=PromptCount)
    async def token_count(
        payload: PromptCountRequest, _: None = Depends(authenticate)
    ) -> PromptCount:
        try:
            return await provider.count_prompt(payload)
        except PromptCountError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/models/load")
    async def load_model(payload: ModelRequest, _: None = Depends(authenticate)) -> dict[str, str]:
        try:
            instance = await provider.load_model(
                payload.model, context_length=payload.context_length
            )
            return {"instance_id": instance, "status": "loaded"}
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/models/unload")
    async def unload_model(
        payload: UnloadRequest, _: None = Depends(authenticate)
    ) -> dict[str, str]:
        try:
            await provider.unload_model(payload.instance_id)
            return {"instance_id": payload.instance_id, "status": "unloaded"}
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/runs", response_model=RunHistory)
    def run_history(
        _: None = Depends(authenticate),
        limit: int = Query(default=20, ge=1, le=50),
        before: str | None = Query(default=None, max_length=1024),
        q: str = Query(default="", max_length=256),
        scope: Literal["objective", "conversation"] = "objective",
    ) -> dict[str, Any]:
        try:
            return list_runs(run_store, limit=limit, before=before, query=q, scope=scope)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/runs", status_code=status.HTTP_201_CREATED, response_model=RunRecord)
    async def create_run(
        payload: CreateRunRequest, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            record = run_store.create_run(
                objective=payload.objective,
                acceptance_criteria=payload.acceptance_criteria,
                workspace=payload.workspace,
                mode=payload.mode,
                model=payload.model,
                max_iterations=payload.max_iterations,
                max_seconds=payload.max_seconds,
                max_total_tokens=payload.max_total_tokens,
                max_output_tokens=payload.max_output_tokens,
                file_checks=[check.model_dump() for check in payload.file_checks],
                procedure_trial=payload.procedure_trial.model_dump()
                if payload.procedure_trial
                else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if record["model"]:
            supervisor.submit(record["id"])
        return record

    @app.get("/runs/{run_id}", response_model=RunRecord)
    async def get_run(run_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        record = run_store.get_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return record

    @app.get("/runs/{run_id}/events", response_model=list[RunEvent])
    async def get_run_events(
        run_id: str,
        after: int = 0,
        _: None = Depends(authenticate),
    ) -> list[dict[str, Any]]:
        if run_store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run_store.public_events(run_id, max(after, 0))

    def transition_run(run_id: str, destination: RunState, reason: str) -> dict[str, Any]:
        try:
            return run_store.transition(run_id, destination, reason=reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found.") from exc
        except InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/usage", response_model=UsageReport)
    async def get_run_usage(run_id: str, _: None = Depends(authenticate)) -> dict[str, int]:
        if run_store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run_store.usage_report(run_id)

    @app.get("/runs/{run_id}/plan", response_model=PlanRecord)
    def get_plan(run_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        if run_store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        tasks = TaskStore(run_store)
        records = tasks.records(run_id)
        ready, blocked = tasks.readiness(run_id) if records else ([], [])
        return {"tasks": records, "ready": ready, "blocked": blocked}

    @app.get("/runs/{run_id}/tasks/{task_id}/candidate", response_model=TaskCandidate)
    def get_candidate(run_id: str, task_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        try:
            return candidate_evidence(run_store, run_id, task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task not found.") from exc

    @app.post("/runs/{run_id}/plan/dispatch", response_model=RunRecord, status_code=202)
    async def dispatch_plan(run_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        run = run_store.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        if run["state"] != "verifying" or not run["model"]:
            raise HTTPException(
                status_code=409, detail="The run must be awaiting review with a selected model."
            )
        tasks = TaskStore(run_store)
        if not tasks.records(run_id) or not tasks.readiness(run_id)[0]:
            raise HTTPException(status_code=409, detail="No tasks are ready to start.")
        token = run_store.acquire_lease(run_id, "local-user-dispatch", seconds=180)
        if token is None:
            raise HTTPException(status_code=409, detail="A worker still owns this run.")
        try:
            record = run_store.transition(
                run_id, RunState.RUNNING, reason="user_dispatched_ready_tasks", owner=token
            )
            if not supervisor.submit_plan(run_id, token):
                run_store.transition(
                    run_id, RunState.VERIFYING, reason="worker_still_active", owner=token
                )
                raise HTTPException(status_code=409, detail="A worker is still stopping.")
        except BaseException:
            run_store.release_lease(run_id, token)
            raise
        return record

    @app.post("/runs/{run_id}/tasks/{task_id}/review", response_model=PlanRecord)
    async def decide_candidate(
        run_id: str, task_id: str, payload: TaskReviewDecision, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            await review_candidate(run_store, run_id, task_id, payload)
        except FilesystemWorkerError as exc:
            raise HTTPException(
                status_code=409,
                detail="Specialist evidence verification could not finish; inspect the run journal before retrying.",
            ) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task not found.") from exc
        except (ValueError, OSError, LeaseLost) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return get_plan(run_id, None)

    @app.post("/runs/{run_id}/review", response_model=RunRecord)
    async def decide_run(
        run_id: str, payload: RunReviewDecision, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            return await review_run(run_store, run_id, payload)
        except FilesystemWorkerError as exc:
            raise HTTPException(
                status_code=409,
                detail="Independent file verification could not finish; inspect the run journal before retrying.",
            ) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found.") from exc
        except (ValueError, OSError, LeaseLost) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/runs/{run_id}/artifacts", response_model=list[ArtifactRecord])
    def artifacts(run_id: str, _: None = Depends(authenticate)) -> list[dict[str, Any]]:
        if run_store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return run_store.list_artifacts(run_id)

    @app.get("/artifacts/{artifact_id}")
    def download_artifact(artifact_id: str, _: None = Depends(authenticate)) -> Response:
        metadata = run_store.get_artifact(artifact_id)
        if metadata is None:
            raise HTTPException(status_code=404, detail="Artifact not found.")
        root = (run_store.path.parent / "artifacts").resolve(strict=True)
        target = (root / metadata["storage_key"]).resolve(strict=True)
        if not target.is_relative_to(root):
            raise HTTPException(status_code=409, detail="Artifact escaped managed storage.")
        with target.open("rb") as source:
            if not opened_file_path(source).is_relative_to(root):
                raise HTTPException(
                    status_code=409, detail="Opened artifact escaped managed storage."
                )
            content = source.read(8 * 1024 * 1024 + 1)
        if (
            len(content) != metadata["size_bytes"]
            or hashlib.sha256(content).hexdigest() != metadata["sha256"]
        ):
            raise HTTPException(status_code=409, detail="Artifact integrity verification failed.")
        return Response(
            content=content,
            media_type=metadata["media_type"],
            headers={"Content-Disposition": f'attachment; filename="{artifact_id}.txt"'},
        )

    @app.get("/runs/{run_id}/events/stream")
    async def stream_run_events(
        run_id: str,
        request: Request,
        after: int = 0,
        last_event_id: str | None = Header(default=None),
        _: None = Depends(authenticate),
    ) -> StreamingResponse:
        if run_store.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        try:
            cursor = max(after, int(last_event_id or "0"), 0)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Last-Event-ID must be a sequence number."
            ) from exc

        async def events() -> AsyncIterator[str]:
            sequence = cursor
            while not await request.is_disconnected():
                # Read state before events so a terminal transition cannot be missed
                # between the event query and the terminal-state check.
                run = run_store.get_run(run_id)
                for event in run_store.public_events(run_id, sequence):
                    sequence = event["sequence"]
                    yield f"id: {sequence}\nevent: run_event\ndata: {json.dumps(event)}\n\n"
                if run is None or run["state"] in {"completed", "failed", "cancelled"}:
                    yield f"event: done\ndata: {json.dumps({'sequence': sequence})}\n\n"
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.post("/runs/{run_id}/pause", response_model=RunRecord)
    async def pause_run(run_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        record = transition_run(run_id, RunState.PAUSED, "user_requested")
        await supervisor.stop(run_id)
        return record

    @app.post("/runs/{run_id}/resume", response_model=RunRecord)
    async def resume_run(run_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        record = transition_run(run_id, RunState.QUEUED, "user_requested")
        if record["model"]:
            supervisor.submit(run_id)
        return record

    @app.post("/runs/{run_id}/cancel", response_model=RunRecord)
    async def cancel_run(run_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        record = transition_run(run_id, RunState.CANCELLED, "user_requested")
        await supervisor.stop(run_id)
        return record

    @app.post("/chat/stream")
    async def stream_chat(
        payload: ChatRequest,
        request: Request,
        _: None = Depends(authenticate),
    ) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            sequence = 0
            try:
                async for chunk in provider.stream(model=payload.model, messages=payload.messages):
                    if await request.is_disconnected():
                        return
                    sequence += 1
                    event = json.dumps(
                        {"sequence": sequence, "chunk": chunk}, separators=(",", ":")
                    )
                    yield f"id: {sequence}\nevent: token\ndata: {event}\n\n"
                yield f"event: done\ndata: {json.dumps({'sequence': sequence})}\n\n"
            except ProviderError as exc:
                yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    return app

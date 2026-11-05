"""Authenticated loopback API for the desktop client."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi import Path as PathParameter
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from starlette.middleware.cors import CORSMiddleware

from xaa.api.contracts import (
    ArtifactRecord,
    CLIChatSessionRecord,
    MemoryRecord,
    PlanRecord,
    ProjectValidationSummary,
    RunEvent,
    RunHistory,
    RunRecord,
    TaskCandidate,
    UsageReport,
)
from xaa.channels.telegram import TelegramApiError, TelegramBotApi, TelegramService, TelegramStore
from xaa.credentials import CredentialStore
from xaa.providers.lm_studio import LMStudioProvider, ModelInfo, ProviderError
from xaa.providers.tokenization import PromptCount, PromptCountError, PromptCountRequest
from xaa.runtime.command_contracts import CommandGrant
from xaa.runtime.command_controller import CommandController
from xaa.runtime.coordinator import Coordinator
from xaa.runtime.execution_discovery import DistributionInventory, installed_distributions
from xaa.runtime.execution_readiness import ExecutionReadiness, ReadinessRequest, ReadinessResult
from xaa.runtime.file_handles import opened_file_path
from xaa.runtime.mcp_manager import MCPConnectionError, MCPManager
from xaa.runtime.project_validation import (
    ProjectValidationRequest,
    validate_project,
)
from xaa.runtime.project_validation import (
    summary as project_validation_summary,
)
from xaa.runtime.run_review import RunReviewDecision, review_run
from xaa.runtime.states import RunState
from xaa.runtime.supervisor import RunSupervisor
from xaa.runtime.task_integration import IntegrationPreview
from xaa.runtime.task_integration import preview as integration_preview
from xaa.runtime.task_review import TaskReviewDecision, candidate_evidence, review_candidate
from xaa.runtime.tool_worker import FilesystemWorkerError
from xaa.runtime.verification import FileContentCheck
from xaa.runtime.web_fetch import normalize_approved_hosts
from xaa.runtime.wsl_transport import WSLTransport
from xaa.scheduler.service import ScheduleService
from xaa.storage.backup_bundle import create_backup_bundle
from xaa.storage.cli_chat import CLIChatStore
from xaa.storage.commands import CommandJournal
from xaa.storage.history import list_runs
from xaa.storage.mcp import MCPRegistry, MCPServerSpec
from xaa.storage.memory import MemoryStore
from xaa.storage.runs import InvalidTransition, LeaseLost, RunStore, utc_now
from xaa.storage.schedules import ScheduleStore, parse_local_time, validate_quiet_hours
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
from xaa.storage.specialist_profiles import ROLES, SpecialistProfileStore
from xaa.storage.tasks import TaskStore
from xaa.storage.trajectory import export_trajectory


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
    command_grant: CommandGrant | None = None
    approved_web_hosts: list[str] = Field(default_factory=list, max_length=10)
    approved_web_search: bool = False
    approved_browser: bool = False
    approved_mcp_tools: list[str] = Field(default_factory=list, max_length=50)
    file_checks: list[FileContentCheck] = Field(
        default_factory=lambda: list[FileContentCheck](), max_length=20
    )

    @field_validator("approved_web_hosts")
    @classmethod
    def validate_approved_web_hosts(cls, value: list[str]) -> list[str]:
        return normalize_approved_hosts(value)


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


class CLIChatTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=256)
    workspace: str | None = Field(default=None, max_length=4096)
    mode: Literal["observe", "workspace"]
    user_message: str = Field(min_length=1, max_length=2000)
    assistant_message: str = Field(min_length=1, max_length=16000)


class LMStudioEndpointRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = Field(min_length=1, max_length=300)


class LMStudioConfigRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str
    selected_model: str | None


class LMStudioSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = Field(default=None, max_length=256)


class LMStudioCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=512)


class LMStudioCredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configured: bool


class WebSearchCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=512)


class MCPSecretRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: SecretStr = Field(min_length=1, max_length=4096)


class MCPToolPermissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    permission: Literal["none", "read", "write"]
    schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class MCPToolResponse(BaseModel):
    server_id: str
    name: str
    title: str | None
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None
    schema_sha256: str
    permission: Literal["none", "read", "write"]
    qualified_name: str
    updated_at: str


class MCPServerResponse(BaseModel):
    id: str
    name: str
    command: str
    args: list[str]
    cwd: str | None
    secret_env: list[str]
    created_at: str
    updated_at: str
    state: Literal["connected", "disconnected", "error"]
    error: str | None
    secret_env_configured: dict[str, bool]
    tools: list[MCPToolResponse]


class MCPConnectResponse(BaseModel):
    state: Literal["connected"]
    tools: list[MCPToolResponse]


class CreateScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str = Field(min_length=1, max_length=10_000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    workspace: str | None = Field(default=None, max_length=2_048)
    model: str = Field(min_length=1, max_length=256)
    start_at: str = Field(min_length=16, max_length=16)
    frequency: Literal["once", "daily", "weekly"]
    timezone: str = Field(min_length=1, max_length=128)
    missed_run_policy: Literal["skip", "run_once"] = "skip"
    quiet_hours: str | None = Field(default=None, max_length=11)
    max_iterations: int = Field(default=8, ge=1, le=100)
    max_seconds: int = Field(default=900, ge=1, le=86400)
    telegram_user_id: str | None = Field(default=None, pattern=r"^[0-9]{1,20}$")


class ScheduleExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    occurrence_id: str
    run_id: str | None
    state: str
    delivery_evidence: str | None
    delivery_attempt_count: int | None
    delivery_next_attempt_at: str | None
    created_at: str
    completed_at: str | None


class ScheduleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    objective: str
    recurrence: dict[str, Any]
    timezone: str
    missed_run_policy: str
    overlap_policy: str
    quiet_hours: str | None
    delivery_destination: str | None
    enabled: bool
    created_at: str
    updated_at: str
    executions: list[ScheduleExecutionRecord]


class ScheduleEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(strict=True)


class TelegramCredentialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: SecretStr = Field(min_length=1, max_length=512)


class TelegramCredentialStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configured: bool
    bot_username: str | None = None
    linked_users: int
    processed_updates: int
    deliveries: dict[str, int]


class TelegramPairingCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    expires_at: str


class TelegramLinkRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revoked_users: int
    linked_users: int


class TelegramLinkedUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str


class SpecialistRouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_route: str | None


class SpecialistInstructionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    system_instructions: str = Field(max_length=4000)


class SpecialistProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: str
    display_name: str
    model_route: str | None
    system_instructions: str
    updated_at: str


def create_app(
    provider: LMStudioProvider,
    api_token: SecretStr,
    *,
    trusted_origins: tuple[str, ...] = ("http://tauri.localhost", "https://tauri.localhost"),
    store: RunStore | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    stream_responses: bool = False,
    credential_store: CredentialStore | None = None,
) -> FastAPI:
    """Create a fail-closed service; caller controls token and provider lifecycle."""
    expected = api_token.get_secret_value()
    execution_readiness = ExecutionReadiness()
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
    cli_chat_store = CLIChatStore(run_store)
    specialist_profiles = SpecialistProfileStore(run_store)
    mcp_manager = MCPManager(run_store, credential_store)
    memory_store = MemoryStore(run_store)
    skill_store = SkillStore(run_store)
    supervisor = RunSupervisor(
        Coordinator(
            run_store,
            provider,
            stream_responses=stream_responses,
            credential_store=credential_store,
            mcp_manager=mcp_manager,
        )
    )
    schedule_store = ScheduleStore(run_store)
    schedule_service = ScheduleService(run_store, supervisor.submit)
    telegram_store = TelegramStore(run_store)
    telegram_service = (
        TelegramService(run_store, credential_store, supervisor.submit)
        if credential_store is not None
        else None
    )

    with run_store.connection() as connection:
        profile = connection.execute(
            "SELECT endpoint FROM provider_profiles WHERE id='lm-studio'"
        ).fetchone()
        if profile is None:
            endpoint = provider.validate_endpoint(provider.base_url)
            now = utc_now()
            connection.execute(
                "INSERT INTO provider_profiles VALUES ('lm-studio','lm-studio',?,NULL,NULL,NULL,?,?)",
                (endpoint, now, now),
            )
        else:
            endpoint = str(profile["endpoint"])

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await provider.configure_endpoint(endpoint)
        supervisor.start()
        schedule_service.start()
        if telegram_service is not None:
            telegram_service.start()
        yield
        if telegram_service is not None:
            await telegram_service.close()
        await schedule_service.close()
        await supervisor.close()
        await mcp_manager.close()
        await provider.close()

    app = FastAPI(title="X.A.A. Local API", version="0.1.0", lifespan=lifespan)

    @app.get("/mcp/servers", response_model=list[MCPServerResponse])
    def list_mcp_servers(_: None = Depends(authenticate)) -> list[dict[str, Any]]:
        registry = MCPRegistry(run_store)
        servers: list[dict[str, Any]] = []
        for server in registry.list_servers():
            state, error = mcp_manager.server_status(server.id)
            configured = {
                name: bool(credential_store and credential_store.get(f"mcp:{server.id}:{name}"))
                for name in server.secret_env
            }
            servers.append(
                {
                    **server.model_dump(),
                    "state": state,
                    "error": error,
                    "secret_env_configured": configured,
                    "tools": [tool.model_dump() for tool in registry.list_tools(server.id)],
                }
            )
        return servers

    @app.post("/mcp/servers", status_code=201, response_model=MCPServerResponse)
    def create_mcp_server(
        payload: MCPServerSpec, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            server = MCPRegistry(run_store).create_server(payload)
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="An MCP server with that name already exists."
            ) from exc
        return {
            **server.model_dump(),
            "state": "disconnected",
            "error": None,
            "secret_env_configured": {name: False for name in server.secret_env},
            "tools": [],
        }

    @app.delete("/mcp/servers/{server_id}", status_code=204)
    async def delete_mcp_server(server_id: str, _: None = Depends(authenticate)) -> Response:
        if not await mcp_manager.delete_server(server_id):
            raise HTTPException(status_code=404, detail="MCP server not found.")
        return Response(status_code=204)

    @app.post("/mcp/servers/{server_id}/connect", response_model=MCPConnectResponse)
    async def connect_mcp_server(server_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        try:
            tools = await mcp_manager.connect_server(server_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="MCP server not found.") from exc
        except MCPConnectionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"state": "connected", "tools": [tool.model_dump() for tool in tools]}

    @app.post("/mcp/servers/{server_id}/disconnect")
    async def disconnect_mcp_server(
        server_id: str, _: None = Depends(authenticate)
    ) -> dict[str, str]:
        if MCPRegistry(run_store).get_server(server_id) is None:
            raise HTTPException(status_code=404, detail="MCP server not found.")
        await mcp_manager.disconnect_server(server_id)
        return {"state": "disconnected"}

    @app.put(
        "/mcp/servers/{server_id}/tools/{tool_name}/permission", response_model=MCPToolResponse
    )
    def set_mcp_tool_permission(
        server_id: str,
        tool_name: str,
        payload: MCPToolPermissionRequest,
        _: None = Depends(authenticate),
    ) -> dict[str, Any]:
        try:
            return (
                MCPRegistry(run_store)
                .set_permission(server_id, tool_name, payload.permission, payload.schema_sha256)
                .model_dump()
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="MCP tool not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/mcp/servers/{server_id}/secrets/{env_name}")
    def set_mcp_secret(
        server_id: str, env_name: str, payload: MCPSecretRequest, _: None = Depends(authenticate)
    ) -> dict[str, bool]:
        server = MCPRegistry(run_store).get_server(server_id)
        if server is None:
            raise HTTPException(status_code=404, detail="MCP server not found.")
        if env_name not in server.secret_env:
            raise HTTPException(
                status_code=400, detail="This environment name is not configured as a secret."
            )
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        try:
            credential_store.set(payload.value.get_secret_value(), f"mcp:{server_id}:{env_name}")
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Could not save the OS credential."
            ) from exc
        return {"configured": True}

    @app.delete("/mcp/servers/{server_id}/secrets/{env_name}")
    def delete_mcp_secret(
        server_id: str, env_name: str, _: None = Depends(authenticate)
    ) -> Response:
        server = MCPRegistry(run_store).get_server(server_id)
        if server is None:
            raise HTTPException(status_code=404, detail="MCP server not found.")
        if env_name not in server.secret_env:
            raise HTTPException(
                status_code=400, detail="This environment name is not configured as a secret."
            )
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        credential_store.delete(f"mcp:{server_id}:{env_name}")
        return Response(status_code=204)

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
        allow_methods=["GET", "POST", "PUT", "DELETE"],
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

    @app.post("/execution/readiness", response_model=ReadinessResult)
    async def check_execution_readiness(
        request: ReadinessRequest, _: None = Depends(authenticate)
    ) -> ReadinessResult:
        return await execution_readiness.check(request)

    @app.get("/execution/distributions", response_model=DistributionInventory)
    async def execution_distributions(_: None = Depends(authenticate)) -> DistributionInventory:
        return await installed_distributions()

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

    @app.get("/providers/lm-studio/config", response_model=LMStudioConfigRecord)
    def lm_studio_config(_: None = Depends(authenticate)) -> LMStudioConfigRecord:
        with run_store.connection() as connection:
            profile = connection.execute(
                "SELECT endpoint,selected_model FROM provider_profiles WHERE id='lm-studio'"
            ).fetchone()
        if profile is None:
            raise HTTPException(status_code=500, detail="LM Studio configuration is missing.")
        return LMStudioConfigRecord(
            endpoint=profile["endpoint"], selected_model=profile["selected_model"]
        )

    @app.put("/providers/lm-studio/config", response_model=LMStudioConfigRecord)
    async def set_lm_studio_endpoint(
        payload: LMStudioEndpointRequest, _: None = Depends(authenticate)
    ) -> LMStudioConfigRecord:
        try:
            endpoint_value = provider.validate_endpoint(payload.endpoint)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        with run_store.connection() as connection:
            owned_runs = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE lease_owner IS NOT NULL"
            ).fetchone()[0]
        if owned_runs:
            raise HTTPException(
                status_code=409,
                detail="Finish or pause active work before changing the LM Studio server.",
            )
        await provider.configure_endpoint(endpoint_value)
        now = utc_now()
        with run_store.connection() as connection:
            connection.execute(
                "UPDATE provider_profiles SET endpoint=?,selected_model=NULL,updated_at=? WHERE id='lm-studio'",
                (endpoint_value, now),
            )
        return LMStudioConfigRecord(endpoint=endpoint_value, selected_model=None)

    @app.get("/providers/lm-studio/credential", response_model=LMStudioCredentialStatus)
    def lm_studio_credential_status(
        _: None = Depends(authenticate),
    ) -> LMStudioCredentialStatus:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        return LMStudioCredentialStatus(configured=credential_store.get() is not None)

    @app.put("/providers/lm-studio/credential", response_model=LMStudioCredentialStatus)
    async def set_lm_studio_credential(
        payload: LMStudioCredentialRequest, _: None = Depends(authenticate)
    ) -> LMStudioCredentialStatus:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        with run_store.connection() as connection:
            owned_runs = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE lease_owner IS NOT NULL"
            ).fetchone()[0]
        if owned_runs:
            raise HTTPException(
                status_code=409,
                detail="Finish or pause active work before changing the LM Studio credential.",
            )
        token_value = payload.token.get_secret_value()
        try:
            credential_store.set(token_value)
            await provider.configure_credential(token_value)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Could not save the OS credential."
            ) from exc
        return LMStudioCredentialStatus(configured=True)

    @app.delete("/providers/lm-studio/credential", response_model=LMStudioCredentialStatus)
    async def delete_lm_studio_credential(
        _: None = Depends(authenticate),
    ) -> LMStudioCredentialStatus:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        with run_store.connection() as connection:
            owned_runs = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE lease_owner IS NOT NULL"
            ).fetchone()[0]
        if owned_runs:
            raise HTTPException(
                status_code=409,
                detail="Finish or pause active work before changing the LM Studio credential.",
            )
        try:
            credential_store.delete()
            await provider.configure_credential(None)
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Could not clear the OS credential."
            ) from exc
        return LMStudioCredentialStatus(configured=False)

    @app.get("/providers/brave-search/credential", response_model=LMStudioCredentialStatus)
    def brave_search_credential_status(
        _: None = Depends(authenticate),
    ) -> LMStudioCredentialStatus:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        return LMStudioCredentialStatus(configured=credential_store.get("brave-search") is not None)

    @app.put("/providers/brave-search/credential", response_model=LMStudioCredentialStatus)
    def set_brave_search_credential(
        payload: WebSearchCredentialRequest, _: None = Depends(authenticate)
    ) -> LMStudioCredentialStatus:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        with run_store.connection() as connection:
            active = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE lease_owner IS NOT NULL"
            ).fetchone()[0]
        if active:
            raise HTTPException(
                status_code=409,
                detail="Finish or pause active work before changing search credentials.",
            )
        try:
            credential_store.set(payload.token.get_secret_value(), "brave-search")
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Could not save the OS credential."
            ) from exc
        return LMStudioCredentialStatus(configured=True)

    @app.delete("/providers/brave-search/credential", response_model=LMStudioCredentialStatus)
    def delete_brave_search_credential(
        _: None = Depends(authenticate),
    ) -> LMStudioCredentialStatus:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        with run_store.connection() as connection:
            active = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE lease_owner IS NOT NULL"
            ).fetchone()[0]
        if active:
            raise HTTPException(
                status_code=409,
                detail="Finish or pause active work before changing search credentials.",
            )
        try:
            credential_store.delete("brave-search")
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Could not clear the OS credential."
            ) from exc
        return LMStudioCredentialStatus(configured=False)

    @app.put("/providers/lm-studio/selection")
    async def set_lm_studio_model(
        payload: LMStudioSelectionRequest, _: None = Depends(authenticate)
    ) -> dict[str, str | None]:
        if payload.model is not None:
            try:
                available = await provider.models()
            except ProviderError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if payload.model not in {item.id for item in available}:
                raise HTTPException(
                    status_code=422, detail="Selected model is not currently available."
                )
        with run_store.connection() as connection:
            connection.execute(
                "UPDATE provider_profiles SET selected_model=?,updated_at=? WHERE id='lm-studio'",
                (payload.model, utc_now()),
            )
        return {"model": payload.model}

    @app.get("/channels/telegram", response_model=TelegramCredentialStatus)
    def telegram_status(_: None = Depends(authenticate)) -> dict[str, Any]:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        configured = credential_store.get("telegram-bot") is not None
        account_id = (
            credential_store.get("telegram-bot-account") or telegram_store.latest_account_id()
        )
        profile = telegram_store.profile(account_id)
        return {
            "configured": configured,
            "bot_username": profile["username"] if configured else None,
            "linked_users": profile["linked_users"],
            "processed_updates": profile["processed_updates"],
            "deliveries": profile["deliveries"],
        }

    @app.put("/channels/telegram/credential", response_model=TelegramCredentialStatus)
    async def set_telegram_credential(
        payload: TelegramCredentialRequest, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        token = payload.token.get_secret_value()
        try:
            async with TelegramBotApi(token) as api:
                identity = await api.get_me()
            credential_store.set(token, profile="telegram-bot")
            credential_store.set(identity.account_id, profile="telegram-bot-account")
            telegram_store.save_bot(identity)
        except (TelegramApiError, OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=422, detail="Telegram rejected the bot token or is unavailable."
            ) from exc
        profile = telegram_store.profile(identity.account_id)
        return {
            "configured": True,
            "bot_username": profile["username"],
            "linked_users": profile["linked_users"],
            "processed_updates": profile["processed_updates"],
            "deliveries": profile["deliveries"],
        }

    @app.delete("/channels/telegram/credential", response_model=TelegramCredentialStatus)
    def clear_telegram_credential(_: None = Depends(authenticate)) -> dict[str, Any]:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        account_id = (
            credential_store.get("telegram-bot-account") or telegram_store.latest_account_id()
        )
        try:
            credential_store.delete("telegram-bot")
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Could not clear the OS credential."
            ) from exc
        profile = telegram_store.profile(account_id)
        return {
            "configured": False,
            "bot_username": None,
            "linked_users": profile["linked_users"],
            "processed_updates": profile["processed_updates"],
            "deliveries": profile["deliveries"],
        }

    @app.delete("/channels/telegram/links", response_model=TelegramLinkRevocation)
    def revoke_telegram_links(_: None = Depends(authenticate)) -> dict[str, int]:
        if credential_store is None:
            raise HTTPException(status_code=503, detail="OS credential storage is unavailable.")
        account_id = (
            credential_store.get("telegram-bot-account") or telegram_store.latest_account_id()
        )
        if account_id is None:
            return {
                "revoked_users": 0,
                "linked_users": 0,
            }
        revoked = telegram_store.revoke_users(account_id)
        return {
            "revoked_users": revoked,
            "linked_users": telegram_store.profile(account_id)["linked_users"],
        }

    @app.get("/channels/telegram/linked-users", response_model=list[TelegramLinkedUser])
    def list_telegram_linked_users(
        _: None = Depends(authenticate),
    ) -> list[dict[str, str]]:
        if credential_store is None or credential_store.get("telegram-bot") is None:
            return []
        account_id = (
            credential_store.get("telegram-bot-account") or telegram_store.latest_account_id()
        )
        if account_id is None:
            return []
        return [{"user_id": user_id} for user_id in telegram_store.linked_users(account_id)]

    @app.post(
        "/channels/telegram/pairing",
        status_code=status.HTTP_201_CREATED,
        response_model=TelegramPairingCode,
    )
    def create_telegram_pairing(_: None = Depends(authenticate)) -> dict[str, str]:
        if credential_store is None or credential_store.get("telegram-bot") is None:
            raise HTTPException(status_code=409, detail="Configure a Telegram bot token first.")
        code = secrets.token_urlsafe(32)
        expires_at = telegram_store.create_pairing(code)
        return {"code": code, "expires_at": expires_at}

    @app.post("/backups", status_code=status.HTTP_201_CREATED)
    def create_backup(_: None = Depends(authenticate)) -> dict[str, str]:
        return {"path": str(create_backup_bundle(run_store)), "status": "verified"}

    @app.get("/schedules", response_model=list[ScheduleRecord])
    def schedules(_: None = Depends(authenticate)) -> list[dict[str, Any]]:
        return schedule_store.list()

    @app.post("/schedules", status_code=status.HTTP_201_CREATED, response_model=ScheduleRecord)
    def create_schedule(
        payload: CreateScheduleRequest, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            start_at = parse_local_time(payload.start_at)
            ZoneInfo(payload.timezone)
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=422, detail="Choose a valid local time and timezone."
            ) from exc
        workspace = payload.workspace
        selected_workspace: Path | None = None
        if workspace is not None:
            try:
                selected_workspace = Path(workspace).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise HTTPException(
                    status_code=409, detail="Scheduled workspace is unavailable."
                ) from exc
            if not selected_workspace.is_dir():
                raise HTTPException(
                    status_code=422, detail="Scheduled workspace must be an existing directory."
                )
        with run_store.connection() as connection:
            profile = connection.execute(
                "SELECT selected_model FROM provider_profiles WHERE id='lm-studio'"
            ).fetchone()
        if profile is None or profile["selected_model"] != payload.model:
            raise HTTPException(
                status_code=409,
                detail="Select this model in LM Studio setup before scheduling work.",
            )
        delivery_destination: str | None = None
        if payload.telegram_user_id is not None:
            if credential_store is None or credential_store.get("telegram-bot") is None:
                raise HTTPException(
                    status_code=409,
                    detail="Configure a Telegram bot before selecting a delivery destination.",
                )
            account_id = (
                credential_store.get("telegram-bot-account") or telegram_store.latest_account_id()
            )
            if account_id is None or payload.telegram_user_id not in telegram_store.linked_users(
                account_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail="Choose a user currently linked to the configured Telegram bot.",
                )
            delivery_destination = f"telegram:{account_id}:{payload.telegram_user_id}"
        try:
            return schedule_store.create(
                objective=payload.objective,
                acceptance_criteria=payload.acceptance_criteria,
                workspace=str(selected_workspace) if selected_workspace is not None else None,
                model=payload.model,
                start_at=start_at,
                frequency=payload.frequency,
                timezone=payload.timezone,
                missed_run_policy=payload.missed_run_policy,
                max_iterations=payload.max_iterations,
                max_seconds=payload.max_seconds,
                delivery_destination=delivery_destination,
                quiet_hours=validate_quiet_hours(payload.quiet_hours),
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/schedules/{schedule_id}/enabled", response_model=ScheduleRecord)
    def set_schedule_enabled(
        schedule_id: str,
        payload: ScheduleEnabledRequest,
        _: None = Depends(authenticate),
    ) -> dict[str, Any]:
        if not schedule_store.set_enabled(schedule_id, payload.enabled):
            raise HTTPException(status_code=404, detail="Schedule not found.")
        result = schedule_store.get(schedule_id)
        assert result is not None
        return result

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

    @app.get("/specialists/profiles", response_model=list[SpecialistProfileResponse])
    def list_specialist_profiles(_: None = Depends(authenticate)) -> list[dict[str, Any]]:
        return specialist_profiles.list()

    @app.put("/specialists/profiles/{role}/route", response_model=SpecialistProfileResponse)
    async def set_specialist_route(
        role: str,
        payload: SpecialistRouteRequest,
        _: None = Depends(authenticate),
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise HTTPException(status_code=404, detail="Specialist role not found.")
        if payload.model_route is not None:
            try:
                models = await provider.models()
            except ProviderError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if payload.model_route not in {model.id for model in models}:
                raise HTTPException(
                    status_code=409, detail="Specialist model is not currently available."
                )
        return specialist_profiles.set_route(role, payload.model_route)

    @app.put("/specialists/profiles/{role}/instructions", response_model=SpecialistProfileResponse)
    def set_specialist_instructions(
        role: str,
        payload: SpecialistInstructionsRequest,
        _: None = Depends(authenticate),
    ) -> dict[str, Any]:
        try:
            return specialist_profiles.set_instructions(role, payload.system_instructions)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Specialist role not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/specialists/profiles/{role}/memory", response_model=list[MemoryRecord])
    def list_specialist_memory(role: str, _: None = Depends(authenticate)) -> list[dict[str, Any]]:
        if role not in ROLES:
            raise HTTPException(status_code=404, detail="Specialist role not found.")
        return memory_store.search(f"specialist:{role}")

    @app.post("/specialists/profiles/{role}/memory", response_model=MemoryRecord, status_code=201)
    def save_specialist_memory(
        role: str, payload: PreferenceRequest, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise HTTPException(status_code=404, detail="Specialist role not found.")
        try:
            return memory_store.save_preference(f"specialist:{role}", payload.content)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/specialists/profiles/{role}/memory/{memory_id}", status_code=204)
    def delete_specialist_memory(
        role: str, memory_id: str, _: None = Depends(authenticate)
    ) -> Response:
        if role not in {"planner", "researcher", "developer", "reviewer", "tester", "executor"}:
            raise HTTPException(status_code=404, detail="Specialist role not found.")
        if not memory_store.delete(f"specialist:{role}", memory_id):
            raise HTTPException(status_code=404, detail="Specialist memory not found.")
        return Response(status_code=204)

    @app.get("/chat/sessions/{session_id}", response_model=CLIChatSessionRecord)
    def get_cli_chat_session(session_id: str, _: None = Depends(authenticate)) -> dict[str, Any]:
        try:
            record = cli_chat_store.get(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if record is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return record

    @app.post(
        "/chat/sessions/{session_id}/turn",
        response_model=CLIChatSessionRecord,
    )
    def append_cli_chat_turn(
        session_id: str,
        payload: CLIChatTurnRequest,
        _: None = Depends(authenticate),
    ) -> dict[str, Any]:
        try:
            return cli_chat_store.append_turn(
                session_id,
                run_id=payload.run_id,
                model=payload.model,
                workspace=payload.workspace,
                mode=payload.mode,
                user_message=payload.user_message,
                assistant_message=payload.assistant_message,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Chat run not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.delete("/chat/sessions/{session_id}", status_code=204)
    def delete_cli_chat_session(session_id: str, _: None = Depends(authenticate)) -> Response:
        try:
            deleted = cli_chat_store.delete(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        return Response(status_code=204)

    @app.post("/runs", status_code=status.HTTP_201_CREATED, response_model=RunRecord)
    async def create_run(
        payload: CreateRunRequest, _: None = Depends(authenticate)
    ) -> dict[str, Any]:
        try:
            if payload.command_grant is not None:
                execution_readiness.require_grant(payload.command_grant)
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
                command_grant=payload.command_grant.model_dump() if payload.command_grant else None,
                approved_web_hosts=payload.approved_web_hosts,
                approved_web_search=payload.approved_web_search,
                approved_browser=payload.approved_browser,
                approved_mcp_tools=payload.approved_mcp_tools,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(
                status_code=409, detail="Command workspace is unavailable."
            ) from exc
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

    @app.get("/runs/{run_id}/export")
    def export_run_trajectory(run_id: str, _: None = Depends(authenticate)) -> Response:
        secrets_to_redact: list[str] = []
        if credential_store is not None:
            for profile in ("lm-studio", "brave-search", "telegram-bot"):
                value = credential_store.get(profile)
                if value:
                    secrets_to_redact.append(value)
            for server in MCPRegistry(run_store).list_servers():
                for name in server.secret_env:
                    value = credential_store.get(f"mcp:{server.id}:{name}")
                    if value:
                        secrets_to_redact.append(value)
        try:
            trajectory = export_trajectory(run_store, run_id, secrets=tuple(secrets_to_redact))
        except ValueError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        if trajectory is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        content = json.dumps(trajectory, ensure_ascii=False, separators=(",", ":"))
        return Response(
            content=content,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="xaa-run-{run_id}.json"'},
        )

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
        except (ValueError, OSError) as exc:
            raise HTTPException(
                status_code=409, detail="Private specialist evidence could not be verified."
            ) from exc

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

    @app.post(
        "/runs/{run_id}/tasks/{task_id}/integration-preview", response_model=IntegrationPreview
    )
    async def preview_task_integration(
        run_id: str, task_id: str, _: None = Depends(authenticate)
    ) -> IntegrationPreview:
        token = run_store.acquire_lease(run_id, "local-user-integration-preview", seconds=180)
        if token is None:
            raise HTTPException(
                status_code=409, detail="The run is still owned or is not awaiting review."
            )
        try:
            return await integration_preview(run_store, run_id, task_id, token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task not found.") from exc
        except (ValueError, OSError, LeaseLost, FilesystemWorkerError) as exc:
            raise HTTPException(
                status_code=409,
                detail="Integration preview could not be verified. Check the project and refresh the preview.",
            ) from exc
        finally:
            run_store.release_lease(run_id, token)

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

    @app.get("/runs/{run_id}/validation", response_model=ProjectValidationSummary)
    async def get_project_validation(
        run_id: str, _: None = Depends(authenticate)
    ) -> ProjectValidationSummary:
        token = run_store.acquire_lease(run_id, "local-user-project-validation-read", seconds=180)
        if token is None:
            raise HTTPException(status_code=409, detail="The run is currently owned by a worker.")
        try:
            return await project_validation_summary(run_store, run_id, token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found.") from exc
        except (ValueError, OSError, LeaseLost, FilesystemWorkerError) as exc:
            raise HTTPException(
                status_code=409, detail="Current project evidence is unavailable."
            ) from exc
        finally:
            run_store.release_lease(run_id, token)

    @app.post("/runs/{run_id}/validation", response_model=ProjectValidationSummary)
    async def run_project_validation(
        run_id: str, payload: ProjectValidationRequest, _: None = Depends(authenticate)
    ) -> ProjectValidationSummary:
        try:
            await validate_project(run_store, run_id, payload)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found.") from exc
        except (ValueError, OSError, PermissionError, LeaseLost, FilesystemWorkerError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        token = run_store.acquire_lease(run_id, "local-user-project-validation-read", seconds=180)
        if token is None:
            raise HTTPException(
                status_code=409,
                detail="Refresh validation evidence after the run releases its lease.",
            )
        try:
            return await project_validation_summary(run_store, run_id, token)
        finally:
            run_store.release_lease(run_id, token)

    @app.post(
        "/runs/{run_id}/validation/{call_id}/reconcile", response_model=ProjectValidationSummary
    )
    async def reconcile_project_validation(
        run_id: str, call_id: str, _: None = Depends(authenticate)
    ) -> ProjectValidationSummary:
        owner = run_store.acquire_lease(
            run_id, "local-user-project-validation-recovery", seconds=180
        )
        if owner is None:
            raise HTTPException(status_code=409, detail="The run is currently owned by a worker.")
        try:
            journal = CommandJournal(run_store, purpose="project_validation")
            intent = journal.recovery_intent(run_id, owner, call_id)
            transport = await WSLTransport.discover(
                intent.distribution, deadline=time.monotonic() + 30
            )
            if transport.profile_sha256 != intent.profile_sha256:
                raise PermissionError("Execution profile changed; reconciliation is unavailable.")
            await CommandController(run_store, transport, purpose="project_validation").recover(
                run_id, owner, call_id
            )
            return await project_validation_summary(run_store, run_id, owner)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Run or validation call not found."
            ) from exc
        except (
            ValueError,
            OSError,
            PermissionError,
            LeaseLost,
            FilesystemWorkerError,
            TimeoutError,
        ) as exc:
            raise HTTPException(
                status_code=409,
                detail="Validation reconciliation could not confirm a result; refresh and inspect the run.",
            ) from exc
        finally:
            run_store.release_lease(run_id, owner)

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
        extension = "png" if metadata["media_type"] == "image/png" else "txt"
        return Response(
            content=content,
            media_type=metadata["media_type"],
            headers={"Content-Disposition": f'attachment; filename="{artifact_id}.{extension}"'},
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

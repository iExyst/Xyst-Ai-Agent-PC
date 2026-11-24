"""Durable specialist plans sharing the parent run's authority boundary."""

import base64
import hashlib
import json
import re
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Literal, cast
from uuid import uuid4

from xaa.runtime.permissions import workspace_write_authorized
from xaa.runtime.task_graph import TaskDefinition, TaskGraph, TaskStatus
from xaa.storage.mcp import MCPRegistry
from xaa.storage.runs import RunStore, TokenBudgetExceeded, utc_now
from xaa.storage.skills import is_text_asset_media_type, snapshot_digest
from xaa.storage.specialist_profiles import (
    MAX_SPECIALIST_PROCEDURE_CONTEXT_BYTES,
    MAX_SPECIALIST_PROCEDURE_RENDERED_PDF_BYTES,
    MAX_SPECIALIST_PROCEDURE_RESOURCE_BYTES,
    SPECIALIST_IMAGE_MEDIA_TYPES,
    specialist_procedure_resources,
)


class TaskTokenBudgetExceeded(TokenBudgetExceeded):
    """A specialist's own reported token allowance is exhausted or unknown."""


def specialist_procedure_snapshot_digest(snapshot: dict[str, Any]) -> str:
    payload = {key: value for key, value in snapshot.items() if key != "snapshot_sha256"}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def validate_specialist_procedure_snapshot(snapshot: dict[str, Any]) -> None:
    """Fail closed on changed text/resources before exposing a saved role procedure."""
    expected_digest = snapshot.get("snapshot_sha256")
    digest_matches = specialist_procedure_snapshot_digest(snapshot) == expected_digest
    if not digest_matches and "linked_resources" not in snapshot:
        # Pre-resource snapshots used the shared skill manifest digest. Keep queued
        # tasks created by older app versions resumable after an in-place upgrade.
        digest_matches = snapshot_digest(snapshot) == expected_digest
    if not digest_matches:
        raise ValueError("Saved specialist procedure failed its snapshot integrity check.")
    content = snapshot.get("content")
    resources_value: Any = snapshot.get("linked_resources", [])
    credential_names_value: Any = snapshot.get("credential_names", [])
    unavailable_permissions_value: Any = snapshot.get("unavailable_required_permissions", [])
    if not isinstance(content, str) or not isinstance(resources_value, list):
        raise TypeError("Saved specialist procedure has an invalid text/resource shape.")
    if not isinstance(credential_names_value, list):
        raise TypeError("Saved specialist procedure credential grant is invalid.")
    credential_names = cast(list[Any], credential_names_value)
    if (
        len(credential_names) > 32
        or any(
            not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) is None
            for name in credential_names
        )
        or len(set(credential_names)) != len(credential_names)
    ):
        raise ValueError("Saved specialist procedure credential grant is invalid.")
    required_permissions_value: Any = snapshot.get("required_permissions", [])
    if not isinstance(unavailable_permissions_value, list) or not isinstance(
        required_permissions_value, list
    ):
        raise TypeError("Saved specialist procedure permission requirements are invalid.")
    unavailable_permissions = cast(list[Any], unavailable_permissions_value)
    required_permissions = cast(list[Any], required_permissions_value)
    if any(
        not isinstance(permission, str) or permission not in {"workspace.read", "workspace.write"}
        for permission in unavailable_permissions
    ) or any(not isinstance(permission, str) for permission in required_permissions):
        raise ValueError("Saved specialist procedure permission requirements are invalid.")
    unavailable_permission_names = cast(list[str], unavailable_permissions)
    required_permission_names = cast(list[str], required_permissions)
    if len(set(unavailable_permission_names)) != len(unavailable_permission_names) or not set(
        unavailable_permission_names
    ) <= set(required_permission_names):
        raise ValueError("Saved specialist procedure permission requirements are invalid.")
    resources = cast(list[Any], resources_value)
    if len(resources) > 32:
        raise ValueError("Saved specialist procedure has too many linked resources.")
    if len(content.encode("utf-8")) > MAX_SPECIALIST_PROCEDURE_CONTEXT_BYTES:
        raise ValueError("Saved specialist procedure text exceeds the 80 KiB limit.")
    total_resource_bytes = 0
    total_rendered_pdf_bytes = 0
    seen_paths: set[str] = set()
    for resource_value in resources:
        if not isinstance(resource_value, dict):
            raise TypeError("Saved specialist procedure resource is invalid.")
        resource = cast(dict[str, Any], resource_value)
        path = resource.get("path")
        media_type = resource.get("media_type")
        text = resource.get("content")
        encoded = resource.get("content_base64")
        digest = resource.get("sha256")
        if (
            not isinstance(path, str)
            or not path
            or len(path) > 512
            or path in seen_paths
            or not isinstance(media_type, str)
            or len(media_type) > 128
            or not isinstance(digest, str)
        ):
            raise ValueError("Saved specialist procedure resource metadata is invalid.")
        normalized = PurePosixPath(path)
        if (
            normalized.is_absolute()
            or normalized.as_posix() != path
            or "\\" in path
            or any(part in {"", ".", ".."} for part in normalized.parts)
        ):
            raise ValueError("Saved specialist procedure resource path is invalid.")
        seen_paths.add(path)
        source_size_bytes: int | None = None
        if is_text_asset_media_type(media_type) and isinstance(text, str) and encoded is None:
            raw = text.encode("utf-8")
        elif media_type == "application/pdf" and isinstance(text, str) and encoded is None:
            raw = text.encode("utf-8")
            source_size_value = resource.get("source_size_bytes")
            extracted_digest = resource.get("extracted_text_sha256")
            if (
                not isinstance(source_size_value, str)
                or len(source_size_value) > 5
                or not source_size_value.isdecimal()
                or not 1 <= int(source_size_value) <= 64 * 1024
                or re.fullmatch(r"[a-f0-9]{64}", digest) is None
                or not isinstance(extracted_digest, str)
                or not re.fullmatch(r"[a-f0-9]{64}", extracted_digest)
                or hashlib.sha256(raw).hexdigest() != extracted_digest
            ):
                raise ValueError("Saved specialist PDF text failed its integrity check.")
            source_size_bytes = int(source_size_value)
            page_images_value = resource.get("rendered_pages", [])
            if not isinstance(page_images_value, list):
                raise ValueError("Saved specialist PDF page images are invalid.")
            page_images = cast(list[Any], page_images_value)
            if len(page_images) > 4:
                raise ValueError("Saved specialist PDF page images are invalid.")
            previous_page = 0
            for page_image_value in page_images:
                if not isinstance(page_image_value, dict):
                    raise TypeError("Saved specialist PDF page image is invalid.")
                page_image = cast(dict[str, Any], page_image_value)
                page_number = page_image.get("page")
                image_digest = page_image.get("sha256")
                image_base64 = page_image.get("content_base64")
                if (
                    set(page_image) != {"page", "sha256", "content_base64"}
                    or type(page_number) is not int
                    or not previous_page < page_number <= 50
                    or not isinstance(image_digest, str)
                    or re.fullmatch(r"[a-f0-9]{64}", image_digest) is None
                    or not isinstance(image_base64, str)
                ):
                    raise ValueError("Saved specialist PDF page image metadata is invalid.")
                try:
                    image_raw = base64.b64decode(image_base64, validate=True)
                except ValueError as exc:
                    raise ValueError(
                        "Saved specialist PDF page image is not valid base64."
                    ) from exc
                if (
                    not image_raw.startswith(b"\x89PNG\r\n\x1a\n")
                    or len(image_raw) > 2 * 1024 * 1024
                    or hashlib.sha256(image_raw).hexdigest() != image_digest
                ):
                    raise ValueError("Saved specialist PDF page image failed its integrity check.")
                previous_page = page_number
                total_rendered_pdf_bytes += len(image_raw)
                if total_rendered_pdf_bytes > MAX_SPECIALIST_PROCEDURE_RENDERED_PDF_BYTES:
                    raise ValueError("Saved specialist PDF page images exceed the 2 MiB limit.")
        elif (
            media_type in SPECIALIST_IMAGE_MEDIA_TYPES and isinstance(encoded, str) and text is None
        ):
            try:
                raw = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ValueError("Saved specialist image is not valid base64.") from exc
            signature_valid = (
                raw.startswith(b"\x89PNG\r\n\x1a\n")
                if media_type == "image/png"
                else raw.startswith(b"\xff\xd8\xff")
            )
            if not signature_valid:
                raise ValueError("Saved specialist image failed its file signature check.")
        else:
            raise ValueError("Saved specialist procedure resource content is invalid.")
        if len(raw) > 64 * 1024 or (
            media_type != "application/pdf" and hashlib.sha256(raw).hexdigest() != digest
        ):
            raise ValueError("Saved specialist procedure resource failed its integrity check.")
        total_resource_bytes += source_size_bytes if source_size_bytes is not None else len(raw)
    if total_resource_bytes > MAX_SPECIALIST_PROCEDURE_RESOURCE_BYTES:
        raise ValueError("Saved specialist procedure resources exceed the 1 MiB snapshot limit.")
    required_managers_value: Any = snapshot.get("required_dependency_managers", [])
    required_managers = (
        cast(list[str], required_managers_value)
        if isinstance(required_managers_value, list)
        else []
    )
    if (
        not isinstance(required_managers_value, list)
        or any(item not in {"python", "npm"} for item in required_managers)
        or len(set(required_managers)) != len(required_managers)
    ):
        raise ValueError("Saved specialist procedure dependency declarations are invalid.")
    for manager in ("python", "npm"):
        environment_value = snapshot.get(f"{manager}_environment")
        if environment_value is None:
            continue
        if not isinstance(environment_value, dict):
            raise TypeError("Saved specialist procedure environment metadata is invalid.")
        environment = cast(dict[str, Any], environment_value)
        if (
            set(environment) != {"environment_digest", "distribution", "tree_sha256"}
            or not isinstance(environment.get("environment_digest"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", cast(str, environment["environment_digest"]))
            or not isinstance(environment.get("distribution"), str)
            or not environment["distribution"]
            or len(cast(str, environment["distribution"])) > 64
            or not isinstance(environment.get("tree_sha256"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", cast(str, environment["tree_sha256"]))
        ):
            raise ValueError("Saved specialist procedure environment metadata is invalid.")


class TaskStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    @staticmethod
    def _specialist_credential_grants(connection: Any, run_id: str) -> dict[str, dict[str, Any]]:
        row = connection.execute(
            "SELECT payload FROM run_events WHERE run_id=? "
            "AND event_type='specialist.procedure_credentials_granted' ORDER BY sequence LIMIT 1",
            (run_id,),
        ).fetchone()
        if row is None:
            return {}
        value: object = json.loads(row["payload"])
        value_dict = cast(dict[str, Any], value) if isinstance(value, dict) else {}
        grants_value: object = value_dict.get("grants")
        if not isinstance(grants_value, list):
            raise TypeError("Saved specialist credential grants are invalid.")
        grant_items = cast(list[Any], grants_value)
        grants: dict[str, dict[str, Any]] = {}
        for grant_value in grant_items:
            if not isinstance(grant_value, dict):
                raise TypeError("Saved specialist credential grant is invalid.")
            grant_data = cast(dict[str, Any], grant_value)
            role = grant_data.get("role")
            skill_id = grant_data.get("skill_id")
            version = grant_data.get("version")
            names_value = grant_data.get("credential_names")
            names = cast(list[Any], names_value) if isinstance(names_value, list) else None
            if (
                not isinstance(role, str)
                or role
                not in {"planner", "researcher", "developer", "reviewer", "tester", "executor"}
                or role in grants
                or not isinstance(skill_id, str)
                or re.fullmatch(r"[a-f0-9-]{36}", skill_id) is None
                or type(version) is not int
                or not 1 <= version <= 128
                or names is None
                or not names
                or len(names) > 32
                or any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names)
            ):
                raise ValueError("Saved specialist credential grant is invalid.")
            grants[role] = {
                "skill_id": skill_id,
                "version": version,
                "credential_names": [cast(str, name) for name in names],
            }
        return grants

    @staticmethod
    def _procedure_snapshot(
        connection: Any,
        skill_id: str | None,
        available_permissions: Sequence[str],
        python_environment_digest: str | None = None,
        npm_environment_digest: str | None = None,
        credential_grant: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if skill_id is None:
            return None
        row = connection.execute(
            "SELECT s.name,s.active_version,v.content,v.required_tools,v.required_permissions,"
            "v.evaluation_evidence FROM skills s JOIN skill_versions v "
            "ON v.skill_id=s.id AND v.version=s.active_version "
            "WHERE s.id=? AND s.lifecycle_state='active'",
            (skill_id,),
        ).fetchone()
        if row is None:
            raise ValueError(
                "A specialist procedure is no longer active; update its profile first."
            )
        credential_names: Sequence[str] = ()
        if credential_grant is not None:
            if credential_grant.get("skill_id") != skill_id or credential_grant.get(
                "version"
            ) != int(row["active_version"]):
                raise ValueError(
                    "Specialist credential grant no longer matches its selected procedure."
                )
            credential_names_value = credential_grant.get("credential_names")
            if not isinstance(credential_names_value, list):
                raise ValueError("Specialist credential grant is invalid.")
            credential_names = cast(list[str], credential_names_value)
        content = str(row["content"])
        content_bytes = len(content.encode("utf-8"))
        if content_bytes > MAX_SPECIALIST_PROCEDURE_CONTEXT_BYTES:
            raise ValueError("A specialist procedure exceeds the 80 KiB context limit.")
        resources = specialist_procedure_resources(
            connection, skill_id, int(row["active_version"]), content_bytes
        )
        evidence = json.loads(row["evaluation_evidence"])
        required_permissions = sorted(json.loads(row["required_permissions"]))
        snapshot: dict[str, Any] = {
            "skill_id": skill_id,
            "version": row["active_version"],
            "name": row["name"],
            "content": content,
            "required_tools": json.loads(row["required_tools"]),
            "required_permissions": required_permissions,
            "linked_resources": resources,
        }
        source_value = evidence.get("source")
        source = cast(dict[str, Any], source_value) if isinstance(source_value, dict) else {}
        declarations_value = source.get("declared_requirements")
        try:
            declarations = (
                json.loads(declarations_value)
                if isinstance(declarations_value, str) and len(declarations_value) <= 8192
                else None
            )
        except json.JSONDecodeError:
            declarations = None
        declared = cast(dict[str, Any], declarations) if isinstance(declarations, dict) else {}
        declared_credentials: set[str] = set()
        for key in ("required_environment_names", "optional_environment_names"):
            raw_names = declared.get(key)
            if isinstance(raw_names, list):
                declared_credentials.update(
                    name
                    for name in cast(list[Any], raw_names)
                    if isinstance(name, str) and re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", name)
                )
        granted_credentials = list(credential_names)
        if (
            len(granted_credentials) > 32
            or len(set(granted_credentials)) != len(granted_credentials)
            or any(name not in declared_credentials for name in granted_credentials)
        ):
            raise ValueError("Specialist credential grant does not match its selected procedure.")
        if granted_credentials:
            snapshot["credential_names"] = granted_credentials
        required_dependency_managers = [
            manager
            for manager, key in (
                ("python", "python_dependency_specs"),
                ("npm", "npm_dependency_specs"),
            )
            if isinstance(declared.get(key), list) and bool(declared.get(key))
        ]
        if required_dependency_managers:
            snapshot["required_dependency_managers"] = required_dependency_managers
        for manager, digest in (
            ("python", python_environment_digest),
            ("npm", npm_environment_digest),
        ):
            if digest is None:
                continue
            lock_table = (
                "skill_python_dependency_locks"
                if manager == "python"
                else "skill_npm_dependency_locks"
            )
            environment_table = (
                "skill_python_dependency_environments"
                if manager == "python"
                else "skill_npm_dependency_environments"
            )
            environment = connection.execute(
                f"SELECT environment.environment_digest,environment.distribution,environment.tree_sha256 "
                f"FROM {lock_table} AS lock JOIN {environment_table} AS environment "
                "USING(skill_id,skill_version,environment_digest) WHERE lock.skill_id=? "
                "AND lock.skill_version=? AND lock.environment_digest=? AND lock.decision='approved'",
                (skill_id, int(row["active_version"]), digest),
            ).fetchone()
            if environment is None:
                raise ValueError(
                    f"The selected specialist procedure {manager} environment is no longer approved or installed."
                )
            snapshot[f"{manager}_environment"] = dict(environment)
        unavailable_permissions = sorted(set(required_permissions) - set(available_permissions))
        if unavailable_permissions:
            snapshot["unavailable_required_permissions"] = unavailable_permissions
        for key in ("description", "category"):
            if evidence.get(key):
                snapshot[key] = evidence[key]
        snapshot["snapshot_sha256"] = specialist_procedure_snapshot_digest(snapshot)
        return snapshot

    def remaining_run_seconds(self, run_id: str) -> int:
        run = self.runs.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        events = self.runs.list_events(run_id)
        planning_start = next(
            (
                event["created_at"]
                for event in events
                if event["event_type"] == "run.state_changed"
                and event["payload"].get("to") == "planning"
            ),
            run["created_at"],
        )
        started_at = datetime.fromisoformat(planning_start)
        elapsed = max(0.0, (datetime.now(UTC) - started_at).total_seconds())
        return max(0, int(run["limits"]["max_seconds"] - elapsed))

    @staticmethod
    def _task_elapsed_seconds(events: list[dict[str, Any]]) -> float:
        elapsed = 0.0
        started_at: datetime | None = None
        for event in events:
            if event["payload"].get("task_id") is None:
                continue
            if event["event_type"] == "task.claimed" and started_at is None:
                started_at = datetime.fromisoformat(event["created_at"])
            elif started_at is not None and event["event_type"] in {
                "task.blocked",
                "task.result",
                "task.recovery_decided",
                "task.delegated",
            }:
                elapsed += max(
                    0.0,
                    (datetime.fromisoformat(event["created_at"]) - started_at).total_seconds(),
                )
                started_at = None
        if started_at is not None:
            elapsed += max(0.0, (datetime.now(UTC) - started_at).total_seconds())
        return elapsed

    @classmethod
    def _task_resource_usage(
        cls, events: list[dict[str, Any]], budgets: dict[str, Any]
    ) -> dict[str, Any]:
        attempts = sum(event["event_type"] == "task.inference.started" for event in events)
        answers = [
            event
            for event in events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
        ]
        measured = [event for event in answers if event["payload"].get("usage") is not None]
        measured_tokens = sum(event["payload"]["usage"]["total_tokens"] for event in measured)
        usage_measured = attempts == len(measured) and len(answers) == len(measured)
        iteration_cap = budgets["max_iterations"]
        time_cap = budgets["max_seconds"]
        elapsed = cls._task_elapsed_seconds(events)
        token_cap = budgets.get("max_total_tokens")
        return {
            "inference_attempts": attempts,
            "iterations_remaining": max(0, iteration_cap - attempts),
            "elapsed_seconds": elapsed,
            "seconds_remaining": max(0.0, time_cap - elapsed),
            "measured_tokens": measured_tokens,
            "tokens_remaining": (
                max(0, token_cap - measured_tokens)
                if token_cap is not None and usage_measured
                else None
            ),
            "usage_measured": usage_measured,
        }

    def record_read_worker_event(
        self,
        run_id: str,
        parent_token: str,
        task_id: str,
        task_token: str,
        kind: Literal[
            "task.model.delta",
            "task.model.message",
            "task.model.incomplete",
            "task.model.stream_rejected",
            "task.tool.result",
            "task.response_requested",
            "task.procedure.readiness_checked",
        ],
        payload: dict[str, Any],
    ) -> None:
        """Fence read-only specialist output in the same transaction as its publication."""
        if (
            kind
            not in {
                "task.model.delta",
                "task.model.message",
                "task.model.incomplete",
                "task.model.stream_rejected",
                "task.tool.result",
                "task.response_requested",
                "task.procedure.readiness_checked",
            }
            or payload.get("task_id") != task_id
        ):
            raise ValueError(
                "Specialist output must identify its own task and supported event kind."
            )
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            self.runs.require_task_read_scope(connection, run_id, task_id, task_token)
            self.runs.record_event(connection, run_id, kind, payload)

    def check_token_budget(self, run_id: str, task_id: str, *, before_inference: bool) -> None:
        task = next(row for row in self.records(run_id) if row["local_id"] == task_id)
        limit = task["budgets"].get("max_total_tokens")
        if limit is None:
            return
        events = [
            event
            for event in self.runs.list_events(run_id)
            if event["payload"].get("task_id") == task_id
        ]
        attempts = sum(event["event_type"] == "task.inference.started" for event in events)
        answers = [
            event
            for event in events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
        ]
        unknown = attempts > len(answers) or any(
            event["payload"].get("usage") is None for event in answers
        )
        total = sum(
            event["payload"].get("usage", {}).get("total_tokens", 0)
            for event in answers
            if event["payload"].get("usage") is not None
        )
        if unknown or total > limit or (before_inference and total == limit):
            raise TaskTokenBudgetExceeded("Specialist token allowance exhausted or unmeasured.")

    def reserve_inference(
        self, run_id: str, parent_token: str, task_id: str, task_token: str
    ) -> str:
        """Charge an attempt before inference, including interrupted requests."""
        run = self.runs.get_run(run_id)
        assert run is not None
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            row = connection.execute(
                "SELECT tasks.budgets FROM tasks JOIN task_leases ON tasks.id=task_leases.task_id WHERE tasks.id=? AND tasks.run_id=? AND tasks.status='running' AND task_leases.token=? AND task_leases.expires_at>?",
                (f"{run_id}:{task_id}", run_id, task_token, utc_now()),
            ).fetchone()
            if row is None:
                raise PermissionError("Task worker no longer holds a live lease.")
            total = self.runs.count_inference_attempts(connection, run_id)
            own = connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE run_id=? AND event_type='task.inference.started' AND json_extract(payload,'$.task_id')=?",
                (run_id, task_id),
            ).fetchone()[0]
            if (
                total >= run["limits"]["max_iterations"]
                or own >= json.loads(row[0])["max_iterations"]
            ):
                raise ValueError("Shared or specialist iteration budget exhausted.")
            inference_id = str(uuid4())
            self.runs.record_event(
                connection,
                run_id,
                "task.inference.started",
                {"task_id": task_id, "inference_id": inference_id},
            )
            return inference_id

    def reserve_tokens(
        self,
        run_id: str,
        parent_token: str,
        inference_id: str,
        prompt_tokens: int,
        output_tokens: int,
        *,
        task_id: str,
        task_token: str,
    ) -> None:
        try:
            self.runs.reserve_tokens(
                run_id,
                parent_token,
                inference_id,
                prompt_tokens,
                output_tokens,
                task_id=task_id,
                task_token=task_token,
            )
        except TokenBudgetExceeded as exc:
            raise TaskTokenBudgetExceeded(str(exc)) from None

    def renew_worker(self, run_id: str, parent_token: str, task_id: str, task_token: str) -> None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            changed = connection.execute(
                "UPDATE task_leases SET expires_at=? WHERE task_id=? AND token=? AND expires_at>? AND EXISTS (SELECT 1 FROM tasks WHERE id=task_id AND run_id=? AND status='running')",
                (
                    (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                    f"{run_id}:{task_id}",
                    task_token,
                    utc_now(),
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise PermissionError("Task worker no longer holds a live lease.")

    def recover_expired(self, run_id: str, parent_token: str) -> list[str]:
        """Fence lost workers without assuming their external actions failed."""
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            rows = connection.execute(
                "SELECT tasks.id FROM tasks LEFT JOIN task_leases ON tasks.id=task_leases.task_id WHERE tasks.run_id=? AND tasks.status='running' AND (task_leases.expires_at IS NULL OR task_leases.expires_at<=?)",
                (run_id, utc_now()),
            ).fetchall()
            recovered: list[str] = []
            for row in rows:
                identity = row[0]
                local_id = identity[len(run_id) + 1 :]
                connection.execute(
                    "UPDATE tasks SET status='blocked', updated_at=? WHERE id=?",
                    (utc_now(), identity),
                )
                connection.execute("DELETE FROM task_leases WHERE task_id=?", (identity,))
                self.runs.record_event(
                    connection,
                    run_id,
                    "task.blocked",
                    {
                        "task_id": local_id,
                        "reason": "worker_lost_reconciliation_required",
                    },
                )
                recovered.append(local_id)
            return recovered

    def propagate_dependency_blocks(self, run_id: str, parent_token: str) -> None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            _, blocked = self.readiness(run_id)
            for local_id in blocked:
                changed = connection.execute(
                    "UPDATE tasks SET status='blocked', updated_at=? WHERE id=? AND status='queued'",
                    (utc_now(), f"{run_id}:{local_id}"),
                ).rowcount
                if changed:
                    self.runs.record_event(
                        connection,
                        run_id,
                        "task.blocked",
                        {"task_id": local_id, "reason": "dependency_failed"},
                    )

    def claim_ready(self, run_id: str, parent_token: str) -> dict[str, Any] | None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            records = self.records(run_id)
            if not records:
                return None
            ready, blocked = self.readiness(run_id)
            for local_id in blocked:
                connection.execute(
                    "UPDATE tasks SET status='blocked', updated_at=? WHERE id=? AND status='queued'",
                    (utc_now(), f"{run_id}:{local_id}"),
                )
                self.runs.record_event(
                    connection,
                    run_id,
                    "task.blocked",
                    {"task_id": local_id, "reason": "dependency_failed"},
                )
            if not ready:
                return None
            resumable_ids = {
                event["payload"].get("task_id")
                for event in self.runs.list_events(run_id)
                if event["event_type"] == "task.delegated"
                or (
                    event["event_type"] == "task.recovery_decided"
                    and event["payload"].get("decision") == "resume"
                )
            }
            local_id = next((identity for identity in ready if identity in resumable_ids), ready[0])
            record = next(row for row in records if row["local_id"] == local_id)
            if local_id in resumable_ids:
                task_events = [
                    event
                    for event in self.runs.list_events(run_id)
                    if event["payload"].get("task_id") == local_id
                ]
                task_elapsed = self._task_elapsed_seconds(task_events)
                record["budgets"]["max_seconds"] = max(
                    0.0, record["budgets"]["max_seconds"] - task_elapsed
                )
            identity = record["id"]
            token = str(uuid4())
            expires = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
            connection.execute(
                "UPDATE tasks SET status='running', updated_at=? WHERE id=? AND status='queued'",
                (utc_now(), identity),
            )
            connection.execute("INSERT INTO task_leases VALUES (?,?,?)", (identity, token, expires))
            self.runs.record_event(connection, run_id, "task.claimed", {"task_id": local_id})
            return {"task": {**record, "status": "running"}, "token": token, "expires_at": expires}

    def record_outcome(
        self,
        run_id: str,
        parent_token: str,
        task_id: str,
        task_token: str,
        *,
        state: str,
        result: dict[str, Any],
    ) -> None:
        if state not in {"verifying", "failed", "blocked"}:
            raise ValueError(
                "Workers may submit only a candidate result, failure or blocked outcome."
            )
        identity = f"{run_id}:{task_id}"
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            lease = connection.execute(
                "SELECT 1 FROM task_leases WHERE task_id=? AND token=? AND expires_at>?",
                (identity, task_token, utc_now()),
            ).fetchone()
            if lease is None:
                raise PermissionError("Task worker no longer holds a live lease.")
            if state == "verifying":
                task_row = connection.execute(
                    "SELECT input_artifacts FROM tasks WHERE id=? AND run_id=?",
                    (identity, run_id),
                ).fetchone()
                assigned_inputs = set(json.loads(task_row["input_artifacts"]))
                required_inputs = {
                    row["id"]
                    for row in connection.execute(
                        "SELECT id,media_type FROM artifacts WHERE run_id=?",
                        (run_id,),
                    ).fetchall()
                    if row["id"] in assigned_inputs and row["media_type"] == "text/plain"
                }
                observed: dict[str, tuple[int, list[tuple[int, int]]]] = {}
                if required_inputs:
                    for event in connection.execute(
                        "SELECT payload FROM run_events WHERE run_id=? AND event_type='task.tool.result' AND json_extract(payload,'$.task_id')=?",
                        (run_id, task_id),
                    ).fetchall():
                        payload = cast(dict[str, Any], json.loads(event["payload"]))
                        read_value = payload.get("read_input_artifact")
                        result_value = payload.get("result")
                        if not isinstance(read_value, dict) or not isinstance(result_value, dict):
                            continue
                        read = cast(dict[str, Any], read_value)
                        result_page = cast(dict[str, Any], result_value)
                        artifact_id = read.get("artifact_id")
                        offset = result_page.get("offset")
                        count = result_page.get("characters_read")
                        total = result_page.get("total_characters")
                        if (
                            not isinstance(artifact_id, str)
                            or artifact_id not in required_inputs
                            or not isinstance(offset, int)
                            or not isinstance(count, int)
                            or not isinstance(total, int)
                            or offset < 0
                            or count < 0
                            or total < 0
                            or read.get("eof") is not (offset + count >= total)
                        ):
                            continue
                        previous = observed.get(artifact_id)
                        if previous is None:
                            observed[artifact_id] = (total, [(offset, offset + count)])
                        elif previous[0] == total:
                            previous[1].append((offset, offset + count))
                    for artifact_id in required_inputs:
                        page_data = observed.get(artifact_id)
                        if page_data is None:
                            raise ValueError(
                                "Read every assigned input artifact before submitting a candidate."
                            )
                        total, intervals = page_data
                        cursor = 0
                        for start, end in sorted(intervals):
                            if start > cursor:
                                break
                            cursor = max(cursor, end)
                        if cursor < total:
                            raise ValueError(
                                "Read every page of each assigned input artifact before submitting a candidate."
                            )
            changed = connection.execute(
                "UPDATE tasks SET status=?, result=?, updated_at=? WHERE id=? AND run_id=? AND status='running'",
                (state, json.dumps(result), utc_now(), identity, run_id),
            ).rowcount
            if changed != 1:
                raise ValueError("Task is not running.")
            connection.execute("DELETE FROM task_leases WHERE task_id=?", (identity,))
            self.runs.record_event(
                connection, run_id, "task.result", {"task_id": task_id, "state": state}
            )

    def validate_plan(
        self,
        run_id: str,
        tasks: list[TaskDefinition],
        *,
        remaining_tokens: bool = True,
        remaining_seconds: bool = True,
    ) -> TaskGraph:
        run = self.runs.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        for task in tasks:
            if not task.input_artifacts:
                continue
            placeholders = ",".join("?" for _ in task.input_artifacts)
            with self.runs.connection() as connection:
                rows = connection.execute(
                    f"SELECT id,task_id,media_type,size_bytes FROM artifacts WHERE run_id=? AND id IN ({placeholders})",
                    (run_id, *task.input_artifacts),
                ).fetchall()
            if len(rows) != len(task.input_artifacts):
                raise ValueError(
                    "Task input artifacts must reference artifacts already saved in this run."
                )
            if any(row["task_id"] is not None for row in rows):
                raise ValueError("Task inputs must be unowned artifacts from the same run.")
            if any(
                row["media_type"] not in {"text/plain", "image/png", "image/jpeg"}
                or (row["media_type"] == "text/plain" and row["size_bytes"] > 4 * 1024 * 1024)
                or (
                    row["media_type"] in {"image/png", "image/jpeg"}
                    and row["size_bytes"] > 3 * 1024 * 1024
                )
                for row in rows
            ):
                raise ValueError(
                    "Task inputs must be supported unowned text or image artifacts within size limits."
                )
            image_bytes = sum(
                row["size_bytes"]
                for row in rows
                if row["media_type"] in {"image/png", "image/jpeg"}
            )
            if image_bytes > 6 * 1024 * 1024:
                raise ValueError("Assigned images exceed the 6 MiB combined specialist limit.")
            if image_bytes and run["limits"].get("pre_inference_token_reservations") is True:
                raise ValueError(
                    "Specialist image inputs are unavailable with exact text-token admission."
                )
        permissions = {"workspace.read"}
        if run.get("approved_web_hosts"):
            permissions.add("web.read")
        if run.get("approved_web_search"):
            permissions.add("web.search")
        if run.get("approved_browser") and run.get("approved_web_hosts"):
            permissions.add("web.browser.read")
        permissions.update(
            tool.qualified_name
            for tool in MCPRegistry(self.runs).list_tools()
            if tool.permission == "read"
        )
        if workspace_write_authorized(run):
            permissions.add("workspace.write")
            if run.get("command_grant") is not None:
                permissions.add("workspace.command")
        return TaskGraph(
            tasks,
            parent_permissions=permissions,
            iteration_budget=run["limits"]["max_iterations"],
            seconds_budget=(
                self.remaining_run_seconds(run_id)
                if remaining_seconds
                else run["limits"]["max_seconds"]
            ),
            token_budget=(
                max(
                    0,
                    run["limits"]["max_total_tokens"]
                    - (self.runs.token_usage(run_id)["total_tokens"] if remaining_tokens else 0),
                )
                if run["limits"]["max_total_tokens"] is not None
                else None
            ),
            output_limit=run["limits"]["max_output_tokens"],
        )

    def save_plan(
        self, run_id: str, token: str, tasks: list[TaskDefinition], *, parent_headroom: int = 0
    ) -> None:
        graph = self.validate_plan(run_id, tasks)
        run = self.runs.get_run(run_id)
        assert run is not None
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, token)
            if sum(task.max_seconds for task in tasks) > self.remaining_run_seconds(run_id):
                raise ValueError("Task reservations exceed the remaining shared time budget.")
            if (
                parent_headroom < 0
                or sum(task.max_iterations for task in tasks)
                + parent_headroom
                + self.runs.count_inference_attempts(connection, run_id)
                > run["limits"]["max_iterations"]
            ):
                raise ValueError("Task reservations exceed the remaining shared iteration budget.")
            if connection.execute(
                "SELECT 1 FROM tasks WHERE run_id=? LIMIT 1", (run_id,)
            ).fetchone():
                raise ValueError("A saved plan cannot be silently replaced.")
            specialist_credential_grants = self._specialist_credential_grants(connection, run_id)
            specialist_profiles = {
                str(row["role"]): dict(row)
                for row in connection.execute(
                    "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,"
                    "s.active_version AS procedure_version,p.procedure_python_environment_digest,"
                    "p.procedure_npm_environment_digest FROM specialist_profiles p "
                    "LEFT JOIN skills s ON s.id=p.procedure_skill_id"
                ).fetchall()
            }
            profile_instructions = {
                str(role): str(row["system_instructions"])
                for role, row in specialist_profiles.items()
            }
            remaining = list(graph.tasks.values())
            saved: set[str] = set()
            effective_budgets: dict[str, dict[str, int | None]] = {}
            while remaining:
                saveable = [
                    task for task in remaining if task.parent_id is None or task.parent_id in saved
                ]
                if not saveable:
                    raise ValueError("Task hierarchy contains an unresolved parent.")
                for task in saveable:
                    inherited = (
                        effective_budgets[task.parent_id] if task.parent_id is not None else {}
                    )
                    effective_budgets[task.id] = {
                        "max_total_tokens": task.max_total_tokens
                        if task.max_total_tokens is not None
                        else inherited.get("max_total_tokens"),
                        "max_output_tokens": task.max_output_tokens
                        if task.max_output_tokens is not None
                        else inherited.get("max_output_tokens"),
                    }
                    connection.execute(
                        "INSERT INTO tasks (id,run_id,parent_task_id,objective,acceptance_criteria,input_artifacts,expected_outputs,assigned_role,identity_name,model_route,identity_instructions,procedure_snapshot,workspace,permission_scope,budgets,status,checkpoint,result,created_at,updated_at,schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, ?, 1)",
                        (
                            f"{run_id}:{task.id}",
                            run_id,
                            f"{run_id}:{task.parent_id}" if task.parent_id is not None else None,
                            task.objective,
                            json.dumps(task.acceptance_criteria),
                            json.dumps(task.input_artifacts),
                            json.dumps(task.expected_outputs),
                            task.role,
                            specialist_profiles.get(task.role, {}).get("display_name") or task.role,
                            specialist_profiles.get(task.role, {}).get("model_route")
                            or run["model"],
                            profile_instructions.get(task.role, ""),
                            json.dumps(procedure_snapshot, ensure_ascii=False)
                            if (
                                procedure_snapshot := self._procedure_snapshot(
                                    connection,
                                    specialist_profiles.get(task.role, {}).get(
                                        "procedure_skill_id"
                                    ),
                                    task.permissions,
                                    specialist_profiles.get(task.role, {}).get(
                                        "procedure_python_environment_digest"
                                    ),
                                    specialist_profiles.get(task.role, {}).get(
                                        "procedure_npm_environment_digest"
                                    ),
                                    specialist_credential_grants.get(task.role),
                                )
                            )
                            is not None
                            else None,
                            run["workspace"],
                            json.dumps(task.permissions),
                            json.dumps(
                                {
                                    "max_iterations": task.max_iterations,
                                    "max_seconds": task.max_seconds,
                                    **(
                                        {
                                            "max_total_tokens": effective_budgets[task.id][
                                                "max_total_tokens"
                                            ]
                                        }
                                        if effective_budgets[task.id]["max_total_tokens"]
                                        is not None
                                        else {}
                                    ),
                                    **(
                                        {
                                            "max_output_tokens": effective_budgets[task.id][
                                                "max_output_tokens"
                                            ]
                                        }
                                        if effective_budgets[task.id]["max_output_tokens"]
                                        is not None
                                        else {}
                                    ),
                                }
                            ),
                            now,
                            now,
                        ),
                    )
                    saved.add(task.id)
                    remaining.remove(task)
            for task in graph.tasks.values():
                for dependency in task.dependencies:
                    connection.execute(
                        "INSERT INTO task_dependencies VALUES (?,?)",
                        (f"{run_id}:{task.id}", f"{run_id}:{dependency}"),
                    )
            self.runs.record_event(
                connection,
                run_id,
                "plan.saved",
                {
                    "task_ids": list(graph.tasks),
                    "parent_ids": {task.id: task.parent_id for task in graph.tasks.values()},
                },
            )

    def delegate_subtasks(
        self,
        run_id: str,
        parent_token: str,
        task_id: str,
        task_token: str,
        call_id: str,
        reason: str,
        proposed: Sequence[TaskDefinition],
    ) -> dict[str, Any]:
        """Atomically extend a running specialist's plan and yield it to its children."""
        if not call_id or len(call_id) > 128:
            raise ValueError("Delegation requires a valid provider tool-call ID.")
        if not reason.strip() or len(reason) > 1000:
            raise ValueError("Delegation requires a concise reason.")
        if not proposed or len(proposed) > 16:
            raise ValueError("A specialist may add between one and sixteen subtasks at a time.")
        parent = next((item for item in self.records(run_id) if item["local_id"] == task_id), None)
        if parent is None:
            raise ValueError("Delegating task does not exist in this run.")

        proposed_ids = {item.id for item in proposed}
        normalized: list[TaskDefinition] = []
        for item in proposed:
            parent_id = task_id if item.parent_id is None else item.parent_id
            if parent_id != task_id and parent_id not in proposed_ids:
                raise ValueError("New subtasks must belong to the active task's new subtree.")
            normalized.append(item.model_copy(update={"parent_id": parent_id}))
        argument_value = {
            "reason": reason.strip(),
            "tasks": [item.model_dump(mode="json") for item in normalized],
        }
        encoded_arguments = json.dumps(argument_value, sort_keys=True, ensure_ascii=False)
        if len(encoded_arguments.encode("utf-8")) > 64 * 1024:
            raise ValueError("Delegated task proposal exceeds the 64 KiB plan limit.")

        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, parent_token)
            self.runs.require_task_read_scope(connection, run_id, task_id, task_token)
            if (
                connection.execute(
                    "SELECT 1 FROM tasks WHERE run_id=? AND id=? AND status='running'",
                    (run_id, f"{run_id}:{task_id}"),
                ).fetchone()
                is None
            ):
                raise PermissionError("Only a running specialist can add subtasks.")
            if (
                connection.execute(
                    "SELECT 1 FROM tool_calls WHERE run_id=? AND provider_call_id=?",
                    (run_id, call_id),
                ).fetchone()
                is not None
            ):
                raise ValueError("Delegation tool call ID already belongs to an action.")

            records = self.records(run_id)
            definitions = [
                TaskDefinition(
                    id=item["local_id"],
                    objective=item["objective"],
                    acceptance_criteria=item["acceptance_criteria"],
                    dependencies=item["dependencies"],
                    parent_id=item["parent_id"],
                    input_artifacts=item["input_artifacts"],
                    expected_outputs=item["expected_outputs"],
                    role=item["assigned_role"],
                    permissions=item["permission_scope"],
                    max_iterations=item["budgets"]["max_iterations"],
                    max_seconds=int(item["budgets"]["max_seconds"]),
                    max_total_tokens=item["budgets"].get("max_total_tokens"),
                    max_output_tokens=item["budgets"].get("max_output_tokens"),
                )
                for item in records
            ]
            definitions.extend(normalized)
            # Validate the combined dependency and hierarchy graph against saved run authority.
            self.validate_plan(run_id, definitions, remaining_tokens=False, remaining_seconds=False)

            run = self.runs.get_run(run_id)
            assert run is not None
            all_events = self.runs.list_events(run_id)
            events_by_task: dict[str, list[dict[str, Any]]] = {}
            for event in all_events:
                event_task_id = event["payload"].get("task_id")
                if isinstance(event_task_id, str):
                    events_by_task.setdefault(event_task_id, []).append(event)
            active_iterations = 0
            active_seconds = 0.0
            active_tokens = 0
            for item in records:
                if item["status"] not in {"queued", "running"}:
                    continue
                usage = self._task_resource_usage(
                    events_by_task.get(item["local_id"], []), item["budgets"]
                )
                active_iterations += usage["iterations_remaining"]
                active_seconds += usage["seconds_remaining"]
                token_cap = item["budgets"].get("max_total_tokens")
                if token_cap is not None and usage["usage_measured"]:
                    active_tokens += usage["tokens_remaining"] or 0

            remaining_iterations = max(
                0,
                run["limits"]["max_iterations"]
                - self.runs.count_inference_attempts(connection, run_id),
            )
            added_iterations = sum(item.max_iterations for item in normalized)
            if active_iterations + added_iterations > remaining_iterations:
                raise ValueError("Delegated subtasks exceed the remaining shared iteration budget.")
            remaining_seconds = self.remaining_run_seconds(run_id)
            added_seconds = sum(item.max_seconds for item in normalized)
            if active_seconds + added_seconds > remaining_seconds:
                raise ValueError("Delegated subtasks exceed the remaining shared time budget.")

            effective: dict[str, dict[str, int | None]] = {
                item["local_id"]: {
                    "max_total_tokens": item["budgets"].get("max_total_tokens"),
                    "max_output_tokens": item["budgets"].get("max_output_tokens"),
                }
                for item in records
            }
            pending = list(normalized)
            while pending:
                saveable = [item for item in pending if item.parent_id in effective]
                if not saveable:
                    raise ValueError("Delegated task hierarchy has an unresolved parent.")
                for item in saveable:
                    inherited = effective[item.parent_id or ""]
                    effective[item.id] = {
                        "max_total_tokens": item.max_total_tokens
                        if item.max_total_tokens is not None
                        else inherited["max_total_tokens"],
                        "max_output_tokens": item.max_output_tokens
                        if item.max_output_tokens is not None
                        else inherited["max_output_tokens"],
                    }
                    pending.remove(item)

            run_token_limit = run["limits"]["max_total_tokens"]
            if run_token_limit is not None:
                run_tokens_used = self.runs.token_usage(run_id)["total_tokens"]
                added_tokens = sum(
                    value["max_total_tokens"] or 0
                    for task_id_value, value in effective.items()
                    if task_id_value in proposed_ids
                )
                if active_tokens + added_tokens > max(0, run_token_limit - run_tokens_used):
                    raise ValueError("Delegated subtasks exceed the remaining shared token budget.")

            specialist_credential_grants = self._specialist_credential_grants(connection, run_id)
            profiles = {
                str(row["role"]): dict(row)
                for row in connection.execute(
                    "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,"
                    "s.active_version AS procedure_version,p.procedure_python_environment_digest,"
                    "p.procedure_npm_environment_digest FROM specialist_profiles p "
                    "LEFT JOIN skills s ON s.id=p.procedure_skill_id"
                ).fetchall()
            }
            inserted: set[str] = set()
            pending = list(normalized)
            while pending:
                saveable = [
                    item
                    for item in pending
                    if item.parent_id == task_id or item.parent_id in inserted
                ]
                if not saveable:
                    raise ValueError("Delegated task hierarchy has an unresolved parent.")
                for item in saveable:
                    inherited = effective[item.parent_id or ""]
                    identity_name = profiles.get(item.role, {}).get("display_name") or item.role
                    model_route = profiles.get(item.role, {}).get("model_route") or run["model"]
                    instructions = profiles.get(item.role, {}).get("system_instructions", "")
                    procedure_snapshot = self._procedure_snapshot(
                        connection,
                        profiles.get(item.role, {}).get("procedure_skill_id"),
                        item.permissions,
                        profiles.get(item.role, {}).get("procedure_python_environment_digest"),
                        profiles.get(item.role, {}).get("procedure_npm_environment_digest"),
                        specialist_credential_grants.get(item.role),
                    )
                    connection.execute(
                        "INSERT INTO tasks (id,run_id,parent_task_id,objective,acceptance_criteria,input_artifacts,expected_outputs,assigned_role,identity_name,model_route,identity_instructions,procedure_snapshot,workspace,permission_scope,budgets,status,checkpoint,result,created_at,updated_at,schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', NULL, NULL, ?, ?, 1)",
                        (
                            f"{run_id}:{item.id}",
                            run_id,
                            f"{run_id}:{item.parent_id}",
                            item.objective,
                            json.dumps(item.acceptance_criteria),
                            json.dumps(item.input_artifacts),
                            json.dumps(item.expected_outputs),
                            item.role,
                            identity_name,
                            model_route,
                            instructions,
                            json.dumps(procedure_snapshot, ensure_ascii=False)
                            if procedure_snapshot is not None
                            else None,
                            run["workspace"],
                            json.dumps(item.permissions),
                            json.dumps(
                                {
                                    "max_iterations": item.max_iterations,
                                    "max_seconds": item.max_seconds,
                                    **(
                                        {"max_total_tokens": inherited["max_total_tokens"]}
                                        if inherited["max_total_tokens"] is not None
                                        else {}
                                    ),
                                    **(
                                        {"max_output_tokens": inherited["max_output_tokens"]}
                                        if inherited["max_output_tokens"] is not None
                                        else {}
                                    ),
                                }
                            ),
                            now,
                            now,
                        ),
                    )
                    inserted.add(item.id)
                    pending.remove(item)
            for item in normalized:
                for dependency in item.dependencies:
                    connection.execute(
                        "INSERT INTO task_dependencies VALUES (?,?)",
                        (f"{run_id}:{item.id}", f"{run_id}:{dependency}"),
                    )

            result = {
                "status": "delegated",
                "parent_task_id": task_id,
                "subtask_ids": [item.id for item in normalized],
                "owner_review_required": True,
                "parent_state": "queued_until_subtasks_are_owner_reviewed",
            }
            connection.execute(
                "INSERT INTO tool_calls (id,task_id,run_id,tool_name,tool_version,arguments,idempotency_key,state,result,error,created_at,updated_at,provider_call_id) VALUES (?, ?, ?, ?, ?, ?, ?, 'intended', NULL, NULL, ?, ?, ?)",
                (
                    str(uuid4()),
                    f"{run_id}:{task_id}",
                    run_id,
                    "task.delegate_subtasks",
                    "1",
                    encoded_arguments,
                    f"task-delegation:{task_id}:{call_id}",
                    now,
                    now,
                    call_id,
                ),
            )
            self.runs.record_event(
                connection,
                run_id,
                "tool.intended",
                {"call_id": call_id, "tool": "task.delegate_subtasks", "internal": True},
            )
            changed = connection.execute(
                "UPDATE tasks SET status='queued',result=NULL,updated_at=? WHERE id=? AND run_id=? AND status='running'",
                (now, f"{run_id}:{task_id}", run_id),
            ).rowcount
            if changed != 1:
                raise PermissionError("Delegating specialist state changed before commit.")
            connection.execute(
                "DELETE FROM task_leases WHERE task_id=? AND token=?",
                (f"{run_id}:{task_id}", task_token),
            )
            connection.execute(
                "UPDATE tool_calls SET state='completed',result=?,updated_at=? WHERE run_id=? AND provider_call_id=? AND state='intended'",
                (json.dumps(result), now, run_id, call_id),
            )
            self.runs.record_event(
                connection, run_id, "tool.completed", {"call_id": call_id, "internal": True}
            )
            self.runs.record_event(
                connection,
                run_id,
                "task.tool.result",
                {"task_id": task_id, "call_id": call_id, "result": result},
            )
            self.runs.record_event(
                connection,
                run_id,
                "task.delegated",
                {
                    "task_id": task_id,
                    "subtask_ids": [item.id for item in normalized],
                    "reason": reason.strip(),
                },
            )
            return result

    def records(self, run_id: str) -> list[dict[str, Any]]:
        prefix = run_id + ":"
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE run_id=? ORDER BY created_at,id", (run_id,)
            ).fetchall()
            recovery_reasons = {
                str(item[0])
                for item in connection.execute(
                    "SELECT json_extract(payload,'$.task_id') FROM run_events WHERE run_id=? AND event_type='task.blocked' AND json_extract(payload,'$.reason') IN (?,?)",
                    (
                        run_id,
                        "worker_lost_reconciliation_required",
                        "parent_worker_lost_reconciliation_required",
                    ),
                ).fetchall()
                if item[0] is not None
            }
            records: list[dict[str, Any]] = []
            for row in rows:
                record = dict(row)
                record["local_id"] = record["id"][len(prefix) :]
                record["parent_id"] = (
                    record["parent_task_id"][len(prefix) :]
                    if record["parent_task_id"] is not None
                    else None
                )
                record.pop("parent_task_id")
                record["dependencies"] = [
                    item[0][len(prefix) :]
                    for item in connection.execute(
                        "SELECT depends_on_task_id FROM task_dependencies WHERE task_id=? ORDER BY depends_on_task_id",
                        (record["id"],),
                    ).fetchall()
                ]
                for field in (
                    "acceptance_criteria",
                    "input_artifacts",
                    "expected_outputs",
                    "permission_scope",
                    "budgets",
                ):
                    record[field] = json.loads(record[field])
                record["procedure_snapshot"] = (
                    json.loads(record["procedure_snapshot"])
                    if record.get("procedure_snapshot")
                    else None
                )
                records.append(record)
        run_events = self.runs.list_events(run_id)
        events_by_task: dict[str, list[dict[str, Any]]] = {}
        for event in run_events:
            event_task_id = event["payload"].get("task_id")
            if isinstance(event_task_id, str):
                events_by_task.setdefault(event_task_id, []).append(event)
        for record in records:
            record["resource_usage"] = self._task_resource_usage(
                events_by_task.get(record["local_id"], []), record["budgets"]
            )
        statuses = {record["local_id"]: record["status"] for record in records}
        for record in records:
            record["blocked_reason"] = None
            if record["status"] != "blocked":
                continue
            outcome: dict[str, Any] = json.loads(record["result"]) if record["result"] else {}
            if record["local_id"] in recovery_reasons:
                record["blocked_reason"] = "worker_lost_reconciliation_required"
            elif "ContextLimitError" in outcome.get("causes", []):
                record["blocked_reason"] = "prompt_count_mismatch"
            elif "ReviewerEvidenceMissing" in outcome.get("causes", []):
                record["blocked_reason"] = "review_evidence_missing"
            elif "TaskTokenBudgetExceeded" in outcome.get("causes", []):
                record["blocked_reason"] = "token_budget_exhausted_or_unmeasured"
            elif "TaskTimeBudgetExceeded" in outcome.get("causes", []):
                record["blocked_reason"] = "task_time_budget_exhausted"
            elif "RunTimeBudgetExceeded" in outcome.get("causes", []):
                record["blocked_reason"] = "run_time_budget_exhausted"
            elif any(
                statuses.get(dependency) in {"failed", "blocked", "cancelled"}
                for dependency in record["dependencies"]
            ) or any(
                child["parent_id"] == record["local_id"]
                and child["status"] in {"failed", "blocked", "cancelled"}
                for child in records
            ):
                record["blocked_reason"] = "dependency_failed"
            else:
                record["blocked_reason"] = "execution_blocked"
        return records

    def cancel_queued(self, run_id: str, task_id: str) -> None:
        """Cancel a queued assignment and its still-queued descendants atomically."""
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status FROM tasks WHERE run_id=? AND id=?",
                (run_id, f"{run_id}:{task_id}"),
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] == "cancelled":
                return
            if row["status"] != "queued":
                raise ValueError("Only a queued specialist task can be cancelled before it starts.")
            descendants = connection.execute(
                "WITH RECURSIVE tree(id) AS ("
                "SELECT id FROM tasks WHERE id=? AND run_id=? "
                "UNION ALL SELECT child.id FROM tasks child JOIN tree parent "
                "ON child.parent_task_id=parent.id WHERE child.run_id=?"
                ") SELECT id FROM tree",
                (f"{run_id}:{task_id}", run_id, run_id),
            ).fetchall()
            now = utc_now()
            for descendant in descendants:
                identity = str(descendant[0])
                changed = connection.execute(
                    "UPDATE tasks SET status='cancelled',updated_at=? "
                    "WHERE run_id=? AND id=? AND status='queued'",
                    (now, run_id, identity),
                ).rowcount
                if changed:
                    local_id = identity[len(run_id) + 1 :]
                    self.runs.record_event(
                        connection,
                        run_id,
                        "task.cancelled",
                        {
                            "task_id": local_id,
                            "reason": "owner_cancelled_before_start",
                            **({"parent_task_id": task_id} if local_id != task_id else {}),
                        },
                    )

    def cancel_running(self, run_id: str, task_id: str) -> None:
        """Fence an active assignment before its asyncio worker is cancelled."""
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            identity = f"{run_id}:{task_id}"
            row = connection.execute(
                "SELECT status FROM tasks WHERE run_id=? AND id=?", (run_id, identity)
            ).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["status"] == "cancelled":
                return
            if row["status"] != "running":
                raise ValueError("Only a running specialist task can be cancelled while active.")
            lease = connection.execute(
                "SELECT 1 FROM task_leases WHERE task_id=?", (identity,)
            ).fetchone()
            if lease is None:
                raise ValueError("The specialist worker is no longer active; refresh the plan.")
            connection.execute(
                "UPDATE tasks SET status='cancelled',updated_at=? WHERE id=? AND status='running'",
                (utc_now(), identity),
            )
            connection.execute("DELETE FROM task_leases WHERE task_id=?", (identity,))
            self.runs.record_event(
                connection,
                run_id,
                "task.cancelled",
                {"task_id": task_id, "reason": "owner_cancelled_while_running"},
            )

    def recovery_summary(self, run_id: str, task_id: str) -> dict[str, Any]:
        """Summarize durable worker evidence without treating a missing result as success."""
        task = next((row for row in self.records(run_id) if row["local_id"] == task_id), None)
        if task is None:
            raise KeyError(task_id)
        if (
            task["status"] != "blocked"
            or task["blocked_reason"] != "worker_lost_reconciliation_required"
        ):
            raise ValueError("This task does not require lost-worker reconciliation.")
        events = [
            event
            for event in self.runs.list_events(run_id)
            if event["payload"].get("task_id") == task_id
        ]
        messages = [
            {"sequence": event["sequence"], "message": event["payload"]["message"]}
            for event in events
            if event["event_type"] == "task.model.message"
            and isinstance(event["payload"].get("message"), dict)
        ]
        calls: list[dict[str, Any]] = [
            call
            for item in messages
            for call in cast(list[dict[str, Any]], item["message"].get("tool_calls", []))
        ]
        proposed_ids: Counter[str] = Counter()
        completed_ids: Counter[str] = Counter(
            event["payload"]["call_id"]
            for event in events
            if event["event_type"] == "task.tool.result"
            and isinstance(event["payload"].get("call_id"), str)
        )
        pending_calls: list[dict[str, str | None]] = []
        for call in calls:
            call_id = call.get("id")
            if not isinstance(call_id, str):
                pending_calls.append({"call_id": None, "name": None})
                continue
            proposed_ids[call_id] += 1
            if completed_ids[call_id] < proposed_ids[call_id]:
                function = cast(dict[str, Any], call.get("function", {}))
                name = function.get("name")
                pending_calls.append(
                    {"call_id": call_id, "name": name if isinstance(name, str) else None}
                )
        candidate_item = next(
            (
                item
                for item in reversed(messages)
                for message in [item["message"]]
                if isinstance(message.get("content"), str)
                and message["content"].strip()
                and not message.get("tool_calls")
            ),
            None,
        )
        candidate = candidate_item["message"]["content"] if candidate_item else None
        last_message_sequence = messages[-1]["sequence"] if messages else -1
        completed_results = [event for event in events if event["event_type"] == "task.tool.result"]
        unresolved = [
            item
            for item in self.runs.unresolved_tool_calls(run_id)
            if item.get("task_id") == task_id
        ]
        pending_command_ids = {
            item["call_id"]
            for item in pending_calls
            if item["name"] == "workspace_run_command" and item["call_id"] is not None
        }
        reservations: dict[str, str] = {}
        for event in self.runs.list_events(run_id):
            payload = event["payload"]
            model_call_id = payload.get("model_call_id")
            command_call_id = payload.get("call_id")
            if (
                event["event_type"] == "execution.reserved"
                and payload.get("task_id") == task_id
                and model_call_id in pending_command_ids
                and isinstance(command_call_id, str)
            ):
                reservations[model_call_id] = command_call_id
        with self.runs.connection() as connection:
            saved_commands = {
                row["provider_call_id"]: row
                for row in connection.execute(
                    "SELECT provider_call_id,tool_name,state,arguments,result FROM tool_calls "
                    "WHERE run_id=? AND task_id=? AND tool_name IN "
                    "('workspace.run_command','workspace.read_file','workspace.list_directory',"
                    "'workspace.write_file','workspace.create_file','workspace.delete_file',"
                    "'web.fetch','web.search','browser.run','mcp.call') "
                    "AND state IN ('intended','completed','failed')",
                    (run_id, f"{run_id}:{task_id}"),
                )
                if row["provider_call_id"]
            }
        recoverable_commands: list[dict[str, Any]] = []
        for model_call_id in pending_command_ids:
            journal_call_id = reservations.get(model_call_id)
            command = saved_commands.get(journal_call_id)
            if command is None:
                continue
            arguments = cast(dict[str, Any], json.loads(command["arguments"]))
            argv = cast(list[Any] | None, arguments.get("argv"))
            if not isinstance(argv, list) or any(not isinstance(item, str) for item in argv):
                continue
            recoverable_commands.append(
                {
                    "model_call_id": model_call_id,
                    "journal_call_id": journal_call_id,
                    "argv": argv,
                    "state": command["state"],
                }
            )
        branch_tools = {
            "workspace_read_file": "workspace.read_file",
            "workspace_list_directory": "workspace.list_directory",
            "workspace_write_file": "workspace.write_file",
            "workspace_write_lines": "workspace.write_file",
            "workspace_create_file": "workspace.create_file",
            "workspace_delete_file": "workspace.delete_file",
            "web_fetch": "web.fetch",
            "web_search": "web.search",
            "browser_run": "browser.run",
        }
        reviewer_read_tools = {
            "review_dependency_file": "review_dependency_file",
            "review_dependency_output": "review_dependency_output",
        }
        recoverable_task_actions: list[dict[str, str]] = []
        scope_permissions = set(task["permission_scope"])
        required_permissions = {
            "workspace.read_file": "workspace.read",
            "workspace.list_directory": "workspace.read",
            "workspace.write_file": "workspace.write",
            "workspace.create_file": "workspace.write",
            "workspace.delete_file": "workspace.write",
            "web.fetch": "web.read",
            "web.search": "web.search",
            "browser.run": "web.browser.read",
        }
        for call in calls:
            model_call_id = call.get("id")
            function = cast(dict[str, Any], call.get("function", {}))
            model_tool_name = cast(str, function.get("name"))
            tool_name = branch_tools.get(model_tool_name)
            if tool_name is None and model_tool_name in scope_permissions:
                tool_name = "mcp.call"
            if tool_name is not None:
                required_permission = (
                    model_tool_name if tool_name == "mcp.call" else required_permissions[tool_name]
                )
                if required_permission not in scope_permissions:
                    tool_name = None
            reviewer_read = (
                model_tool_name in reviewer_read_tools
                and task["assigned_role"] == "reviewer"
                and "workspace.read" in scope_permissions
            )
            if reviewer_read:
                tool_name = reviewer_read_tools[model_tool_name]
            if (
                not isinstance(model_call_id, str)
                or tool_name is None
                or proposed_ids[model_call_id] != 1
            ):
                continue
            journal_call_id = (
                "taskfs-"
                + hashlib.sha256(f"{run_id}:{task_id}:{model_call_id}".encode()).hexdigest()
            )
            saved = saved_commands.get(journal_call_id)
            if reviewer_read:
                if saved is not None:
                    continue
                recoverable_task_actions.append(
                    {
                        "model_call_id": model_call_id,
                        "journal_call_id": journal_call_id,
                        "tool_name": tool_name,
                        "state": "intended",
                    }
                )
                continue
            if (
                saved is None
                or saved["tool_name"] != tool_name
                or completed_ids[model_call_id] >= proposed_ids[model_call_id]
            ):
                continue
            recoverable_state = saved["state"]
            saved_arguments = cast(dict[str, Any], json.loads(saved["arguments"]))
            schema_digest = saved_arguments.get("schema_sha256")
            mcp_read = (
                tool_name == "mcp.call"
                and saved_arguments.get("tool") == model_tool_name
                and saved_arguments.get("permission") == "read"
                and isinstance(schema_digest, str)
                and len(schema_digest) == 64
                and all(character in "0123456789abcdef" for character in schema_digest)
            )
            can_attach_completed = (
                recoverable_state == "completed"
                and saved["result"] is not None
                and (tool_name != "mcp.call" or mcp_read)
            )
            can_seal_read = (
                tool_name
                in {
                    "workspace.read_file",
                    "workspace.list_directory",
                    "web.fetch",
                    "web.search",
                    "browser.run",
                }
                and recoverable_state == "intended"
            ) or (mcp_read and recoverable_state == "intended")
            can_attach_interrupted = (
                tool_name
                in {
                    "workspace.read_file",
                    "workspace.list_directory",
                    "web.fetch",
                    "web.search",
                    "browser.run",
                }
                and recoverable_state == "failed"
                and saved["result"] is not None
                and json.loads(saved["result"]).get("error", {}).get("code") == "interrupted_read"
            ) or (
                mcp_read
                and recoverable_state == "failed"
                and saved["result"] is not None
                and json.loads(saved["result"]).get("error", {}).get("code") == "interrupted_read"
            )
            if not (can_attach_completed or can_seal_read or can_attach_interrupted):
                continue
            action = {
                "model_call_id": model_call_id,
                "journal_call_id": journal_call_id,
                "tool_name": tool_name,
                "state": recoverable_state,
            }
            if tool_name == "mcp.call":
                action["mcp_tool"] = model_tool_name
            recoverable_task_actions.append(action)
        candidate_reviewable = bool(
            candidate_item
            and candidate_item["sequence"] == last_message_sequence
            and not pending_calls
            and not unresolved
        )
        resume_from_start = (
            not messages
            and not calls
            and not completed_results
            and not pending_calls
            and not unresolved
            and not any(event["event_type"] == "task.response_requested" for event in events)
        )
        safe_boundary = resume_from_start or (
            bool(messages)
            and not pending_calls
            and not unresolved
            and (
                not calls
                or (
                    all(completed_ids[call_id] >= count for call_id, count in proposed_ids.items())
                    and completed_results
                    and completed_results[-1]["sequence"] > last_message_sequence
                )
            )
        )
        answered_inferences = {
            event["payload"].get("inference_id")
            for event in events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
            and event["payload"].get("task_id") == task_id
            and event["payload"].get("usage") is not None
        }
        pending_reservations = [
            event["payload"]
            for event in events
            if event["event_type"] == "tokens.reserved"
            and event["payload"].get("task_id") == task_id
            and event["payload"].get("inference_id") not in answered_inferences
        ]
        tokens_reserved = sum(item.get("reserved_tokens", 0) for item in pending_reservations)
        task_attempts = sum(event["event_type"] == "task.inference.started" for event in events)
        task_answers = [
            event
            for event in events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
        ]
        run = self.runs.get_run(run_id)
        assert run is not None
        task_answered_ids = {
            event["payload"].get("inference_id")
            for event in task_answers
            if isinstance(event["payload"].get("inference_id"), str)
        }
        task_reserved_ids = {
            event["payload"].get("inference_id")
            for event in events
            if event["event_type"] == "tokens.reserved"
            and isinstance(event["payload"].get("inference_id"), str)
        }
        unadmitted_task_attempts = {
            event["payload"].get("inference_id")
            for event in events
            if event["event_type"] == "task.inference.started"
            and isinstance(event["payload"].get("inference_id"), str)
            and event["payload"].get("inference_id") not in task_answered_ids
            and event["payload"].get("inference_id") not in task_reserved_ids
        }
        task_token_limit = task["budgets"].get("max_total_tokens")
        task_reported_tokens = sum(
            event["payload"]["usage"]["total_tokens"]
            for event in task_answers
            if event["payload"].get("usage") is not None
        )
        task_unmeasured_calls = max(0, task_attempts - len(task_answers)) + sum(
            event["payload"].get("usage") is None for event in task_answers
        )
        exact_reservations_required = run["limits"].get("pre_inference_token_reservations") is True
        task_usage_unknown = task_unmeasured_calls > 0
        if exact_reservations_required and task_unmeasured_calls == len(unadmitted_task_attempts):
            # Exact mode writes a hold before sending an inference request. An unanswered
            # attempt without a hold is a safe preflight boundary, not unknown model usage.
            task_usage_unknown = False
        task_budget_available = (
            task_attempts < task["budgets"]["max_iterations"]
            and (
                task_token_limit is None
                or (not task_usage_unknown and task_reported_tokens < task_token_limit)
            )
            and self._task_elapsed_seconds(events) < task["budgets"]["max_seconds"]
        )
        run_events = self.runs.list_events(run_id)
        run_answered_ids = {
            event["payload"].get("inference_id")
            for event in run_events
            if event["event_type"] in {"task.model.message", "task.model.incomplete"}
        }
        run_reserved_ids = {
            event["payload"].get("inference_id")
            for event in run_events
            if event["event_type"] == "tokens.reserved"
        }
        planning_starts = [
            event["created_at"]
            for event in run_events
            if event["event_type"] == "run.state_changed"
            and event["payload"].get("to") == "planning"
        ]
        elapsed = (
            datetime.now(UTC)
            - datetime.fromisoformat(planning_starts[0] if planning_starts else run["created_at"])
        ).total_seconds()
        run_usage = self.runs.token_usage(run_id)
        shared_limit = run["limits"]["max_total_tokens"]
        run_unadmitted_attempts = {
            event["payload"].get("inference_id")
            for event in run_events
            if event["event_type"] == "task.inference.started"
            and isinstance(event["payload"].get("inference_id"), str)
            and event["payload"].get("inference_id") not in run_answered_ids
            and event["payload"].get("inference_id") not in run_reserved_ids
        }
        run_usage_unknown = run_usage["unmeasured_calls"] > 0
        if exact_reservations_required and run_usage["unmeasured_calls"] == len(
            run_unadmitted_attempts
        ):
            run_usage_unknown = False
        run_budget_available = (
            self.runs.inference_attempts(run_id) < run["limits"]["max_iterations"]
            and elapsed < run["limits"]["max_seconds"]
            and self.runs.usage_report(run_id)["outstanding_reserved_tokens"] == 0
            and (
                shared_limit is None
                or (not run_usage_unknown and run_usage["total_tokens"] < shared_limit)
            )
        )
        safe_to_resume = (
            safe_boundary
            and candidate is None
            and tokens_reserved == 0
            and task_budget_available
            and run_budget_available
        )
        summary: dict[str, Any] = {
            "task_id": task_id,
            "status": task["status"],
            "candidate": candidate,
            "candidate_reviewable": candidate_reviewable and not unresolved,
            "saved_tool_results": sum(
                event["event_type"] == "task.tool.result" for event in events
            ),
            "pending_model_tool_calls": pending_calls,
            "unresolved_tool_calls": len(unresolved),
            "recoverable_commands": recoverable_commands,
            "recoverable_task_actions": recoverable_task_actions,
            "safe_to_resume": safe_to_resume,
            "resume_from_start": resume_from_start and safe_to_resume,
            "consumed_iterations": task_attempts,
            "run_measured_tokens": self.runs.token_usage(run_id)["total_tokens"],
            "reserved_tokens": tokens_reserved,
        }
        return summary

    def record_recovered_command_result(
        self, run_id: str, token: str, task_id: str, model_call_id: str, journal_call_id: str
    ) -> None:
        """Attach a sealed task command result to its pending model tool call exactly once."""
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, token)
            task = connection.execute(
                "SELECT status FROM tasks WHERE run_id=? AND id=?",
                (run_id, f"{run_id}:{task_id}"),
            ).fetchone()
            blocked = connection.execute(
                "SELECT json_extract(payload,'$.reason') FROM run_events "
                "WHERE run_id=? AND event_type='task.blocked' "
                "AND json_extract(payload,'$.task_id')=? ORDER BY sequence DESC LIMIT 1",
                (run_id, task_id),
            ).fetchone()
            if (
                task is None
                or task["status"] != "blocked"
                or blocked is None
                or blocked[0] != "worker_lost_reconciliation_required"
            ):
                raise ValueError("Task recovery state changed; refresh the plan.")
            tool = connection.execute(
                "SELECT tool_name,state,result FROM tool_calls WHERE run_id=? AND task_id=? AND provider_call_id=?",
                (run_id, f"{run_id}:{task_id}", journal_call_id),
            ).fetchone()
            reservation = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='execution.reserved' AND json_extract(payload,'$.call_id')=?",
                (run_id, journal_call_id),
            ).fetchone()
            if tool is None or tool["tool_name"] != "workspace.run_command":
                raise ValueError("Saved task command is unavailable for reconciliation.")
            if tool["state"] not in {"completed", "failed"} or tool["result"] is None:
                raise ValueError("Saved command still needs backend reconciliation.")
            if reservation is None:
                raise ValueError("Saved task command has no durable reservation.")
            reservation_payload = json.loads(reservation[0])
            if (
                reservation_payload.get("task_id") != task_id
                or reservation_payload.get("model_call_id") != model_call_id
            ):
                raise PermissionError("Saved command belongs to a different task action.")
            model_calls = [
                call
                for row in connection.execute(
                    "SELECT payload FROM run_events WHERE run_id=? AND event_type='task.model.message'",
                    (run_id,),
                )
                for call in json.loads(row[0]).get("message", {}).get("tool_calls", [])
                if json.loads(row[0]).get("task_id") == task_id
                and call.get("id") == model_call_id
                and call.get("function", {}).get("name") == "workspace_run_command"
            ]
            if len(model_calls) != 1:
                raise ValueError("Pending model command is missing or ambiguous.")
            if connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='task.tool.result' AND json_extract(payload,'$.task_id')=? AND json_extract(payload,'$.call_id')=?",
                (run_id, task_id, model_call_id),
            ).fetchone():
                raise ValueError("Task command result was already attached to its model call.")
            result = json.loads(tool["result"])
            self.runs.record_event(
                connection,
                run_id,
                "task.tool.result",
                {
                    "task_id": task_id,
                    "call_id": model_call_id,
                    "journal_id": journal_call_id,
                    "result": result,
                    "recovered": True,
                },
            )

    def record_recovered_task_action_result(
        self, run_id: str, token: str, task_id: str, model_call_id: str, journal_call_id: str
    ) -> None:
        """Attach a saved task action result or seal an interrupted read; never replay it."""
        allowed = {
            "workspace.read_file",
            "workspace.list_directory",
            "workspace.write_file",
            "workspace.create_file",
            "workspace.delete_file",
            "review_dependency_file",
            "review_dependency_output",
            "web.fetch",
            "web.search",
            "browser.run",
            "mcp.call",
        }
        expected_journal_id = (
            "taskfs-" + hashlib.sha256(f"{run_id}:{task_id}:{model_call_id}".encode()).hexdigest()
        )
        if journal_call_id != expected_journal_id:
            raise PermissionError("Saved task action identity does not match its model call.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, token)
            task = connection.execute(
                "SELECT status,permission_scope,assigned_role FROM tasks WHERE run_id=? AND id=?",
                (run_id, f"{run_id}:{task_id}"),
            ).fetchone()
            blocked = connection.execute(
                "SELECT json_extract(payload,'$.reason') FROM run_events "
                "WHERE run_id=? AND event_type='task.blocked' "
                "AND json_extract(payload,'$.task_id')=? ORDER BY sequence DESC LIMIT 1",
                (run_id, task_id),
            ).fetchone()
            if (
                task is None
                or task["status"] != "blocked"
                or blocked is None
                or blocked[0] != "worker_lost_reconciliation_required"
            ):
                raise ValueError("Task recovery state changed; refresh the plan.")
            tool = connection.execute(
                "SELECT tool_name,state,arguments,result FROM tool_calls "
                "WHERE run_id=? AND task_id=? AND provider_call_id=?",
                (run_id, f"{run_id}:{task_id}", journal_call_id),
            ).fetchone()
            model_calls = [
                call
                for row in connection.execute(
                    "SELECT payload FROM run_events WHERE run_id=? AND event_type='task.model.message'",
                    (run_id,),
                )
                for call in json.loads(row[0]).get("message", {}).get("tool_calls", [])
                if json.loads(row[0]).get("task_id") == task_id and call.get("id") == model_call_id
            ]
            if len(model_calls) != 1:
                raise ValueError("Pending model task action is missing or ambiguous.")
            model_tool_name = model_calls[0].get("function", {}).get("name")
            scope_permissions: set[str] = (
                set(cast(list[str], json.loads(task["permission_scope"]))) if task else set()
            )
            expected_tool = {
                "workspace_read_file": "workspace.read_file",
                "workspace_list_directory": "workspace.list_directory",
                "workspace_write_file": "workspace.write_file",
                "workspace_write_lines": "workspace.write_file",
                "workspace_create_file": "workspace.create_file",
                "workspace_delete_file": "workspace.delete_file",
                "review_dependency_file": "review_dependency_file",
                "review_dependency_output": "review_dependency_output",
                "web_fetch": "web.fetch",
                "web_search": "web.search",
                "browser_run": "browser.run",
            }.get(model_tool_name)
            if (
                expected_tool is None
                and isinstance(model_tool_name, str)
                and model_tool_name in scope_permissions
            ):
                expected_tool = "mcp.call"
            if expected_tool not in allowed:
                raise PermissionError("Saved tool is outside task recovery scope.")
            required_permission = (
                model_tool_name
                if expected_tool == "mcp.call"
                else {
                    "workspace.read_file": "workspace.read",
                    "workspace.list_directory": "workspace.read",
                    "workspace.write_file": "workspace.write",
                    "workspace.create_file": "workspace.write",
                    "workspace.delete_file": "workspace.write",
                    "review_dependency_file": "workspace.read",
                    "review_dependency_output": "workspace.read",
                    "web.fetch": "web.read",
                    "web.search": "web.search",
                    "browser.run": "web.browser.read",
                }[expected_tool]
            )
            if required_permission not in scope_permissions:
                raise PermissionError("Task no longer has permission for this saved read.")
            mcp_read = expected_tool == "mcp.call"
            if mcp_read:
                if tool is None:
                    raise ValueError("No saved MCP read action is available to attach.")
                arguments = cast(dict[str, Any], json.loads(tool["arguments"]))
                schema_digest = arguments.get("schema_sha256")
                if (
                    tool["tool_name"] != "mcp.call"
                    or arguments.get("tool") != model_tool_name
                    or arguments.get("permission") != "read"
                    or not isinstance(schema_digest, str)
                    or len(schema_digest) != 64
                    or any(character not in "0123456789abcdef" for character in schema_digest)
                ):
                    raise PermissionError("Saved MCP action is not an inherited read-only call.")
            dependency_read = expected_tool in {
                "review_dependency_file",
                "review_dependency_output",
            }
            if dependency_read:
                if (
                    task["assigned_role"] != "reviewer"
                    or "workspace.read" not in json.loads(task["permission_scope"])
                    or tool is not None
                ):
                    raise PermissionError(
                        "Dependency reads require a reviewer task and no saved effect row."
                    )
                result = {
                    "ok": False,
                    "effect": "read_only",
                    "error": {
                        "code": "interrupted_read",
                        "message": "This dependency read has no saved result. No dependency contents are assumed; request the read again if the dependency remains available.",
                    },
                }
            elif tool is None or tool["tool_name"] not in allowed:
                raise ValueError("No saved task action is available to attach.")
            elif tool["tool_name"] != expected_tool:
                raise PermissionError("Saved action result belongs to a different model call.")
            elif tool["state"] == "intended":
                if (
                    tool["tool_name"]
                    not in {
                        "workspace.read_file",
                        "workspace.list_directory",
                        "web.fetch",
                        "web.search",
                        "browser.run",
                    }
                    and not mcp_read
                ):
                    raise PermissionError("An interrupted write cannot be resolved as a read.")
                if connection.execute(
                    "SELECT 1 FROM run_events WHERE run_id=? AND event_type='tool.executing' "
                    "AND json_extract(payload,'$.call_id')=?",
                    (run_id, journal_call_id),
                ).fetchone():
                    raise ValueError("Read has execution evidence; inspect its saved result first.")
                result = {
                    "ok": False,
                    "effect": "read_only",
                    "error": {
                        "code": "interrupted_read",
                        "message": "This read-only operation has no saved result. Request a new read if still needed; no previous contents are assumed.",
                    },
                }
                connection.execute(
                    "UPDATE tool_calls SET state='failed',result=?,error=?,updated_at=? "
                    "WHERE run_id=? AND provider_call_id=? AND state='intended'",
                    (
                        json.dumps(result),
                        json.dumps(result["error"]),
                        utc_now(),
                        run_id,
                        journal_call_id,
                    ),
                )
                self.runs.record_event(
                    connection,
                    run_id,
                    "tool.failed",
                    {
                        "call_id": journal_call_id,
                        "internal": True,
                        "effect": "read_only",
                        "code": "interrupted_read",
                    },
                )
            else:
                if tool["result"] is None:
                    raise ValueError("Saved task action has no durable result to attach.")
                result = json.loads(tool["result"])
                if (
                    tool["state"] == "failed"
                    and result.get("error", {}).get("code") != "interrupted_read"
                ):
                    raise ValueError(
                        "Only a sealed interrupted read can be attached after failure."
                    )
            if not dependency_read and tool is not None and expected_tool != tool["tool_name"]:
                raise PermissionError("Saved action result belongs to a different model call.")
            if not dependency_read:
                assert tool is not None
                required_permission = (
                    model_tool_name
                    if mcp_read
                    else "workspace.write"
                    if tool["tool_name"]
                    in {"workspace.write_file", "workspace.create_file", "workspace.delete_file"}
                    else "workspace.read"
                )
                if required_permission not in json.loads(task["permission_scope"]):
                    raise PermissionError("Task did not have permission for the saved action.")
            if connection.execute(
                "SELECT 1 FROM run_events WHERE run_id=? AND event_type='task.tool.result' "
                "AND json_extract(payload,'$.task_id')=? AND json_extract(payload,'$.call_id')=?",
                (run_id, task_id, model_call_id),
            ).fetchone():
                raise ValueError("File action result was already attached to its model call.")
            self.runs.record_event(
                connection,
                run_id,
                "task.tool.result",
                {
                    "task_id": task_id,
                    "call_id": model_call_id,
                    "journal_id": journal_call_id,
                    "result": result,
                    "recovered": True,
                },
            )

    def resolve_lost_worker(self, run_id: str, token: str, task_id: str, decision: str) -> None:
        if decision not in {"resume", "review_candidate", "fail"}:
            raise ValueError("Choose resume, review_candidate, or fail.")
        summary = self.recovery_summary(run_id, task_id)
        if decision == "resume" and not summary["safe_to_resume"]:
            raise ValueError(
                "Review the saved candidate or reconcile pending work before resuming."
            )
        if decision == "review_candidate" and not summary["candidate_reviewable"]:
            raise ValueError("No final candidate is available without later or unresolved work.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.runs.require_lease(connection, run_id, token)
            current = next(row for row in self.records(run_id) if row["local_id"] == task_id)
            if (
                current["status"] != "blocked"
                or current["blocked_reason"] != "worker_lost_reconciliation_required"
            ):
                raise ValueError("Task recovery state changed; refresh the plan.")
            destination = {
                "resume": "queued",
                "review_candidate": "verifying",
                "fail": "failed",
            }[decision]
            changed = connection.execute(
                "UPDATE tasks SET status=?,result=CASE WHEN ?='review_candidate' THEN ? ELSE result END,updated_at=? WHERE id=? AND run_id=? AND status='blocked'",
                (
                    destination,
                    decision,
                    json.dumps({"candidate": summary["candidate"]}),
                    utc_now(),
                    f"{run_id}:{task_id}",
                    run_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("Task recovery state changed; refresh the plan.")
            self.runs.record_event(
                connection,
                run_id,
                "task.recovery_decided",
                {
                    "task_id": task_id,
                    "decision": decision,
                    "actor": "local-user",
                    "evidence": summary,
                },
            )

    def readiness(self, run_id: str) -> tuple[list[str], list[str]]:
        records = self.records(run_id)
        tasks = [
            TaskDefinition(
                id=row["local_id"],
                objective=row["objective"],
                acceptance_criteria=row["acceptance_criteria"],
                input_artifacts=row["input_artifacts"],
                expected_outputs=row["expected_outputs"],
                dependencies=row["dependencies"],
                parent_id=row["parent_id"],
                role=row["assigned_role"],
                permissions=row["permission_scope"],
                **row["budgets"],
            )
            for row in records
        ]
        graph = self.validate_plan(run_id, tasks, remaining_tokens=False, remaining_seconds=False)
        return graph.readiness(
            {row["local_id"]: cast(TaskStatus, row["status"]) for row in records}
        )

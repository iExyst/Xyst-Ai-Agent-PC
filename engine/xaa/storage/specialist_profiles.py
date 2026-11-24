"""Persistent named specialist identities and model routes."""

from __future__ import annotations

import base64
import hashlib
from pathlib import PurePosixPath
from typing import Any, Literal

from xaa.runtime.document_input import extract_document_text, render_pdf_scanned_pages
from xaa.storage.runs import RunStore, utc_now
from xaa.storage.skills import is_text_asset_media_type

# Match the skill importer so large, pinned reference procedures can be assigned.
# The provider's exact context admission remains the final guard before inference.
MAX_SPECIALIST_PROCEDURE_CONTEXT_BYTES = 80 * 1024
MAX_SPECIALIST_PROCEDURE_RESOURCES = 32
MAX_SPECIALIST_PROCEDURE_RESOURCE_BYTES = 1024 * 1024
MAX_SPECIALIST_PROCEDURE_RENDERED_PDF_BYTES = 2 * 1024 * 1024
SPECIALIST_IMAGE_MEDIA_TYPES = frozenset({"image/jpeg", "image/png"})


def specialist_procedure_environments(
    connection: Any, skill_id: str, version: int
) -> dict[str, list[dict[str, Any]]]:
    """Return only approved, currently installed dependency environments."""
    python_rows = connection.execute(
        "SELECT environment.environment_digest,environment.distribution,environment.tree_sha256,"
        "environment.installed_bytes,environment.file_count FROM skill_python_dependency_locks AS lock "
        "JOIN skill_python_dependency_environments AS environment USING(skill_id,skill_version,environment_digest) "
        "WHERE lock.skill_id=? AND lock.skill_version=? AND lock.decision='approved' "
        "ORDER BY environment.distribution,environment.environment_digest",
        (skill_id, version),
    ).fetchall()
    npm_rows = connection.execute(
        "SELECT environment.environment_digest,environment.distribution,environment.tree_sha256,"
        "environment.installed_bytes,environment.file_count,environment.node_version,environment.npm_version "
        "FROM skill_npm_dependency_locks AS lock JOIN skill_npm_dependency_environments AS environment "
        "USING(skill_id,skill_version,environment_digest) WHERE lock.skill_id=? AND lock.skill_version=? "
        "AND lock.decision='approved' ORDER BY environment.distribution,environment.environment_digest",
        (skill_id, version),
    ).fetchall()
    return {
        "python": [dict(row) for row in python_rows],
        "npm": [dict(row) for row in npm_rows],
    }


def specialist_procedure_resources(
    connection: Any, skill_id: str, version: int, content_bytes: int
) -> list[dict[str, Any]]:
    """Snapshot digest-checked text, text PDFs, and PNG/JPEG resources for specialists."""
    rows = connection.execute(
        "SELECT relative_path,media_type,size_bytes,sha256,content FROM skill_version_assets "
        "WHERE skill_id=? AND version=? ORDER BY relative_path LIMIT 33",
        (skill_id, version),
    ).fetchall()
    if len(rows) > MAX_SPECIALIST_PROCEDURE_RESOURCES:
        raise ValueError("Specialist procedures may contain at most 32 linked text resources.")
    if content_bytes > MAX_SPECIALIST_PROCEDURE_CONTEXT_BYTES:
        raise ValueError("Specialist procedure text must be 80 KiB or smaller.")
    total_bytes = 0
    resources: list[dict[str, Any]] = []
    rendered_pdf_bytes = 0
    for row in rows:
        path = str(row["relative_path"])
        normalized = PurePosixPath(path)
        if (
            len(path) > 512
            or normalized.is_absolute()
            or normalized.as_posix() != path
            or "\\" in path
            or any(part in {"", ".", ".."} for part in normalized.parts)
        ):
            raise ValueError("A linked specialist procedure resource path is invalid.")
        raw = bytes(row["content"])
        media_type = str(row["media_type"])
        if not is_text_asset_media_type(media_type) and media_type not in (
            SPECIALIST_IMAGE_MEDIA_TYPES | {"application/pdf"}
        ):
            raise ValueError(
                "Specialist procedures can use linked UTF-8 text, extractable-text PDFs, PNG, and JPEG resources only."
            )
        if (
            len(raw) > 64 * 1024
            or int(row["size_bytes"]) != len(raw)
            or hashlib.sha256(raw).hexdigest() != row["sha256"]
        ):
            raise ValueError("A linked specialist procedure resource failed its integrity check.")
        content = ""
        extracted_pdf = False
        rendered_pages: list[tuple[int, bytes]] = []
        page_images: list[dict[str, Any]] = []
        if is_text_asset_media_type(media_type):
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("Specialist procedure resources must contain UTF-8 text.") from exc
        elif media_type == "application/pdf":
            rendered_pages = render_pdf_scanned_pages(raw)
            try:
                content = extract_document_text(raw, media_type)
            except ValueError as exc:
                if rendered_pages and "PDF contains no extractable text" in str(exc):
                    content = ""
                else:
                    raise ValueError(
                        "Linked specialist PDFs must contain bounded extractable text or scanned pages."
                    ) from exc
            if len(content.encode("utf-8")) > 64 * 1024:
                raise ValueError("Extracted specialist PDF text must be 64 KiB or smaller.")
            extracted_pdf = True
            for page_number, png in rendered_pages:
                rendered_pdf_bytes += len(png)
                if rendered_pdf_bytes > MAX_SPECIALIST_PROCEDURE_RENDERED_PDF_BYTES:
                    raise ValueError("Rendered specialist PDF pages exceed the 2 MiB limit.")
                page_images.append(
                    {
                        "page": page_number,
                        "sha256": hashlib.sha256(png).hexdigest(),
                        "content_base64": base64.b64encode(png).decode("ascii"),
                    }
                )
        else:
            signature_valid = (
                raw.startswith(b"\x89PNG\r\n\x1a\n")
                if media_type == "image/png"
                else raw.startswith(b"\xff\xd8\xff")
            )
            if not signature_valid:
                raise ValueError("A linked specialist image failed its file signature check.")
        total_bytes += len(raw)
        if total_bytes > MAX_SPECIALIST_PROCEDURE_RESOURCE_BYTES:
            raise ValueError("Specialist procedure linked resources must total 1 MiB or less.")
        resource: dict[str, Any] = {
            "path": path,
            "media_type": media_type,
            "sha256": str(row["sha256"]),
        }
        if is_text_asset_media_type(media_type) or extracted_pdf:
            resource["content"] = content
            if extracted_pdf:
                resource["extracted_text_sha256"] = hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest()
                resource["source_size_bytes"] = str(len(raw))
                if page_images:
                    resource["rendered_pages"] = page_images
        else:
            resource["content_base64"] = base64.b64encode(raw).decode("ascii")
        resources.append(resource)
    return resources


SpecialistRole = Literal["planner", "researcher", "developer", "reviewer", "tester", "executor"]
ROLES: tuple[SpecialistRole, ...] = (
    "planner",
    "researcher",
    "developer",
    "reviewer",
    "tester",
    "executor",
)


class SpecialistProfileStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    def list(self) -> list[dict[str, Any]]:
        with self.runs.connection() as connection:
            rows = connection.execute(
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,"
                "p.procedure_python_environment_digest,p.procedure_npm_environment_digest,p.updated_at,"
                "s.name AS procedure_name,s.active_version AS procedure_version "
                "FROM specialist_profiles p LEFT JOIN skills s ON s.id=p.procedure_skill_id ORDER BY p.role"
            ).fetchall()
        return [dict(row) for row in rows]

    def set_route(self, role: str, model_route: str | None) -> dict[str, Any]:
        if role not in ROLES:
            raise KeyError(role)
        if model_route is not None and (not model_route.strip() or len(model_route) > 256):
            raise ValueError("Specialist model route must be a discovered model identifier.")
        with self.runs.connection() as connection:
            connection.execute(
                "UPDATE specialist_profiles SET model_route=?,updated_at=? WHERE role=?",
                (model_route, utc_now(), role),
            )
            row = connection.execute(
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,p.procedure_python_environment_digest,p.procedure_npm_environment_digest,p.updated_at,s.name AS procedure_name,s.active_version AS procedure_version FROM specialist_profiles p LEFT JOIN skills s ON s.id=p.procedure_skill_id WHERE p.role=?",
                (role,),
            ).fetchone()
        if row is None:
            raise KeyError(role)
        return dict(row)

    def set_instructions(self, role: str, instructions: str) -> dict[str, Any]:
        if role not in ROLES:
            raise KeyError(role)
        if len(instructions) > 4000 or "\x00" in instructions:
            raise ValueError("Specialist instructions exceed the allowed limit.")
        with self.runs.connection() as connection:
            connection.execute(
                "UPDATE specialist_profiles SET system_instructions=?,updated_at=? WHERE role=?",
                (instructions.strip(), utc_now(), role),
            )
            row = connection.execute(
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,p.procedure_python_environment_digest,p.procedure_npm_environment_digest,p.updated_at,s.name AS procedure_name,s.active_version AS procedure_version FROM specialist_profiles p LEFT JOIN skills s ON s.id=p.procedure_skill_id WHERE p.role=?",
                (role,),
            ).fetchone()
        if row is None:
            raise KeyError(role)
        return dict(row)

    def set_display_name(self, role: str, display_name: str) -> dict[str, Any]:
        if role not in ROLES:
            raise KeyError(role)
        name = display_name.strip()
        if not name or len(name) > 64 or "\x00" in name:
            raise ValueError("Specialist display names must be 1-64 visible characters.")
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT 1 FROM specialist_profiles WHERE role!=? AND display_name=? COLLATE NOCASE",
                (role, name),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("Specialist display names must be unique.")
            changed = connection.execute(
                "UPDATE specialist_profiles SET display_name=?,updated_at=? WHERE role=?",
                (name, utc_now(), role),
            ).rowcount
            if changed != 1:
                raise KeyError(role)
            row = connection.execute(
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,p.procedure_python_environment_digest,p.procedure_npm_environment_digest,p.updated_at,s.name AS procedure_name,s.active_version AS procedure_version FROM specialist_profiles p LEFT JOIN skills s ON s.id=p.procedure_skill_id WHERE p.role=?",
                (role,),
            ).fetchone()
        assert row is not None
        return dict(row)

    def set_procedure(
        self,
        role: str,
        skill_id: str | None,
        python_environment_digest: str | None = None,
        npm_environment_digest: str | None = None,
    ) -> dict[str, Any]:
        if role not in ROLES:
            raise KeyError(role)
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if skill_id is not None:
                row = connection.execute(
                    "SELECT s.name,s.active_version,v.content FROM skills s "
                    "JOIN skill_versions v ON v.skill_id=s.id AND v.version=s.active_version "
                    "WHERE s.id=? AND s.lifecycle_state='active'",
                    (skill_id,),
                ).fetchone()
                if row is None:
                    raise ValueError("Choose a currently active procedure.")
                if (
                    len(str(row["content"]).encode("utf-8"))
                    > MAX_SPECIALIST_PROCEDURE_CONTEXT_BYTES
                ):
                    raise ValueError("Specialist procedures must be 80 KiB or smaller.")
                specialist_procedure_resources(
                    connection,
                    skill_id,
                    int(row["active_version"]),
                    len(str(row["content"]).encode("utf-8")),
                )
                for manager, digest in (
                    ("python", python_environment_digest),
                    ("npm", npm_environment_digest),
                ):
                    if digest is None:
                        continue
                    if len(digest) != 64 or any(
                        character not in "0123456789abcdef" for character in digest
                    ):
                        raise ValueError(f"Selected {manager} environment identity is invalid.")
                    table = (
                        "skill_python_dependency_environments"
                        if manager == "python"
                        else "skill_npm_dependency_environments"
                    )
                    lock_table = (
                        "skill_python_dependency_locks"
                        if manager == "python"
                        else "skill_npm_dependency_locks"
                    )
                    environment = connection.execute(
                        f"SELECT 1 FROM {lock_table} AS lock JOIN {table} AS environment "
                        "USING(skill_id,skill_version,environment_digest) WHERE lock.skill_id=? "
                        "AND lock.skill_version=? AND lock.environment_digest=? AND lock.decision='approved'",
                        (skill_id, int(row["active_version"]), digest),
                    ).fetchone()
                    if environment is None:
                        raise ValueError(
                            f"Choose an approved installed {manager} environment for this active procedure version."
                        )
            elif python_environment_digest is not None or npm_environment_digest is not None:
                raise ValueError("Dependency environments require an active specialist procedure.")
            changed = connection.execute(
                "UPDATE specialist_profiles SET procedure_skill_id=?,procedure_python_environment_digest=?,"
                "procedure_npm_environment_digest=?,updated_at=? WHERE role=?",
                (skill_id, python_environment_digest, npm_environment_digest, utc_now(), role),
            ).rowcount
            if changed != 1:
                raise KeyError(role)
            result = connection.execute(
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,"
                "p.procedure_python_environment_digest,p.procedure_npm_environment_digest,p.updated_at,"
                "s.name AS procedure_name,s.active_version AS procedure_version "
                "FROM specialist_profiles p LEFT JOIN skills s ON s.id=p.procedure_skill_id WHERE p.role=?",
                (role,),
            ).fetchone()
        assert result is not None
        return dict(result)

    def route_for(self, role: str, fallback: str | None) -> str | None:
        with self.runs.connection() as connection:
            row = connection.execute(
                "SELECT model_route FROM specialist_profiles WHERE role=?", (role,)
            ).fetchone()
        return str(row[0]) if row is not None and row[0] else fallback

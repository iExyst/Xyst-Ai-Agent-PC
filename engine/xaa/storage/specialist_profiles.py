"""Persistent named specialist identities and model routes."""

from __future__ import annotations

from typing import Any, Literal

from xaa.storage.runs import RunStore, utc_now

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
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,p.updated_at,"
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
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,p.updated_at,s.name AS procedure_name,s.active_version AS procedure_version FROM specialist_profiles p LEFT JOIN skills s ON s.id=p.procedure_skill_id WHERE p.role=?",
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
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,p.updated_at,s.name AS procedure_name,s.active_version AS procedure_version FROM specialist_profiles p LEFT JOIN skills s ON s.id=p.procedure_skill_id WHERE p.role=?",
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
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,p.updated_at,s.name AS procedure_name,s.active_version AS procedure_version FROM specialist_profiles p LEFT JOIN skills s ON s.id=p.procedure_skill_id WHERE p.role=?",
                (role,),
            ).fetchone()
        assert row is not None
        return dict(row)

    def set_procedure(self, role: str, skill_id: str | None) -> dict[str, Any]:
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
                if len(str(row["content"]).encode("utf-8")) > 16 * 1024:
                    raise ValueError("Specialist procedures must be 16 KiB or smaller.")
                if connection.execute(
                    "SELECT 1 FROM skill_version_assets WHERE skill_id=? AND version=? LIMIT 1",
                    (skill_id, row["active_version"]),
                ).fetchone():
                    raise ValueError("Specialist procedures cannot contain linked resources.")
            changed = connection.execute(
                "UPDATE specialist_profiles SET procedure_skill_id=?,updated_at=? WHERE role=?",
                (skill_id, utc_now(), role),
            ).rowcount
            if changed != 1:
                raise KeyError(role)
            result = connection.execute(
                "SELECT p.role,p.display_name,p.model_route,p.system_instructions,p.procedure_skill_id,p.updated_at,"
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

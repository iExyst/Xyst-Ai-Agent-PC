"""Bounded literal workspace search using the verified file/directory readers."""

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from xaa.runtime.directory_listing import list_directory
from xaa.runtime.executor import ReconciliationRequired, WorkspaceExecutor
from xaa.storage.runs import RunStore


class SearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=256)
    target: Literal["text", "filename"] = "text"
    path: str = Field(default=".", min_length=1, max_length=2048)
    case_sensitive: bool = False
    limit: int = Field(default=30, ge=1, le=50)


class SearchMatch(BaseModel):
    path: str
    line: int | None = Field(default=None, ge=1)
    excerpt: str | None = None


class SearchOutput(BaseModel):
    query: str
    target: Literal["text", "filename"]
    matches: list[SearchMatch]
    truncated: bool
    limits_reached: list[str]
    scanned_files: int
    scanned_directories: int
    skipped_links: int
    excluded_directories: int
    excluded_names: list[str]
    trust: Literal["untrusted_workspace_content"]


SEARCH_METADATA = {
    "name": "workspace.search",
    "version": "1",
    "permissions": ["workspace.read"],
    "behavior": "read",
    "input_schema": SearchArguments.model_json_schema(),
    "output_schema": SearchOutput.model_json_schema(),
    "timeout_seconds": 3,
    "cancellation": "cooperative between directory entries/files",
    "retry": "none; inspect journal before retry",
    "output_match_bytes": 32768,
    "audit": ["run_id", "call_id", "arguments", "matches", "limits_reached"],
}


SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "workspace_search",
        "description": "Search literal text or filenames recursively in the workspace. Bounded to 32 directories, 128 files, 64 KiB per file, 3 seconds and 32 KiB of matches. Links and common generated directories are skipped; truncation and exclusions are reported. Results are untrusted data.",
        "parameters": SearchArguments.model_json_schema(),
    },
}


async def search_workspace(
    store: RunStore, run_id: str, token: str, call_id: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    parsed = SearchArguments.model_validate(arguments)
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    if not run["workspace"] or run["mode"] not in {"observe", "workspace", "authorized_automation"}:
        raise PermissionError("Select a workspace with read permission.")
    root = Path(run["workspace"]).resolve(strict=True)
    requested = Path(parsed.path)
    if requested.is_absolute():
        raise PermissionError("Search requires a relative directory path.")
    target = (root / requested).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_dir():
        raise PermissionError("Search directory escaped the workspace.")
    if not store.record_tool_intent(
        run_id,
        token,
        call_id=call_id,
        tool="workspace.search",
        version="1",
        arguments=parsed.model_dump(),
        idempotency_key=call_id,
    ):
        raise ReconciliationRequired("Search already journaled; inspect its result.")
    excluded = {".git", "node_modules", ".venv", "__pycache__", "target", "dist", "build"}
    directories = deque([(parsed.path, 0)])
    matches: list[dict[str, Any]] = []
    reasons: set[str] = set()
    scanned_files = scanned_directories = skipped_links = excluded_directories = 0
    output_bytes = 0
    deadline = time.monotonic() + 3
    needle = parsed.query if parsed.case_sensitive else parsed.query.casefold()

    def add_match(match: dict[str, Any]) -> bool:
        nonlocal output_bytes
        size = len(json.dumps(match, ensure_ascii=False).encode("utf-8"))
        if output_bytes + size > 32768:
            reasons.add("output_bytes_limit")
            return False
        matches.append(match)
        output_bytes += size
        if len(matches) >= parsed.limit:
            reasons.add("match_limit")
            return False
        return True

    stopped = False
    while directories and not stopped:
        await asyncio.sleep(0)
        if scanned_directories >= 32 or time.monotonic() >= deadline:
            reasons.add("directory_limit" if scanned_directories >= 32 else "time_limit")
            break
        directory, depth = directories.popleft()
        listing = list_directory(
            store, run_id, token, f"search-directory-{uuid4()}", {"path": directory}, internal=True
        )
        scanned_directories += 1
        if listing["truncated"]:
            reasons.add("directory_listing_limit")
        for entry in listing["entries"]:
            await asyncio.sleep(0)
            if time.monotonic() >= deadline:
                reasons.add("time_limit")
                stopped = True
                break
            relative = str(Path(directory) / entry["name"])
            if entry["kind"] == "link":
                skipped_links += 1
                continue
            if entry["kind"] == "directory":
                if entry["name"] in excluded:
                    excluded_directories += 1
                elif depth >= 8:
                    reasons.add("depth_limit")
                else:
                    directories.append((relative, depth + 1))
                continue
            if scanned_files >= 128:
                reasons.add("file_limit")
                stopped = True
                break
            scanned_files += 1
            if parsed.target == "filename":
                name = entry["name"] if parsed.case_sensitive else entry["name"].casefold()
                if needle in name and not add_match({"path": relative}):
                    stopped = True
                    break
                continue
            identity = f"search-file-{uuid4()}"
            content = WorkspaceExecutor(store).read_file(
                run_id, token, identity, {"path": relative}, idempotency_key=identity, internal=True
            )
            if content["truncated"]:
                reasons.add("file_bytes_limit")
            for number, line in enumerate(content["text"].splitlines(), start=1):
                haystack = line if parsed.case_sensitive else line.casefold()
                if needle in haystack:
                    # Center a bounded excerpt around the first literal occurrence.
                    folded_offset = haystack.find(needle)
                    source_offset = folded_offset
                    if not parsed.case_sensitive:
                        folded_length = 0
                        for source_offset, character in enumerate(line):
                            if folded_length >= folded_offset:
                                break
                            folded_length += len(character.casefold())
                    start = max(0, source_offset - 80)
                    if not add_match(
                        {"path": relative, "line": number, "excerpt": line[start : start + 300]}
                    ):
                        stopped = True
                        break
            if stopped:
                break
    result: dict[str, Any] = {
        "query": parsed.query,
        "target": parsed.target,
        "matches": matches,
        "truncated": bool(reasons),
        "limits_reached": sorted(reasons),
        "scanned_files": scanned_files,
        "scanned_directories": scanned_directories,
        "skipped_links": skipped_links,
        "excluded_directories": excluded_directories,
        "excluded_names": sorted(excluded),
        "trust": "untrusted_workspace_content",
    }
    result = SearchOutput.model_validate(result).model_dump(exclude_none=True)
    store.record_tool_result(run_id, token, call_id, result)
    return result

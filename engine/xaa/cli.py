"""Command-line client for the same authenticated API used by the desktop."""

import argparse
import getpass
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote

import httpx

from xaa.credentials import CredentialStore
from xaa.storage.backup_bundle import restore_backup_bundle
from xaa.storage.restore import restore_database


def main() -> None:
    parser = argparse.ArgumentParser(prog="xaa", description="Xyst AI Agent API client")
    parser.add_argument("--api-url", default=os.environ.get("XAA_API_URL", "http://127.0.0.1:8765"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("models", help="Discover local models")
    commands.add_parser("providers", help="Inspect provider status")
    tokens = commands.add_parser(
        "tokens", help="Count a prompt against an already-loaded local model"
    )
    tokens.add_argument("message")
    tokens.add_argument("--model", required=True)
    commands.add_parser("backup", help="Create a verified database and artifact bundle")
    restore = commands.add_parser(
        "restore", help="Restore a database-only snapshot to a new offline destination"
    )
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("--destination", type=Path, required=True)
    bundle_restore = commands.add_parser(
        "restore-bundle",
        help="Restore a verified database and artifact backup to a new data directory",
    )
    bundle_restore.add_argument("bundle", type=Path)
    bundle_restore.add_argument("--destination", type=Path, required=True)
    credentials = commands.add_parser(
        "credentials", help="Manage the LM Studio token in Windows Credential Manager"
    )
    credentials.add_argument("action", choices=["set", "delete"])
    run = commands.add_parser("run", help="Submit an objective")
    run.add_argument("objective")
    run.add_argument("--model", required=True)
    run.add_argument("--workspace")
    run.add_argument("--mode", choices=["observe", "workspace"], default="observe")
    run.add_argument(
        "--max-total-tokens",
        type=int,
        help="Stop threshold for reported run-tree tokens; not a hard inference cap",
    )
    run.add_argument("--max-output-tokens", type=int, help="Per-request generated token limit")
    run.add_argument("--accept", action="append", default=[])
    for name in ("status", "pause", "resume", "cancel", "events"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        if name == "events":
            command.add_argument("--after", type=int, default=0)
    args = parser.parse_args()
    if args.command == "restore":
        try:
            restored = restore_database(args.snapshot, args.destination)
        except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
            parser.exit(1, f"Restore failed: {exc}\n")
        print(f"Restored database: {restored}")
        return
    if args.command == "restore-bundle":
        try:
            restored = restore_backup_bundle(args.bundle, args.destination)
        except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
            parser.exit(1, f"Restore failed: {exc}\n")
        print(f"Restored database and artifacts: {restored}")
        return
    if args.command == "credentials":
        store = CredentialStore()
        if args.action == "set":
            store.set(getpass.getpass("LM Studio API token: "))
        else:
            store.delete()
        print("Credential updated. Restart the engine to apply it.")
        return
    token = os.environ.get("XAA_API_TOKEN", "")
    if len(token) < 32:
        parser.exit(2, "Set XAA_API_TOKEN to the engine's bearer token (at least 32 characters).\n")
    try:
        with httpx.Client(
            base_url=args.api_url, headers={"Authorization": f"Bearer {token}"}, timeout=30
        ) as client:
            if args.command in {"models", "providers"}:
                response = client.get(f"/{args.command}")
            elif args.command == "tokens":
                response = client.post(
                    "/providers/lm-studio/token-count",
                    json={
                        "model": args.model,
                        "messages": [{"role": "user", "content": args.message}],
                    },
                )
            elif args.command == "backup":
                response = client.post("/backups")
            elif args.command == "run":
                response = client.post(
                    "/runs",
                    json={
                        "objective": args.objective,
                        "model": args.model,
                        "workspace": args.workspace,
                        "acceptance_criteria": args.accept,
                        "mode": args.mode,
                        "max_total_tokens": args.max_total_tokens,
                        "max_output_tokens": args.max_output_tokens,
                    },
                )
            else:
                path = f"/runs/{quote(args.run_id, safe='')}"
                if args.command == "events":
                    with client.stream(
                        "GET", f"{path}/events/stream", params={"after": args.after}, timeout=None
                    ) as events:
                        events.raise_for_status()
                        for line in events.iter_lines():
                            if line.startswith(("id:", "event:", "data:")):
                                print(line, flush=True)
                    return
                response = (
                    client.get(path)
                    if args.command == "status"
                    else client.post(f"{path}/{args.command}")
                )
            response.raise_for_status()
            print(json.dumps(response.json(), indent=2))
    except httpx.HTTPStatusError as exc:
        hint = (
            "Load the exact model instance in LM Studio and check tokenizer SDK/token compatibility."
            if args.command == "tokens"
            else "Check the run state and configuration."
        )
        parser.exit(1, f"Engine request rejected (HTTP {exc.response.status_code}). {hint}\n")
    except httpx.HTTPError:
        parser.exit(1, "Cannot communicate with the engine. Start it and check XAA_API_URL.\n")
    except KeyboardInterrupt:
        parser.exit(130, "Stopped watching. Use 'xaa cancel RUN_ID' to cancel the run.\n")


if __name__ == "__main__":
    main()

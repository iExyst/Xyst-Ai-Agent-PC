"""Kill the Telegram worker after a deterministic send acceptance, before journaling success."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self
from uuid import uuid4

from xaa.channels.telegram import (
    TelegramBotApi,
    TelegramIdentity,
    TelegramService,
    TelegramStore,
)
from xaa.credentials import CredentialStore
from xaa.storage.runs import RunStore, utc_now

IDENTITY = TelegramIdentity("420000001", "xaa_diagnostic_bot")
BOT_TOKEN = "123456789:diagnostic-token-not-valid-for-telegram"
PAIR_CODE = "diagnostic-pairing-code-000000000000000000000000"


class DiagnosticCredentials(CredentialStore):
    def get(self, profile: str = "lm-studio") -> str | None:
        return BOT_TOKEN if profile == "telegram-bot" else None

    def set(self, token: str, profile: str = "lm-studio") -> None:
        del token, profile

    def delete(self, profile: str = "lm-studio") -> None:
        del profile


class AcceptingTelegramApi(TelegramBotApi):
    def __init__(self, token: str, accepted_log: Path, *, block_after_accept: bool) -> None:
        del token
        self.accepted_log = accepted_log
        self.block_after_accept = block_after_accept

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def get_me(self) -> TelegramIdentity:
        return IDENTITY

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        del offset
        return []

    async def send_message(self, chat_id: str, text: str) -> int:
        entries = self.accepted_log.read_text(encoding="utf-8").splitlines()
        entry = {"sequence": len(entries) + 1, "chat_id": chat_id, "text": text}
        with self.accepted_log.open("a", encoding="utf-8", newline="\n") as sink:
            sink.write(json.dumps(entry, sort_keys=True) + "\n")
            sink.flush()
            os.fsync(sink.fileno())
        marker = self.accepted_log.with_suffix(".accepted")
        marker.write_text("accepted", encoding="ascii")
        with marker.open("ab") as marker_file:
            os.fsync(marker_file.fileno())
        if self.block_after_accept:
            await asyncio.sleep(60)
        return 73


def prepare_database(root: Path, *, scheduled: bool) -> tuple[str, str | None]:
    runs = RunStore(root / "runs.sqlite3")
    channel = TelegramStore(runs)
    channel.save_bot(IDENTITY)
    channel.create_pairing(PAIR_CODE)
    paired = channel.process_update(
        IDENTITY,
        {
            "update_id": 1,
            "message": {
                "text": f"/pair {PAIR_CODE}",
                "from": {"id": 9001},
                "chat": {"id": 9001, "type": "private"},
            },
        },
        model=None,
    )
    if paired["state"] != "paired":
        raise RuntimeError("Could not prepare a paired disposable Telegram identity.")
    execution_id: str | None = None
    if scheduled:
        from xaa.scheduler.service import ScheduleService
        from xaa.storage.schedules import ScheduleStore

        current = datetime.now(UTC).replace(second=0, microsecond=0)
        schedule = ScheduleStore(runs).create(
            objective="Prepare the scheduled crash-recovery fixture.",
            acceptance_criteria=[],
            workspace=None,
            model="diagnostic-model",
            start_at=current.replace(tzinfo=None),
            frequency="once",
            timezone="UTC",
            missed_run_policy="skip",
            max_iterations=4,
            max_seconds=60,
            delivery_destination=f"telegram:{IDENTITY.account_id}:9001",
        )
        generated = ScheduleService(runs, lambda _run_id: None).tick(current)
        if len(generated) != 1:
            raise RuntimeError("Could not create the scheduled acceptance run.")
        run_id = generated[0]
        with runs.connection() as connection:
            row = connection.execute(
                "SELECT id FROM schedule_executions WHERE schedule_id=? AND run_id=?",
                (schedule["id"], run_id),
            ).fetchone()
        if row is None:
            raise RuntimeError("The scheduled delivery execution was not persisted.")
        execution_id = str(row["id"])
        expected_text = "Scheduled result."
    else:
        queued = channel.process_update(
            IDENTITY,
            {
                "update_id": 2,
                "message": {
                    "text": "Prepare the harmless crash-recovery fixture.",
                    "from": {"id": 9001},
                    "chat": {"id": 9001, "type": "private"},
                },
            },
            model="diagnostic-model",
        )
        run_id = str(queued["run_id"])
        expected_text = "Diagnostic result."
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET state='completed',updated_at=? WHERE id=?", (utc_now(), run_id)
        )
        RunStore.record_event(
            connection,
            run_id,
            "model.message",
            {"message": {"role": "assistant", "content": expected_text}},
        )
    return run_id, execution_id


def make_service(root: Path, *, block_after_accept: bool) -> TelegramService:
    runs = RunStore(root / "runs.sqlite3")

    def api_factory(_token: str) -> AcceptingTelegramApi:
        return AcceptingTelegramApi(
            _token, root / "accepted.jsonl", block_after_accept=block_after_accept
        )

    return TelegramService(
        runs, DiagnosticCredentials(), lambda _run_id: None, api_factory=api_factory
    )


def child(root: Path, *, scheduled: bool) -> None:
    del scheduled
    asyncio.run(make_service(root, block_after_accept=True).cycle())


def main() -> None:
    scheduled = "--scheduled" in sys.argv
    if "--child" in sys.argv:
        child(Path(sys.argv[sys.argv.index("--child") + 1]), scheduled=scheduled)
        return
    if "--reconcile" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--reconcile") + 1])
        asyncio.run(make_service(root, block_after_accept=False).cycle())
        return

    root = (Path(".acceptance") / f"telegram-crash-{uuid4()}").resolve()
    root.mkdir(parents=True)
    accepted_log = root / "accepted.jsonl"
    accepted_log.write_text("", encoding="utf-8")
    run_id, execution_id = prepare_database(root, scheduled=scheduled)
    with (root / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--child",
                str(root),
                *(["--scheduled"] if scheduled else []),
            ],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            marker = accepted_log.with_suffix(".accepted")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if marker.exists():
                    break
                if process.poll() is not None:
                    raise RuntimeError("Telegram worker exited before the accept boundary.")
                time.sleep(0.05)
            else:
                raise TimeoutError("Telegram worker did not reach the simulated accept boundary.")
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    accepted_before = accepted_log.read_text(encoding="utf-8").splitlines()
    reopened = RunStore(root / "runs.sqlite3")
    with reopened.connection() as connection:
        if scheduled:
            assert execution_id is not None
            before_reconcile = str(
                connection.execute(
                    "SELECT state FROM schedule_deliveries WHERE execution_id=?",
                    (execution_id,),
                ).fetchone()[0]
            )
        else:
            before_reconcile = str(
                connection.execute(
                    "SELECT delivery_state FROM telegram_updates WHERE account_id=? AND update_id=2",
                    (IDENTITY.account_id,),
                ).fetchone()[0]
            )
    reconciliation = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--reconcile",
            str(root),
            *(["--scheduled"] if scheduled else []),
        ],
        capture_output=True,
        check=False,
        timeout=20,
        text=True,
    )
    (root / "reconcile.stdout.log").write_text(reconciliation.stdout, encoding="utf-8")
    (root / "reconcile.stderr.log").write_text(reconciliation.stderr, encoding="utf-8")
    if reconciliation.returncode != 0:
        raise RuntimeError(
            f"Fresh-process Telegram reconciliation failed ({reconciliation.returncode})."
        )
    accepted_after = accepted_log.read_text(encoding="utf-8").splitlines()
    with reopened.connection() as connection:
        if scheduled:
            assert execution_id is not None
            after_reconcile = str(
                connection.execute(
                    "SELECT state FROM schedule_deliveries WHERE execution_id=?",
                    (execution_id,),
                ).fetchone()[0]
            )
        else:
            after_reconcile = str(
                connection.execute(
                    "SELECT delivery_state FROM telegram_updates WHERE account_id=? AND update_id=2",
                    (IDENTITY.account_id,),
                ).fetchone()[0]
            )
    passed = (
        before_reconcile == "sending"
        and after_reconcile == "unknown"
        and len(accepted_before) == 1
        and accepted_after == accepted_before
        and json.loads(accepted_after[0])
        == {
            "sequence": 1,
            "chat_id": "9001",
            "text": "Scheduled result." if scheduled else "Diagnostic result.",
        }
    )
    report: dict[str, Any] = {
        "passed": passed,
        "transport": "deterministic fsynced local acceptor; no Telegram endpoint or bot used",
        "crash_boundary": "send accepted durably before delivery journal completion; reconciled in a fresh process",
        "delivery_kind": "scheduled outbox" if scheduled else "inbound reply",
        "run_id": run_id,
        "delivery_state_before_restart": before_reconcile,
        "delivery_state_after_restart": after_reconcile,
        "accepted_effects_before_restart": len(accepted_before),
        "accepted_effects_after_restart": len(accepted_after),
    }
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({**report, "evidence": str(root)}, indent=2))
    if not passed:
        raise RuntimeError("Telegram crash recovery acceptance failed.")


if __name__ == "__main__":
    main()

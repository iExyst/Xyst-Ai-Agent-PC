"""Kill the email worker after a deterministic SMTP accept, before journal completion."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from uuid import uuid4

from xaa.channels.email import EmailConfig, EmailService, EmailStore
from xaa.storage.runs import RunStore, utc_now


class DiagnosticCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.values.get(profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        self.values[profile] = token

    def delete(self, profile: str = "lm-studio") -> None:
        self.values.pop(profile, None)


class AcceptingEmailService(EmailService):
    def __init__(self, *args: Any, accepted_log: Path, block_after_accept: bool) -> None:
        super().__init__(*args)
        self.accepted_log = accepted_log
        self.block_after_accept = block_after_accept

    def _fetch(self, config: EmailConfig, mailbox_id: str) -> list[tuple[str, str, bytes]]:
        del config, mailbox_id
        return []

    def _send(self, config: EmailConfig, delivery: dict[str, str]) -> str:
        del config
        """Represent SMTP's durable 250 acceptance using a separate fsynced sink record."""
        accepted = self.accepted_log.read_text(encoding="utf-8").splitlines()
        entry = {"sequence": len(accepted) + 1, "uid": delivery["uid"]}
        with self.accepted_log.open("a", encoding="utf-8", newline="\n") as sink:
            sink.write(json.dumps(entry, sort_keys=True) + "\n")
            sink.flush()
            os.fsync(sink.fileno())
        marker = self.accepted_log.with_suffix(".accepted")
        marker.write_text("accepted", encoding="ascii")
        with marker.open("ab") as marker_file:
            os.fsync(marker_file.fileno())
        if self.block_after_accept:
            time.sleep(60)
        return "<diagnostic-accepted@example.test>"


def config_values() -> dict[str, str]:
    return {
        "address": "agent@example.test",
        "password": "synthetic-app-password",
        "imap_host": "imap.example.test",
        "smtp_host": "smtp.example.test",
        "allowed_senders": "owner@example.test",
        "authserv_id": "mx.example.test",
    }


def credentials() -> DiagnosticCredentials:
    values = DiagnosticCredentials()
    config = config_values()
    password = config.pop("password")
    values.set(json.dumps(config), "email-mailbox-config")
    values.set(password, "email-mailbox-password")
    return values


def discard_run(_run_id: str) -> None:
    return None


def child(root: Path) -> None:
    runs = RunStore(root / "runs.sqlite3")
    service = AcceptingEmailService(
        runs,
        credentials(),
        discard_run,
        accepted_log=root / "accepted.jsonl",
        block_after_accept=True,
    )
    asyncio.run(service.cycle())


def prepare_database(root: Path) -> str:
    runs = RunStore(root / "runs.sqlite3")
    config = EmailConfig.parse(config_values())
    store = EmailStore(runs)
    mailbox_id = store.save_config(config)
    message = EmailMessage()
    message["From"] = "owner@example.test"
    message["To"] = config.address
    message["Subject"] = "Run a harmless check"
    message["Message-ID"] = "<diagnostic-inbound@example.test>"
    message["Authentication-Results"] = "mx.example.test; dmarc=pass header.from=example.test"
    message.set_content("Please run the acceptance fixture.")
    run_id = store.accept_message(mailbox_id, "77", "1", message.as_bytes(), "diagnostic/fixture")
    if run_id is None:
        raise RuntimeError("Could not prepare the authenticated diagnostic mailbox message.")
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET state='completed',updated_at=? WHERE id=?",
            (utc_now(), run_id),
        )
        RunStore.record_event(
            connection,
            run_id,
            "model.message",
            {"message": {"role": "assistant", "content": "Diagnostic result."}},
        )
    return mailbox_id


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        child(Path(sys.argv[2]))
        return

    root = (Path(".acceptance") / str(uuid4())).resolve()
    root.mkdir(parents=True)
    accepted_log = root / "accepted.jsonl"
    accepted_log.write_text("", encoding="utf-8")
    mailbox_id = prepare_database(root)
    with (root / "worker.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--child", str(root)],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            marker = root / "accepted.accepted"
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if marker.exists():
                    break
                if process.poll() is not None:
                    raise RuntimeError("Email process exited before the accept boundary.")
                time.sleep(0.05)
            else:
                raise TimeoutError("Email worker did not reach the simulated accept boundary.")
            process.kill()
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)

    accepted_before = accepted_log.read_text(encoding="utf-8").splitlines()
    reopened = RunStore(root / "runs.sqlite3")
    before_reconcile = EmailStore(reopened).status("agent@example.test")["deliveries"]
    restart_service = AcceptingEmailService(
        reopened,
        credentials(),
        discard_run,
        accepted_log=accepted_log,
        block_after_accept=False,
    )
    asyncio.run(restart_service.cycle())
    accepted_after = accepted_log.read_text(encoding="utf-8").splitlines()
    final_status = EmailStore(reopened).status("agent@example.test")["deliveries"]
    passed = (
        before_reconcile == {"sending": 1}
        and final_status == {"unknown": 1}
        and len(accepted_before) == 1
        and accepted_after == accepted_before
        and json.loads(accepted_after[0]) == {"sequence": 1, "uid": "1"}
    )
    report: dict[str, Any] = {
        "passed": passed,
        "transport": "deterministic fsynced acceptor; no live SMTP server used",
        "crash_boundary": "accept record durable before email delivery journal completion",
        "delivery_state_before_restart": before_reconcile,
        "reconciled_sending_rows": final_status.get("unknown", 0),
        "delivery_state_after_restart": final_status,
        "accepted_effects_before_restart": len(accepted_before),
        "accepted_effects_after_restart": len(accepted_after),
        "mailbox_id": mailbox_id,
    }
    (root / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({**report, "evidence": str(root)}, indent=2))
    if not passed:
        raise RuntimeError("Email crash recovery acceptance failed.")


if __name__ == "__main__":
    main()

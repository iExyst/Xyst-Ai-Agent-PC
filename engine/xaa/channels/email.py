"""Text-only IMAP/SMTP channel with sender allowlists and durable deduplication."""

from __future__ import annotations

import asyncio
import imaplib
import json
import logging
import re
import smtplib
import sqlite3
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.policy import default
from email.utils import make_msgid, parseaddr
from typing import Any, cast
from uuid import uuid4

from xaa.credentials import CredentialStore
from xaa.storage.runs import RunStore, utc_now

logger = logging.getLogger(__name__)
MAX_EMAIL_BODY = 32_000
MAX_EMAIL_BYTES = 96_000
AUTOMATED_HEADERS = ("Auto-Submitted", "Precedence", "List-Unsubscribe", "X-Auto-Response-Suppress")
AUTOMATED_SENDERS = (
    "noreply",
    "no-reply",
    "no_reply",
    "donotreply",
    "mailer-daemon",
    "postmaster",
    "bounce",
)


@dataclass(frozen=True)
class EmailConfig:
    address: str
    password: str
    imap_host: str
    smtp_host: str
    allowed_senders: frozenset[str]
    authserv_id: str
    imap_port: int = 993
    smtp_port: int = 587
    smtp_security: str = "starttls"

    @classmethod
    def parse(cls, values: dict[str, str]) -> EmailConfig:
        address = values.get("address", "").strip().lower()
        parsed_address = parseaddr(address)[1]
        if not address or parsed_address.lower() != address or "@" not in address:
            raise ValueError("Enter a valid email address.")
        password = values.get("password", "")
        imap_host = values.get("imap_host", "").strip()
        smtp_host = values.get("smtp_host", "").strip()
        if not password or not imap_host or not smtp_host:
            raise ValueError("Email password, IMAP host and SMTP host are required.")
        if any(ch in host for host in (imap_host, smtp_host) for ch in "\r\n/@"):
            raise ValueError("Mail server host is invalid.")
        try:
            imap_port = int(values.get("imap_port", "993"))
            smtp_port = int(values.get("smtp_port", "587"))
        except ValueError as exc:
            raise ValueError("Mail server ports must be valid integers.") from exc
        if not 1 <= imap_port <= 65535 or not 1 <= smtp_port <= 65535:
            raise ValueError("Mail server ports must be between 1 and 65535.")
        security = values.get("smtp_security", "starttls").strip().lower()
        if security not in {"starttls", "tls"}:
            raise ValueError("SMTP must use STARTTLS or implicit TLS.")
        allowed = frozenset(
            item.strip().lower()
            for item in values.get("allowed_senders", "").split(",")
            if item.strip()
        )
        if not allowed or any(
            parseaddr(item)[1].lower() != item or "@" not in item for item in allowed
        ):
            raise ValueError("List at least one valid authorized sender email address.")
        authserv_id = values.get("authserv_id", "").strip().lower()
        if not re.fullmatch(r"[a-z0-9.-]{1,253}", authserv_id):
            raise ValueError("Configure the exact receiving mail server authserv-id.")
        return cls(
            address,
            password,
            imap_host,
            smtp_host,
            allowed,
            authserv_id,
            imap_port,
            smtp_port,
            security,
        )


def _automated(message: Message, sender: str) -> bool:
    lowered = sender.lower()
    if any(marker in lowered for marker in AUTOMATED_SENDERS):
        return True
    auto_submitted = (message.get("Auto-Submitted") or "no").lower()
    precedence = (message.get("Precedence") or "").lower()
    return (
        auto_submitted != "no"
        or precedence in {"bulk", "list", "junk"}
        or any(message.get(header) for header in AUTOMATED_HEADERS[2:])
    )


def _authenticated_sender(message: Message, sender: str, authserv_id: str) -> bool:
    results = message.get_all("Authentication-Results", [])
    if not results:
        return False
    trusted_headers: list[str] = []
    for raw_header in results:
        server, separator, assertions = str(raw_header).strip().partition(";")
        if not separator:
            continue
        if server.strip().lower() == authserv_id.lower():
            trusted_headers.append(assertions)
    if (
        not trusted_headers
        or str(results[0]).split(";", 1)[0].strip().lower() != authserv_id.lower()
    ):
        return False
    for assertions in trusted_headers:
        if not re.search(r"(?:^|;)\s*dmarc=pass(?:\s|;|$)", assertions, re.IGNORECASE):
            return False
        from_domain = re.search(r"(?:^|[;\s])header\.from=([^;\s]+)", assertions, re.IGNORECASE)
        if (
            not from_domain
            or from_domain.group(1).strip("<>").lower() != sender.rsplit("@", 1)[-1].lower()
        ):
            return False
    return True


def _decode_payload(payload: object, charset: str | None) -> str:
    if not isinstance(payload, bytes):
        return ""
    try:
        return payload.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _plain_body(message: Message) -> str:
    if message.is_multipart():
        for part in message.walk():
            if (
                part.get_content_disposition() == "attachment"
                or part.get_content_type() != "text/plain"
            ):
                continue
            payload = part.get_payload(decode=True)
            if payload:
                return _decode_payload(payload, part.get_content_charset())[:MAX_EMAIL_BODY]
        return ""
    payload = message.get_payload(decode=True)
    if not payload:
        return ""
    return _decode_payload(payload, message.get_content_charset())[:MAX_EMAIL_BODY]


class EmailStore:
    def __init__(self, runs: RunStore) -> None:
        self.runs = runs

    def save_config(self, config: EmailConfig) -> str:
        identity = config.address
        values = {
            "imap_host": config.imap_host,
            "imap_port": str(config.imap_port),
            "smtp_host": config.smtp_host,
            "smtp_port": str(config.smtp_port),
            "smtp_security": config.smtp_security,
            "allowed_senders": ",".join(sorted(config.allowed_senders)),
            "authserv_id": config.authserv_id,
        }
        now = utc_now()
        with self.runs.connection() as connection:
            connection.execute(
                "INSERT INTO email_mailboxes(id,address,config_json,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(address) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at",
                (identity, identity, json.dumps(values, separators=(",", ":")), now, now),
            )
        return identity

    def remove_config(self, address: str) -> None:
        with self.runs.connection() as connection:
            connection.execute("DELETE FROM email_mailboxes WHERE address=?", (address,))

    def mailbox(self, address: str) -> dict[str, Any] | None:
        with self.runs.connection() as connection:
            row = connection.execute(
                "SELECT * FROM email_mailboxes WHERE address=?", (address,)
            ).fetchone()
        return dict(row) if row else None

    def poll_cursor(self, mailbox_id: str, uid_validity: str) -> int:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT uid_validity,last_uid FROM email_mailboxes WHERE id=?", (mailbox_id,)
            ).fetchone()
            if row is None:
                raise ValueError("Email mailbox is no longer configured.")
            if row["uid_validity"] != uid_validity:
                connection.execute(
                    "UPDATE email_mailboxes SET uid_validity=?,last_uid=0,updated_at=? WHERE id=?",
                    (uid_validity, utc_now(), mailbox_id),
                )
                return 0
            return int(row["last_uid"])

    def advance_cursor(self, mailbox_id: str, uid_validity: str, uid: str) -> None:
        with self.runs.connection() as connection:
            connection.execute(
                "UPDATE email_mailboxes SET last_uid=max(last_uid,?),updated_at=? WHERE id=? AND uid_validity=?",
                (int(uid), utc_now(), mailbox_id, uid_validity),
            )

    def status(self, address: str | None) -> dict[str, Any]:
        if address is None:
            return {"configured": False, "address": None, "processed_messages": 0, "deliveries": {}}
        with self.runs.connection() as connection:
            mailbox = connection.execute(
                "SELECT id FROM email_mailboxes WHERE address=?", (address,)
            ).fetchone()
            if mailbox is None:
                return {
                    "configured": False,
                    "address": None,
                    "processed_messages": 0,
                    "deliveries": {},
                }
            count = connection.execute(
                "SELECT count(*) FROM email_messages WHERE mailbox_id=?", (mailbox["id"],)
            ).fetchone()[0]
            rows = connection.execute(
                "SELECT delivery_state,count(*) AS amount FROM email_messages WHERE mailbox_id=? AND delivery_state IS NOT NULL GROUP BY delivery_state",
                (mailbox["id"],),
            ).fetchall()
        return {
            "configured": True,
            "address": address,
            "processed_messages": int(count),
            "deliveries": {str(row["delivery_state"]): int(row["amount"]) for row in rows},
        }

    def accept_message(
        self, mailbox_id: str, uid_validity: str, uid: str, raw: bytes, model: str | None
    ) -> str | None:
        if len(raw) > MAX_EMAIL_BYTES:
            return None
        message = cast(Message, BytesParser(policy=default).parsebytes(raw))
        raw_sender = str(message.get("From", ""))
        if len(raw_sender) > 2048:
            return None
        sender = parseaddr(raw_sender)[1].strip().lower()
        candidate_message_id = str(message.get("Message-ID", ""))
        message_id = (
            candidate_message_id
            if len(candidate_message_id) <= 998
            and "\r" not in candidate_message_id
            and "\n" not in candidate_message_id
            else None
        )
        with self.runs.connection() as connection:
            mailbox = connection.execute(
                "SELECT * FROM email_mailboxes WHERE id=?", (mailbox_id,)
            ).fetchone()
            if mailbox is None:
                return None
            options = json.loads(mailbox["config_json"])
            allowed = {
                item.strip().lower()
                for item in options["allowed_senders"].split(",")
                if item.strip()
            }
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                "INSERT OR IGNORE INTO email_messages(mailbox_id,uid_validity,uid,message_id,sender,state,created_at,updated_at) VALUES(?,?,?,?,?,'processing',?,?)",
                (mailbox_id, uid_validity, uid, message_id, sender, utc_now(), utc_now()),
            ).rowcount
            if inserted == 0:
                return None
            body = _plain_body(message).strip()
            if (
                sender not in allowed
                or not _authenticated_sender(message, sender, str(options.get("authserv_id", "")))
                or _automated(message, sender)
                or not body
                or model is None
            ):
                connection.execute(
                    "UPDATE email_messages SET state=? WHERE mailbox_id=? AND uid_validity=? AND uid=?",
                    ("ignored", mailbox_id, uid_validity, uid),
                )
                return None
            session = connection.execute(
                "SELECT s.id FROM channel_bindings b JOIN sessions s ON s.id=b.session_id WHERE b.channel='email' AND b.account_id=? AND b.external_identity=? AND b.authorized=1",
                (mailbox_id, sender),
            ).fetchone()
            session_id = str(session["id"]) if session else str(uuid4())
            now = utc_now()
            if session is None:
                connection.execute(
                    "INSERT INTO sessions(id,channel,account_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                    (session_id, "email", mailbox_id, f"Email from {sender}", now, now),
                )
                connection.execute(
                    "INSERT INTO channel_bindings(id,channel,account_id,external_identity,session_id,identity_link_id,authorized,created_at) VALUES(?,?,?,?,?,NULL,1,?)",
                    (str(uuid4()), "email", mailbox_id, sender, session_id, now),
                )
            run_id = str(uuid4())
            connection.execute(
                "INSERT INTO runs(id,objective,acceptance_criteria,workspace,mode,model,state,created_at,updated_at) VALUES(?,?, '[]',NULL,'observe',?,'queued',?,?)",
                (run_id, body[:MAX_EMAIL_BODY], model, now, now),
            )
            connection.execute(
                "INSERT INTO run_limits(run_id,max_iterations,max_seconds) VALUES(?,8,900)",
                (run_id,),
            )
            connection.execute("INSERT INTO run_checks(run_id,checks) VALUES(?,'[]')", (run_id,))
            from xaa.storage.runs import RunStore as _RunStore

            _RunStore.record_event(
                connection,
                run_id,
                "run.created",
                {"channel": "email", "email_uid": uid, "message_id": message_id},
            )
            _RunStore.record_event(
                connection,
                run_id,
                "message.user",
                {"message": {"role": "user", "content": body[:MAX_EMAIL_BODY]}},
            )
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO messages(id,session_id,sequence,role,content,created_at) VALUES(?,?,?,'user',?,?)",
                (str(uuid4()), session_id, sequence, body[:MAX_EMAIL_BODY], now),
            )
            connection.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
            connection.execute(
                "UPDATE email_messages SET session_id=?,run_id=?,state='queued',delivery_state='pending',updated_at=? WHERE mailbox_id=? AND uid_validity=? AND uid=?",
                (session_id, run_id, now, mailbox_id, uid_validity, uid),
            )
            return run_id

    def claim_delivery(self, mailbox_id: str) -> dict[str, str] | None:
        with self.runs.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT m.uid_validity,m.uid,m.message_id,m.sender,m.run_id,r.state FROM email_messages m JOIN runs r ON r.id=m.run_id WHERE m.mailbox_id=? AND m.delivery_state='pending' ORDER BY m.created_at LIMIT 1",
                (mailbox_id,),
            ).fetchone()
            if row is None or row["state"] not in {"completed", "failed", "blocked", "cancelled"}:
                return None
            content = connection.execute(
                "SELECT payload FROM run_events WHERE run_id=? AND event_type='model.message' ORDER BY sequence DESC LIMIT 1",
                (row["run_id"],),
            ).fetchone()
            if row["state"] == "completed" and content:
                answer = str(json.loads(content["payload"]).get("message", {}).get("content", ""))
            else:
                answer = (
                    "Your X.A.A. run needs attention. Please review its status in the desktop app."
                )
            answer = answer[:MAX_EMAIL_BODY]
            connection.execute(
                "UPDATE email_messages SET delivery_state='sending',updated_at=? WHERE mailbox_id=? AND uid_validity=? AND uid=? AND delivery_state='pending'",
                (utc_now(), mailbox_id, row["uid_validity"], row["uid"]),
            )
            return {
                "uid_validity": str(row["uid_validity"]),
                "uid": str(row["uid"]),
                "sender": str(row["sender"]),
                "subject": "X.A.A. task result",
                "in_reply_to": str(row["message_id"] or ""),
                "body": answer,
            }

    def finish_delivery(
        self, mailbox_id: str, delivery: dict[str, str], state: str, message_id: str | None = None
    ) -> None:
        if state not in {"sent", "failed", "unknown"}:
            raise ValueError("Invalid email delivery state.")
        with self.runs.connection() as connection:
            connection.execute(
                "UPDATE email_messages SET delivery_state=?,delivery_message_id=?,updated_at=? WHERE mailbox_id=? AND uid_validity=? AND uid=? AND delivery_state='sending'",
                (
                    state,
                    message_id,
                    utc_now(),
                    mailbox_id,
                    delivery["uid_validity"],
                    delivery["uid"],
                ),
            )

    def reconcile_sends(self) -> int:
        with self.runs.connection() as connection:
            return connection.execute(
                "UPDATE email_messages SET delivery_state='unknown',updated_at=? WHERE delivery_state='sending'",
                (utc_now(),),
            ).rowcount


class EmailService:
    """Poll one configured mailbox and route authorized senders via the shared supervisor."""

    def __init__(
        self,
        runs: RunStore,
        credentials: CredentialStore,
        submit: Callable[[str], None],
        *,
        imap_factory: Callable[..., Any] = imaplib.IMAP4_SSL,
        smtp_factory: Callable[..., Any] = smtplib.SMTP,
        smtps_factory: Callable[..., Any] = smtplib.SMTP_SSL,
    ) -> None:
        self.runs, self.store, self.credentials, self.submit = (
            runs,
            EmailStore(runs),
            credentials,
            submit,
        )
        self.imap_factory, self.smtp_factory, self.smtps_factory = (
            imap_factory,
            smtp_factory,
            smtps_factory,
        )
        self.task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="email-channel")

    async def close(self) -> None:
        task, self.task = self.task, None
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def run(self) -> None:
        while True:
            try:
                await self.cycle()
            except asyncio.CancelledError:
                raise
            except (
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                sqlite3.Error,
                smtplib.SMTPException,
                imaplib.IMAP4.error,
            ) as exc:
                logger.error("Email adapter cycle failed (%s)", type(exc).__name__)
            await asyncio.sleep(15)

    async def cycle(self) -> None:
        raw_config = self.credentials.get("email-mailbox-config")
        if not raw_config:
            return
        parsed_config: object = json.loads(raw_config)
        if not isinstance(parsed_config, dict):
            raise TypeError("Stored email configuration is malformed.")
        raw_values = cast(dict[object, object], parsed_config)
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_values.items()
        ):
            raise TypeError("Stored email configuration is malformed.")
        values = cast(dict[str, str], parsed_config)
        values["password"] = self.credentials.get("email-mailbox-password") or ""
        config = EmailConfig.parse(values)
        mailbox = self.store.mailbox(config.address)
        if mailbox is None:
            return
        mailbox_id = str(mailbox["id"])
        self.store.reconcile_sends()
        model = None
        with self.runs.connection() as connection:
            row = connection.execute(
                "SELECT selected_model FROM provider_profiles WHERE id='lm-studio'"
            ).fetchone()
            model = str(row[0]) if row and row[0] else None
        received = await asyncio.to_thread(self._fetch, config, mailbox_id)
        for validity, uid, raw in received:
            run_id = self.store.accept_message(mailbox_id, validity, uid, raw, model)
            if run_id:
                self.submit(run_id)
            self.store.advance_cursor(mailbox_id, validity, uid)
        while delivery := self.store.claim_delivery(mailbox_id):
            try:
                send_task = asyncio.create_task(asyncio.to_thread(self._send, config, delivery))
                try:
                    sent_id = await asyncio.shield(send_task)
                except asyncio.CancelledError:
                    try:
                        await asyncio.shield(send_task)
                    except (
                        OSError,
                        RuntimeError,
                        TypeError,
                        ValueError,
                        smtplib.SMTPException,
                        ssl.SSLError,
                    ):
                        pass
                    raise
            except smtplib.SMTPRecipientsRefused:
                self.store.finish_delivery(mailbox_id, delivery, "failed")
            except (smtplib.SMTPException, OSError, ssl.SSLError):
                self.store.finish_delivery(mailbox_id, delivery, "unknown")
            else:
                self.store.finish_delivery(mailbox_id, delivery, "sent", sent_id)

    def _fetch(self, config: EmailConfig, mailbox_id: str) -> list[tuple[str, str, bytes]]:
        client: Any = self.imap_factory(
            config.imap_host, config.imap_port, ssl_context=ssl.create_default_context(), timeout=15
        )
        try:
            client.login(config.address, config.password)
            status, data = client.select("INBOX", readonly=True)
            if status != "OK":
                return []
            validity_result = client.response("UIDVALIDITY")
            validity = ""
            try:
                validity = str(validity_result[1][0].decode("ascii", errors="strict"))
            except (IndexError, TypeError, AttributeError):
                raise ValueError("IMAP server did not provide UIDVALIDITY.") from None
            if not validity.isdigit() or validity == "0":
                raise ValueError("IMAP server returned invalid UIDVALIDITY.")
            last_uid = self.store.poll_cursor(mailbox_id, validity)
            status, data = client.uid("search", None, f"UID {last_uid + 1}:*")
            if status != "OK" or not data or not data[0]:
                return []
            results: list[tuple[str, str, bytes]] = []
            for raw_uid in bytes(data[0]).split()[:20]:
                uid = raw_uid.decode("ascii", errors="strict")
                fetch_status, fetched = client.uid(
                    "fetch", uid, f"(BODY.PEEK[]<0.{MAX_EMAIL_BYTES + 1}>)"
                )
                if fetch_status != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                raw = bytes(fetched[0][1])
                if len(raw) <= MAX_EMAIL_BYTES:
                    results.append((validity, uid, raw))
            return results
        finally:
            try:
                client.logout()
            except (OSError, imaplib.IMAP4.error):
                pass

    def _send(self, config: EmailConfig, delivery: dict[str, str]) -> str:
        message = EmailMessage()
        message["From"] = config.address
        message["To"] = delivery["sender"]
        message["Subject"] = delivery["subject"]
        message["Message-ID"] = make_msgid(domain=config.address.rsplit("@", 1)[-1])
        message["Auto-Submitted"] = "auto-generated"
        if delivery["in_reply_to"]:
            message["In-Reply-To"] = delivery["in_reply_to"]
            message["References"] = delivery["in_reply_to"]
        message.set_content(delivery["body"][:MAX_EMAIL_BODY])
        client: smtplib.SMTP
        if config.smtp_security == "tls":
            client = self.smtps_factory(
                config.smtp_host, config.smtp_port, timeout=15, context=ssl.create_default_context()
            )
        else:
            client = self.smtp_factory(config.smtp_host, config.smtp_port, timeout=15)
            client.starttls(context=ssl.create_default_context())
        try:
            client.login(config.address, config.password)
            client.send_message(message)
            return str(message["Message-ID"])
        finally:
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                client.close()

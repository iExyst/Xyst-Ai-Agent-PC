from __future__ import annotations

import asyncio
import base64
import json
import socketserver
import ssl
import threading
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import make_msgid
from ipaddress import ip_address
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from pydantic import SecretStr
from starlette.testclient import TestClient
from xaa.channels.email import EmailConfig, EmailService, EmailStore, _automated
from xaa.storage.runs import RunStore, utc_now


class MemoryCredentials:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, profile: str = "lm-studio") -> str | None:
        return self.values.get(profile)

    def set(self, token: str, profile: str = "lm-studio") -> None:
        self.values[profile] = token

    def delete(self, profile: str = "lm-studio") -> None:
        self.values.pop(profile, None)


def message_bytes(
    sender: str, body: str, *, automated: bool = False, authenticated: bool = True
) -> tuple[bytes, str]:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "agent@example.test"
    message["Subject"] = "Please review"
    message["Message-ID"] = make_msgid()
    if authenticated:
        message["Authentication-Results"] = "mx.example.test; dmarc=pass header.from=example.test"
    if automated:
        message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    return message.as_bytes(), str(message["Message-ID"])


def test_email_config_requires_tls_and_explicit_sender_allowlist() -> None:
    config = EmailConfig.parse(
        {
            "address": "agent@example.test",
            "password": "secret",
            "imap_host": "imap.example.test",
            "smtp_host": "smtp.example.test",
            "allowed_senders": "owner@example.test,reviewer@example.test",
            "authserv_id": "mx.example.test",
        }
    )
    assert config.allowed_senders == {"owner@example.test", "reviewer@example.test"}
    with pytest.raises(ValueError, match="authorized sender"):
        EmailConfig.parse(
            {
                "address": "agent@example.test",
                "password": "secret",
                "imap_host": "imap.example.test",
                "smtp_host": "smtp.example.test",
                "allowed_senders": "",
                "authserv_id": "mx.example.test",
            }
        )
    with pytest.raises(ValueError, match="STARTTLS"):
        EmailConfig.parse(
            {
                "address": "agent@example.test",
                "password": "secret",
                "imap_host": "imap.example.test",
                "smtp_host": "smtp.example.test",
                "smtp_security": "plain",
                "allowed_senders": "owner@example.test",
                "authserv_id": "mx.example.test",
            }
        )


def test_email_rejects_automated_messages() -> None:
    raw, _ = message_bytes("owner@example.test", "ignored", automated=True)
    from email.parser import BytesParser
    from email.policy import default

    message = BytesParser(policy=default).parsebytes(raw)
    assert _automated(message, "owner@example.test")
    assert _automated(message, "noreply@example.test")
    ordinary, _ = message_bytes("owner@example.test", "ordinary")
    assert not _automated(BytesParser(policy=default).parsebytes(ordinary), "owner@example.test")


def test_email_requires_trusted_aligned_dmarc_result() -> None:
    from email.parser import BytesParser
    from email.policy import default

    from xaa.channels.email import _authenticated_sender

    raw, _ = message_bytes("owner@example.test", "trusted")
    message = BytesParser(policy=default).parsebytes(raw)
    assert _authenticated_sender(message, "owner@example.test", "mx.example.test")
    assert not _authenticated_sender(message, "owner@example.test", "attacker.example.test")
    assert not _authenticated_sender(message, "owner@attacker.test", "mx.example.test")
    from email.parser import BytesParser
    from email.policy import default

    failed = BytesParser(policy=default).parsebytes(raw.replace(b"dmarc=pass", b"dmarc=fail"))
    assert not _authenticated_sender(failed, "owner@example.test", "mx.example.test")
    forged = BytesParser(policy=default).parsebytes(
        raw.replace(
            b"dmarc=pass header.from=example.test",
            b"dmarc=pass header.from=example.test\r\nAuthentication-Results: "
            b"mx.example.test; dmarc=fail header.from=example.test",
        )
    )
    assert not _authenticated_sender(forged, "owner@example.test", "mx.example.test")


def test_email_ingress_is_allowlisted_idempotent_and_delivery_is_never_ambiguous_retry(
    workspace_tmp_path,
) -> None:
    runs = RunStore(workspace_tmp_path / "email.sqlite3")
    store = EmailStore(runs)
    config = EmailConfig.parse(
        {
            "address": "agent@example.test",
            "password": "secret",
            "imap_host": "imap.example.test",
            "smtp_host": "smtp.example.test",
            "allowed_senders": "owner@example.test",
            "authserv_id": "mx.example.test",
        }
    )
    mailbox_id = store.save_config(config)
    assert store.poll_cursor(mailbox_id, "77") == 0
    store.advance_cursor(mailbox_id, "77", "5")
    assert store.poll_cursor(mailbox_id, "77") == 5
    assert store.poll_cursor(mailbox_id, "78") == 0
    store.advance_cursor(mailbox_id, "78", "2")
    assert EmailStore(RunStore(runs.path)).poll_cursor(mailbox_id, "78") == 2
    raw, message_id = message_bytes("owner@example.test", "Summarize the current task.")
    run_id = store.accept_message(mailbox_id, "77", "1", raw, "fixture/model")
    assert run_id
    assert store.accept_message(mailbox_id, "77", "1", raw, "fixture/model") is None
    assert (
        store.accept_message(
            mailbox_id,
            "77",
            "2",
            raw.replace(b"owner@example.test", b"other@example.test"),
            "fixture/model",
        )
        is None
    )
    automated, _ = message_bytes("owner@example.test", "Automated", automated=True)
    assert store.accept_message(mailbox_id, "77", "3", automated, "fixture/model") is None
    spoofed, _ = message_bytes("owner@example.test", "Spoofed", authenticated=False)
    assert store.accept_message(mailbox_id, "77", "4", spoofed, "fixture/model") is None
    assert runs.get_run(run_id)["mode"] == "observe"
    assert runs.get_run(run_id)["workspace"] is None
    assert runs.get_run(run_id)["state"] == "queued"
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET state='completed',updated_at=? WHERE id=?", (utc_now(), run_id)
        )
        RunStore.record_event(
            connection,
            run_id,
            "model.message",
            {"message": {"role": "assistant", "content": "Task complete."}},
        )
    delivery = store.claim_delivery(mailbox_id)
    assert delivery and delivery["sender"] == "owner@example.test"
    assert delivery["in_reply_to"] == message_id
    assert delivery["body"] == "Task complete."
    assert store.reconcile_sends() == 1
    assert store.claim_delivery(mailbox_id) is None
    unknown = store.status(config.address)["unknown_deliveries"]
    assert len(unknown) == 1
    assert unknown[0]["uid_validity"] == "77"
    assert unknown[0]["uid"] == "1"
    assert unknown[0]["run_id"] == run_id
    assert store.reconcile_delivery(config.address, "77", "1", "delivered")
    assert not store.reconcile_delivery(config.address, "77", "1", "retry")
    assert store.status(config.address)["deliveries"] == {"sent": 1}
    with runs.connection() as connection:
        audit = connection.execute(
            "SELECT resolution FROM email_delivery_reconciliations WHERE mailbox_id=? AND uid_validity='77' AND uid='1'",
            (mailbox_id,),
        ).fetchone()
    assert audit["resolution"] == "delivered"


def test_email_service_polls_imap_and_threads_reply_over_starttls(workspace_tmp_path) -> None:
    runs = RunStore(workspace_tmp_path / "email-service.sqlite3")
    store = EmailStore(runs)
    config = EmailConfig.parse(
        {
            "address": "agent@example.test",
            "password": "app-secret",
            "imap_host": "imap.example.test",
            "smtp_host": "smtp.example.test",
            "allowed_senders": "owner@example.test",
            "authserv_id": "mx.example.test",
        }
    )
    mailbox_id = store.save_config(config)
    raw, original_message_id = message_bytes("owner@example.test", "Run a harmless check.")
    credentials = MemoryCredentials()
    credentials.set(
        json.dumps(
            {
                "address": config.address,
                "imap_host": config.imap_host,
                "imap_port": str(config.imap_port),
                "smtp_host": config.smtp_host,
                "smtp_port": str(config.smtp_port),
                "smtp_security": config.smtp_security,
                "allowed_senders": "owner@example.test",
                "authserv_id": config.authserv_id,
            }
        ),
        "email-mailbox-config",
    )
    credentials.set(config.password, "email-mailbox-password")
    with runs.connection() as connection:
        connection.execute(
            "INSERT INTO provider_profiles(id,adapter,endpoint,selected_model,secret_reference,capability_snapshot,created_at,updated_at) VALUES('lm-studio','lm-studio','http://127.0.0.1:1234','fixture/model',NULL,NULL,?,?)",
            (utc_now(), utc_now()),
        )
    submitted: list[str] = []
    sent: list[EmailMessage] = []

    class FakeIMAP:
        def __init__(self, host, port, **kwargs) -> None:
            assert host == config.imap_host and port == config.imap_port
            assert kwargs["ssl_context"] is not None

        def login(self, address, password) -> None:
            assert (address, password) == (config.address, config.password)

        def select(self, mailbox, readonly) -> tuple[str, list[bytes]]:
            assert mailbox == "INBOX" and readonly
            return "OK", [b"1"]

        def response(self, key) -> tuple[bytes, list[bytes]]:
            assert key == "UIDVALIDITY"
            return b"UIDVALIDITY", [b"77"]

        def uid(self, command, *args):
            if command == "search":
                assert args[1] in {"UID 1:*", "UID 2:*"}
                return "OK", [b"1"] if args[1] == "UID 1:*" else [b""]
            assert command == "fetch" and args[0] == "1"
            return "OK", [(b"1 (UID 1 BODY[] {N}", raw)]

        def logout(self) -> None:
            return None

    class FakeSMTP:
        def __init__(self, host, port, timeout) -> None:
            assert host == config.smtp_host and port == config.smtp_port
            assert timeout > 0

        def starttls(self, context) -> None:
            assert context is not None

        def login(self, address, password) -> None:
            assert (address, password) == (config.address, config.password)

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

        def quit(self) -> None:
            return None

    service = EmailService(
        runs,
        credentials,
        submitted.append,
        imap_factory=FakeIMAP,
        smtp_factory=FakeSMTP,
    )
    assert service._fetch(config, mailbox_id) == [("77", "1", raw)]

    async def scenario() -> None:
        await service.cycle()
        assert len(submitted) == 1
        with runs.connection() as connection:
            connection.execute(
                "UPDATE runs SET state='completed',updated_at=? WHERE id=?",
                (utc_now(), submitted[0]),
            )
            RunStore.record_event(
                connection,
                submitted[0],
                "model.message",
                {"message": {"role": "assistant", "content": "The harmless check passed."}},
            )
        await service.cycle()

    asyncio.run(scenario())
    assert len(sent) == 1
    assert sent[0]["To"] == "owner@example.test"
    assert sent[0]["In-Reply-To"] == original_message_id
    assert sent[0].get_content().strip() == "The harmless check passed."
    assert store.status(config.address)["deliveries"] == {"sent": 1}
    assert store.mailbox(config.address)["id"] == mailbox_id


def test_email_smtp_starttls_uses_real_socket_and_verified_local_certificate(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "XAA SMTP fixture")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(key, hashes.SHA256())
    )
    certificate_path = tmp_path / "smtp-fixture-ca.pem"
    key_path = tmp_path / "smtp-fixture-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate_path))
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(certificate_path, key_path)
    captured: dict[str, Any] = {}

    class SMTPHandler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            connection = self.request
            connection.settimeout(5)
            reader = self.rfile
            writer = self.wfile
            writer.write(b"220 localhost XAA SMTP fixture\r\n")
            while line := reader.readline():
                command = line.decode("ascii").strip()
                verb, _, arguments = command.partition(" ")
                if verb.upper() == "EHLO":
                    writer.write(
                        b"250-localhost\r\n250-STARTTLS\r\n250 SIZE 100000\r\n"
                        if not captured.get("tls")
                        else b"250-localhost\r\n250-AUTH PLAIN LOGIN\r\n250 SIZE 100000\r\n"
                    )
                elif verb.upper() == "STARTTLS":
                    writer.write(b"220 Ready to start TLS\r\n")
                    writer.flush()
                    connection = server_tls.wrap_socket(connection, server_side=True)
                    captured["tls"] = connection.version()
                    self.request = connection
                    self.rfile = connection.makefile("rb")
                    self.wfile = connection.makefile("wb")
                    reader, writer = self.rfile, self.wfile
                elif verb.upper() == "AUTH":
                    encoded = arguments.partition(" ")[2]
                    if not encoded:
                        writer.write(b"334 \r\n")
                        writer.flush()
                        encoded = reader.readline().decode("ascii").strip()
                    decoded = base64.b64decode(encoded).decode("utf-8")
                    captured["auth"] = decoded
                    writer.write(b"235 2.7.0 Authentication successful\r\n")
                elif verb.upper() == "MAIL":
                    captured["mail_from"] = arguments
                    writer.write(b"250 2.1.0 Sender accepted\r\n")
                elif verb.upper() == "RCPT":
                    captured["rcpt_to"] = arguments
                    writer.write(b"250 2.1.5 Recipient accepted\r\n")
                elif verb.upper() == "DATA":
                    writer.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                    writer.flush()
                    body: list[bytes] = []
                    while message_line := reader.readline():
                        if message_line == b".\r\n":
                            break
                        body.append(
                            message_line[1:] if message_line.startswith(b"..") else message_line
                        )
                    captured["message"] = b"".join(body)
                    writer.write(b"250 2.0.0 queued\r\n")
                elif verb.upper() == "QUIT":
                    writer.write(b"221 2.0.0 bye\r\n")
                    writer.flush()
                    return
                else:
                    writer.write(b"250 OK\r\n")
                writer.flush()

    class SMTPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with SMTPServer(("127.0.0.1", 0), SMTPHandler) as server:
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            config = EmailConfig.parse(
                {
                    "address": "agent@example.test",
                    "password": "fixture-app-password",
                    "imap_host": "127.0.0.1",
                    "smtp_host": "127.0.0.1",
                    "smtp_port": str(server.server_address[1]),
                    "allowed_senders": "owner@example.test",
                    "authserv_id": "mx.example.test",
                }
            )
            service = EmailService(
                RunStore(tmp_path / "smtp-tls.sqlite3"), MemoryCredentials(), lambda _: None
            )
            message_id = service._send(
                config,
                {
                    "sender": "owner@example.test",
                    "subject": "X.A.A. task result",
                    "in_reply_to": "<incoming@example.test>",
                    "body": "The deterministic task completed.",
                },
            )
        finally:
            server.shutdown()
            server_thread.join(timeout=5)

    assert captured["tls"] in {"TLSv1.2", "TLSv1.3"}
    assert captured["auth"] == "\x00agent@example.test\x00fixture-app-password"
    assert str(captured["mail_from"]).lower().startswith("from:<agent@example.test>")
    assert str(captured["rcpt_to"]).lower() == "to:<owner@example.test>"
    assert message_id.encode("ascii") in captured["message"]
    assert b"In-Reply-To: <incoming@example.test>" in captured["message"]
    assert b"The deterministic task completed." in captured["message"]


def test_email_shutdown_waits_for_ambiguous_smtp_call_and_reconciles_unknown(
    workspace_tmp_path, monkeypatch
) -> None:
    runs = RunStore(workspace_tmp_path / "email-cancel.sqlite3")
    store = EmailStore(runs)
    config = EmailConfig.parse(
        {
            "address": "agent@example.test",
            "password": "app-secret",
            "imap_host": "imap.example.test",
            "smtp_host": "smtp.example.test",
            "allowed_senders": "owner@example.test",
            "authserv_id": "mx.example.test",
        }
    )
    mailbox_id = store.save_config(config)
    raw, _ = message_bytes("owner@example.test", "Run a harmless check.")
    run_id = store.accept_message(mailbox_id, "77", "1", raw, "fixture/model")
    assert run_id
    with runs.connection() as connection:
        connection.execute(
            "UPDATE runs SET state='completed',updated_at=? WHERE id=?", (utc_now(), run_id)
        )
        RunStore.record_event(
            connection,
            run_id,
            "model.message",
            {"message": {"role": "assistant", "content": "Complete."}},
        )
    credentials = MemoryCredentials()
    credentials.set(
        json.dumps(
            {
                "address": config.address,
                "imap_host": config.imap_host,
                "smtp_host": config.smtp_host,
                "allowed_senders": "owner@example.test",
                "authserv_id": config.authserv_id,
            }
        ),
        "email-mailbox-config",
    )
    credentials.set(config.password, "email-mailbox-password")
    service = EmailService(runs, credentials, lambda _: None)
    entered = threading.Event()
    release = threading.Event()

    def blocked_send(_config: EmailConfig, _delivery: dict[str, str]) -> str:
        entered.set()
        release.wait(timeout=5)
        return "<sent@example.test>"

    monkeypatch.setattr(service, "_fetch", lambda *_: [])
    monkeypatch.setattr(service, "_send", blocked_send)

    async def scenario() -> None:
        task = asyncio.create_task(service.cycle())
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert store.status(config.address)["deliveries"] == {"sending": 1}
    assert store.reconcile_sends() == 1
    assert store.status(config.address)["deliveries"] == {"unknown": 1}


def test_email_api_keeps_password_secret_and_rejects_unauthorized_access(
    workspace_tmp_path, monkeypatch
) -> None:
    import xaa.api.app as app_module
    from xaa.providers.lm_studio import LMStudioProvider

    class QuietProvider(LMStudioProvider):
        async def close(self) -> None:
            return None

    credentials = MemoryCredentials()
    runs = RunStore(workspace_tmp_path / "email-api.sqlite3")
    app = app_module.create_app(
        QuietProvider(), SecretStr("a" * 40), store=runs, credential_store=credentials
    )
    client = TestClient(app)
    assert client.get("/channels/email").status_code == 401
    response = client.put(
        "/channels/email/credential",
        headers={"Authorization": f"Bearer {'a' * 40}"},
        json={
            "address": "agent@example.test",
            "password": "highly-secret-app-password",
            "imap_host": "imap.example.test",
            "smtp_host": "smtp.example.test",
            "allowed_senders": ["owner@example.test"],
            "authserv_id": "mx.example.test",
        },
    )
    assert response.status_code == 200, response.text
    assert "highly-secret-app-password" not in response.text
    assert credentials.get("email-mailbox-password") == "highly-secret-app-password"
    assert client.get("/channels/email", headers={"Authorization": f"Bearer {'a' * 40}"}).json()[
        "configured"
    ]
    email_store = EmailStore(runs)
    raw, _ = message_bytes("owner@example.test", "API reconciliation")
    run_id = email_store.accept_message("agent@example.test", "77", "3", raw, "fixture/model")
    assert run_id
    with runs.connection() as connection:
        connection.execute(
            "UPDATE email_messages SET delivery_state='unknown' WHERE uid_validity='77' AND uid='3'"
        )
    status = client.get("/channels/email", headers={"Authorization": f"Bearer {'a' * 40}"}).json()
    assert status["unknown_deliveries"][0]["run_id"] == run_id
    rejected = client.post(
        "/channels/email/deliveries/77/3/reconcile",
        headers={"Authorization": f"Bearer {'a' * 40}"},
        json={"resolution": "retry", "confirmed_destination_checked": False},
    )
    assert rejected.status_code == 422
    reconciled = client.post(
        "/channels/email/deliveries/77/3/reconcile",
        headers={"Authorization": f"Bearer {'a' * 40}"},
        json={"resolution": "delivered", "confirmed_destination_checked": True},
    )
    assert reconciled.status_code == 200
    assert reconciled.json()["unknown_deliveries"] == []
    assert reconciled.json()["deliveries"]["sent"] == 1
    assert (
        client.post(
            "/channels/email/deliveries/77/3/reconcile",
            headers={"Authorization": f"Bearer {'a' * 40}"},
            json={"resolution": "retry", "confirmed_destination_checked": True},
        ).status_code
        == 409
    )
    retry_raw, _ = message_bytes("owner@example.test", "Retry after inspection")
    retry_run = email_store.accept_message(
        "agent@example.test", "77", "4", retry_raw, "fixture/model"
    )
    assert retry_run
    with runs.connection() as connection:
        connection.execute(
            "UPDATE email_messages SET delivery_state='unknown' WHERE uid_validity='77' AND uid='4'"
        )
    retried = client.post(
        "/channels/email/deliveries/77/4/reconcile",
        headers={"Authorization": f"Bearer {'a' * 40}"},
        json={"resolution": "retry", "confirmed_destination_checked": True},
    )
    assert retried.status_code == 200
    assert retried.json()["deliveries"]["pending"] == 1
    assert (
        client.delete(
            "/channels/email/credential", headers={"Authorization": f"Bearer {'a' * 40}"}
        ).status_code
        == 200
    )
    assert credentials.get("email-mailbox-password") is None

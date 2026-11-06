# Email channel setup and operation

X.A.A. supports a text-only IMAP mailbox with SMTP replies. The adapter polls at a bounded interval, processes at most 20 newly received messages per cycle, and limits message input and output. It does not process attachments, HTML-only bodies, commands, or bulk recipients.

## Configure a mailbox

In **Messaging channels → Email**, enter the mailbox address, its app password, IMAP host, SMTP host, exact authorized sender addresses, and the receiving mail server's `authserv-id`. The app password and mailbox settings are kept in Windows Credential Manager. The database stores only non-secret mailbox configuration and processing records.

Use IMAP over implicit TLS on port 993 and SMTP STARTTLS on port 587. The current desktop form uses those defaults. The SMTP TLS certificate is verified. For inbound authorization, X.A.A. requires both an exact sender address from the allowlist and a passing DMARC result in the first `Authentication-Results` header from the configured exact `authserv-id`; the authenticated `header.from` domain must match the sender domain. Every header claiming that same authserv-id must also pass and align, so a forged duplicate that conflicts with the receiving server's result is rejected. Find the authserv-id in a trusted message header added by your receiving mail provider. Messages without this evidence are ignored.

## Message handling

Authorized private email starts an Observe-mode run with no workspace, file, browser, command, or MCP grant. Input is bounded plain text and treated as untrusted user content. Mailbox UID plus UIDVALIDITY is the durable deduplication key; a persistent UID high-water mark prevents old ignored mail from starving new messages and resets when UIDVALIDITY changes. The RFC Message-ID is retained for reply threading. Unlisted senders, automated-message headers, common no-reply addresses, invalid DMARC evidence, empty messages, and oversized messages do not create runs.

Replies go only to the exact allowlisted sender. Each reply uses durable `pending → sending → sent/failed/unknown` state. If SMTP may have accepted a message but the client loses the result, X.A.A. records `unknown` and does not retry automatically, preventing duplicate messages. The original Message-ID is used in `In-Reply-To` and `References`.

The adapter currently supports one configured mailbox, plain-text messages, and no manual retry or attachment handling. Use a dedicated mailbox and a provider that stamps a trusted Authentication-Results header. Fixture tests verify authserv pinning, DMARC alignment, sender allowlisting, deduplication, run scoping, secret handling and ambiguous-send recovery; live IMAP/SMTP interoperability still requires a user-configured account.

## Crash recovery acceptance

Run `uv run python scripts/email_crash_diagnostic.py` to exercise the ambiguous SMTP boundary in separate processes. The diagnostic creates a real SQLite-backed delivery, then kills its email worker after a deterministic fsynced acceptor records acceptance and before the delivery journal can save `sent`. A fresh `EmailService.cycle()` must reconcile the abandoned `sending` row to `unknown` and must not submit it again. Evidence is written to `.acceptance/<id>/report.json`. The acceptor models the durable transport acceptance boundary; this is not live SMTP server acceptance or proof of interoperability with a mail provider.

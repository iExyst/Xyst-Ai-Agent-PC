# Signed generic webhook ingress

The generic webhook is a text-only, inbound channel. In **Messaging channels → Generic webhook**, create a shared HMAC key and copy it to the sending service immediately; X.A.A. stores the key in Windows Credential Manager and never returns it again. Rotating the key invalidates the previous sender configuration. Disabling ingress removes the saved key.

The endpoint is `POST /webhooks/inbound` on the authenticated local API. It accepts at most 64 KiB and requires these headers:

- `X-XAA-Timestamp`: Unix time in seconds, within five minutes of the engine clock.
- `X-XAA-Event-Id`: 1–128 ASCII letters, digits, `.`, `_`, `:`, or `-`.
- `X-XAA-Signature`: `sha256=` followed by lowercase hexadecimal HMAC-SHA256.

The body is UTF-8 JSON with `sender_id`, optional `conversation_id`, and `text`. Extra fields are rejected. Text is limited to 10,000 characters; sender and conversation identifiers are each limited to 256 characters.

The signature covers the exact body bytes and both identity headers, in this format:

```python
message = timestamp.encode("ascii") + b"." + event_id.encode("ascii") + b"." + body
signature = "sha256=" + hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
```

For example, a sender can construct `body` with compact JSON separators and send the unchanged bytes it signed. A successful delivery returns HTTP 202 and a run ID. Repeating the same event ID and body returns that same run; reusing the event ID with different body bytes returns HTTP 409. Invalid or stale signatures return HTTP 401.

Each event starts a separate Observe-mode run with the currently selected model, no workspace, and no channel-granted tools. Event text and identifiers remain untrusted input. The shared key establishes one configured sender trust boundary; anyone who receives the key can submit messages. The endpoint does not send replies, link cross-channel identities, or process attachments. X.A.A.'s API binds to loopback by default; any remote exposure must be deliberately configured and protected at the network boundary.

Fixture coverage verifies authenticated credential configuration, forged and stale signature rejection, body size limits, event deduplication, changed-content conflicts, and the no-workspace/no-tool run scope. It does not establish interoperability with any particular webhook provider or a publicly exposed deployment.

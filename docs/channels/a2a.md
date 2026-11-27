# A2A peer access

X.A.A. accepts bounded text tasks through an authenticated A2A JSON-RPC listener. Tasks use the same persistent coordinator as the desktop app, run in Observe mode, and receive no workspace or tool grants.

## Enable a listener

1. Open **Tools & integrations → Messaging channels → Agent-to-agent (A2A)**.
2. Create an A2A peer credential and copy the displayed secret to the connecting peer. X.A.A. stores the credential in Windows Credential Manager and shows the secret only once.
3. Select a PEM TLS certificate and matching private key. The certificate must cover the machine name or IP address that peers will use. Keep the private-key file protected by your Windows account permissions.
4. Keep **Allow TLS connections from other devices on this network** unchecked for local-only use. To accept peers on a LAN, check it and enable the listener. Network binding is refused without a valid certificate and private key. The Agent Card URL uses HTTPS, and connecting peers must trust the certificate.
5. If Windows Firewall blocks inbound connections, add a rule for the selected port. Give peers the desktop's machine name or IP address; `0.0.0.0` is the bind address, not a client destination.

The network listener exposes only `GET /.well-known/agent-card.json` and `POST /a2a`. The broader owner API remains bound to loopback. The peer credential can submit, stream, query, and cancel A2A tasks; it cannot access owner settings or grant tools/workspace permissions. TLS protects credentials and task contents in transit, but a publicly trusted certificate and a narrow firewall rule are still needed for peers outside the local machine.

## Supported protocol slice

The listener accepts canonical A2A v1 JSON-RPC names `SendMessage`, `SendStreamingMessage`, `GetTask`, and `CancelTask`, plus the older slash-style aliases. Input is text-only, limited to 64 KiB per request and 10,000 characters per message. Message IDs are deduplicated across retries, and task state remains queryable after the engine restarts. `GetTask` returns the bounded user/agent text history and a stable text artifact when a response is available. A candidate awaiting X.A.A. owner review reports `TASK_STATE_INPUT_REQUIRED`; the stream sends the candidate artifact and closes so the peer can poll for the owner's decision. `historyLength` is validated and capped at 100 entries. The source workflow also exercises discovery, streaming and task polling through the official `a2a-sdk` Python client against a local deterministic provider; it does not contact an external agent or use a live model.

Outbound A2A calls, push notifications, attachments, broader A2A methods, and live interoperability with an external agent are not implemented or verified yet. This is a partial channel implementation, not a claim of full A2A or Hermes parity.

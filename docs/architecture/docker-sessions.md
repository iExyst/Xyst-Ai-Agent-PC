# Reusable Docker session design

Hermes' Docker backend reuses a mutable container for a task session. X.A.A.'s
`docker-local-v1` keeps its disposable-per-command default and now offers an
explicit, default-off `docker_session_reuse` grant for a durable run/task
session. The grant retains X.A.A.'s restricted container policy; it does not
enable host mounts, arbitrary environment forwarding, or direct project writes.
The implementation is present in the command journal, Docker transport,
stopped-run recovery worker, and desktop consent control. This contract also
records the acceptance needed before the behavior can be marked verified.

## Contract boundary

Keep session reuse opt-in. Existing grants default to the prior disposable
behavior. A session is
private to one run and one specialist task (or the parent run when no specialist
owns the command). It uses a cryptographically random, durable session ID which
is not model supplied. Each command keeps its own execution ID, intent, output
receipt, timeout, cancellation, and idempotency record.

The initial session profile must retain X.A.A.'s enforced image digest, local
daemon endpoint, read-only image root, non-root user, capability drop, CPU,
memory, PID and bounded writable scratch limits. Network stays denied by
default; the existing explicit bridge grant remains a separate per-run decision.
Do not add host bind mounts or arbitrary environment forwarding as an incidental
part of session reuse. Those capabilities need independent owner grants and
their own canonicalization, secret-redaction, and acceptance requirements.

The container workspace may persist between commands inside the session, but it
must remain isolated from the project directory. When a session closes, X.A.A.
may preserve a bounded, digest-checked archive as an untrusted run artifact for
owner review. It must never copy session changes directly into the project.

## Durable lifecycle

Persist a session reservation before asking Docker to create a container. Store
only the session identity, owning run/task, daemon and image profile digests,
policy version, lifecycle state, timestamps, and container identity. Container
labels bind the daemon object to the saved session and profile. Each command
retains its own execution identity in the durable intent. Use explicit states
for reserved, creating, ready, busy, stopping, closed, and
unknown; state transitions and active-command ownership must be transactional.

On restart, inspect the saved container and labels before any action. Never
replay an interrupted command because a container is still present. The current
recovery path removes the saved session and preserves the existing command
reconciliation behavior; it does not restore a session for reuse after an
ambiguous result. Do not make a session available to another run or specialist
after an ambiguous result.

Run transitions durably mark sessions for cleanup; the stopped-run recovery
worker removes labeled containers and closes records only after observing
removal. An unconfirmed container remains a durable cleanup obligation. Current
acceptance does not yet prove every crash point, app shutdown edge, or aggregate
session resource budget.

## Acceptance gate

Before marking reusable Docker sessions implemented, use a real local Docker
daemon and the pinned image to verify:

1. Two sequential commands in one run observe a deliberate file and working
   directory change made by the first command; a different run and specialist
   cannot observe either session.
2. The image root remains read-only, only bounded private writable mounts exist,
   resource and network policy matches the saved grant, and host sentinels and
   Docker credentials remain inaccessible.
3. Command cancellation and timeout stop the active exec without replay; crash
   tests cover reservation, container creation, active exec, output receipt,
   session close, and app restart.
4. Normal completion, cancellation, failed execution, grant revocation, and
   restart all close containers or retain an explicit unresolved cleanup state.
5. Session output can be exported only as a bounded, verified run artifact and
   reaches the project only through the existing owner-reviewed integration
   path.

Hosted Linux Docker CI can provide repeatable daemon acceptance, but Windows
installer acceptance must also verify the installed coordinator and recovery
path. Until those checks pass, the Hermes Docker session capability remains
planned and the disposable X.A.A. backend remains the only enabled Docker
profile.

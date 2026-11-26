# Reusable Docker session design

Hermes' Docker backend reuses a mutable container for a task session. X.A.A.'s
`docker-local-v1` deliberately creates one disposable container per command and
records that the container was removed before the command is accepted. Reuse
cannot be added by simply leaving the existing container running: the command
journal, cleanup proof, task isolation, and restart recovery all currently rely
on per-command ownership.

## Contract boundary

Implement reusable sessions as a separate opt-in backend/profile. Keep
`docker-local-v1` behavior and its existing grants unchanged. A session is
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
labels must bind the daemon object to the saved session and execution identity.
Use explicit states for reserved, creating, ready, busy, stopping, closed, and
unknown; state transitions and active-command ownership must be transactional.

On restart, inspect the saved container and labels before any action. Never
replay an interrupted command because a container is still present. Reconcile a
completed receipt only when its execution ID and output digests match the
journal; otherwise mark the command outcome unknown, stop the session, and
require the existing owner-review/recovery path. Do not make a session
available to another run or specialist after an ambiguous result.

Close sessions on run completion, cancellation, failure, explicit stop, profile
revocation, and application shutdown. Cleanup must be idempotent and observed;
an unconfirmed container remains a durable cleanup obligation. Startup recovery
must find orphaned session reservations and labeled containers without relying
on in-memory state. Bound concurrent sessions and aggregate their resources
against the owning run/task budgets.

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

# Reusable Docker session design

Hermes' Docker backend reuses a mutable container for a task session. X.A.A.'s
`docker-local-v1` keeps its disposable-per-command default and now offers an
explicit, default-off `docker_session_reuse` grant for a durable run/task
session. The grant retains X.A.A.'s restricted container policy; it does not
enable host mounts, arbitrary environment forwarding, or direct project writes.
The implementation is present in the command journal, Docker transport,
stopped-run recovery worker, and desktop consent control. It is implemented and
available only by owner grant; full Hermes session parity remains unverified.

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
must remain isolated from the project directory. A separate default-off owner
grant can export a tar archive after a successful command. The archive is capped
at 8 MiB, must contain at most 4096 regular files with at most 32 MiB of declared
content, and rejects traversal paths, duplicate paths, links, special files, and
privileged mode bits. The app validates the tar structure and digest before
storing it as an untrusted run artifact. Download and inspect it through the
artifact panel; session files are never copied directly into the project.

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

## Acceptance status

Hosted Docker run [45778101064](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions/runs/45778101064)
passed all 12 live-engine cases using Docker 28.0.4 and the pinned Python image
([JUnit evidence](../../.acceptance/hosted-docker-session-45778101064/junit.xml)).
The tested slice covers sequential-command workspace persistence, isolation
between distinct session IDs, default-denied network and the explicit bridge
grant, timeout and cancellation cleanup, bounded command output, process/CPU
limits, manager-restart cleanup without command replay, and orphan cleanup.

The archive validator and owner grant have local unit, controller, API download,
desktop build, and browser coverage. Hosted Docker run [45783624096](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions/runs/45783624096)
passed all 13 live-engine transport cases, including archive content, receipt, and project isolation checks
([JUnit evidence](../../.acceptance/hosted-docker-session-45783624096/docker-local-acceptance-45783624096/junit.xml)). The newer hosted Docker run [45787095885](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions/runs/45787095885)
passed 14 live-engine cases, adding the SQLite-backed command controller, repeated session commands, archived artifact receipt and contents, and stopped-run recovery
([JUnit evidence](../../.acceptance/hosted-docker-coordinator-45787095885/docker-local-acceptance-45787095885/junit.xml)).
Hosted Docker run [45793528249](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions/runs/45793528249)
passed all 14 live-engine cases and extends coordinator evidence to task isolation:
two specialist tasks in one run received distinct sessions, task B could not read
task A's private file, and stopped-run recovery cleaned the parent and both task
sessions ([JUnit evidence](../../.acceptance/hosted-docker-task-session-45793528249/docker-local-acceptance-45793528249/junit.xml)).
The prior coordinator scenario also verifies a second run receives a separate
session with no access to the first run's private file, archive receipt, and
run-stop cleanup. Exact-source Windows CI [45793528115](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions/runs/45793528115)
passed on `5fdf666a` with 1,783 Python tests and 43 skipped, plus configured
browser, static, type, desktop, API-contract, and process-recovery checks.
The wider acceptance gate is still open: real-engine coverage does not yet prove
host-sentinel and Docker-credential isolation, crashes during reservation,
container creation, active execution, output receipt, or close, installed
Windows coordinator/grant/artifact behavior, aggregate session resource budgets,
or cleanup after every grant-revocation and failed-execution path. A focused
Windows unit regression verifies Docker CLI launches use a private temporary
configuration and omit inherited Docker authentication/configuration, SSH-agent,
cloud-secret, and user-profile environment variables; live daemon credential
handling and host-sentinel acceptance remain open. Exact-source Windows workflow
[45796858646](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions/runs/45796858646)
passed on `413f243b` with 1,784 Python tests and 43 skipped, plus the configured
browser, static, type, desktop, API-contract, and process-recovery checks. Docker
is not installed on the development Windows host, so its live acceptance runs
on the hosted Docker runner.

A new hosted acceptance case now kills the worker process group while a journaled
Docker session command is active, then invokes stopped-run recovery and checks
that the call is sealed without replay. The embedded worker and local collection
checks pass. GitHub rejected manual dispatch because its default-branch copy of
`engine.yml` lacks `workflow_dispatch`, so a path-filtered Docker workflow now
runs the live suite when these transport or acceptance files change. The first
hosted run passed the 14 existing cases but caught an invalid 45-second grant in
the new fixture before worker startup; the fixture now uses the supported
30-second limit. Corrected hosted Docker run
[45803924172](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions/runs/45803924172)
passed all 15 live-engine cases on Docker 28.0.4 and the pinned Python image.
The process-death scenario killed the active worker group, observed the durable
busy session, removed its container through stopped-run recovery, and confirmed
the pending call was sealed without completion or replay
([JUnit evidence](../../.acceptance/hosted-docker-worker-crash-45803924172/junit.xml)).
Exact-source Windows verification for the corrected test is still running.
Session changes must not be copied into the project directly and should reach
it only through owner review.

The disposable-per-command profile remains the default. Reusable sessions are
available only when the owner opts in, and they should not be described as full
Hermes parity until the remaining lifecycle and parity gates pass.

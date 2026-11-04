# Application HTTP API (v1 contract outline)

Loopback-only by default. Desktop requests require authentication; accepted origins are restricted. Event streams use authenticated sequence numbers and replay cursors.

| Method | Route | Contract intent |
|---|---|---|
| POST | `/runs` | Create a run from objective, acceptance criteria, workspace and requested mode. |
| GET | `/runs/{id}` | Read durable run state and checkpoint. |
| POST | `/runs/{id}/pause` | Request cooperative pause. |
| POST | `/runs/{id}/resume` | Resume a paused run after permission/state validation. |
| POST | `/runs/{id}/cancel` | Cancel run and propagate cancellation to active work. |
| GET | `/runs/{id}/events?after={sequence}` | Replay and stream ordered events. |
| POST | `/approvals/{id}/decision` | Record an authorized approve/deny decision. |
| GET | `/providers` | List configured adapters and health status without secret material. |
| POST | `/providers/{id}/diagnostics` | Run capability and harmless tool round-trip diagnostics. |
| GET | `/models` | Return discovered models and verified capabilities. |
| GET | `/skills` | List skills and lifecycle/version metadata. |
| GET | `/memory` | Search/list memory subject to scope and access controls. |
| GET | `/schedules` | List durable schedules and execution status. |

Schemas, error envelope, auth mechanism, pagination, and generated UI types must be finalized before Phase 1 implementation.

Implemented extension: `GET /runs/{id}/events/stream` provides bearer-authenticated SSE replay and live delivery. Resume with `Last-Event-ID` or `after`; the greater nonnegative cursor wins. Each persisted event has `id: sequence` and event type `run_event`. Completed, failed and cancelled runs drain saved events before emitting `done`. Keepalive comments do not advance the cursor. The JSON `/events` route remains available for snapshots.

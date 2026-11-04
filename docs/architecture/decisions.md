# Architecture decisions

Status: proposed baseline, 2026-11-03.

| Decision | Choice | Rationale / constraint |
|---|---|---|
| Desktop shell | Tauri 2 | Windows packaging; keep UI as a client of the engine API. |
| UI | React + TypeScript | Chat, run state, approvals, settings. No UI-owned agent loop. |
| Engine | Python, strict typing | Modular coordinator, provider adapters, tools and persistence. |
| Local API | FastAPI + Pydantic | Typed, authenticated loopback boundary. |
| Persistence | SQLite WAL, versioned migrations | Durable events, state transitions, recovery; artifacts stored separately. |
| Search | SQLite FTS5 initially | Add embeddings only after retrieval evaluation shows value. |
| Default inference | Dedicated LM Studio adapter | No cloud fallback unless explicitly configured. |
| Concurrency | One inference request by default | Share local model through a queue; tune only with hardware evidence. |
| Browser | Playwright, permission-controlled | Browser output remains untrusted input. |
| Extensions | MCP adapters and versioned skills | Capability discovery must include permissions and lifecycle. |
| Packaging | Bundle engine with desktop app | End users should not need Python or Node installed. |
| Initial platform | Windows | Keep engine interfaces portable; defer platform-specific features behind adapters. |

## Dependency direction

Desktop / CLI / channel adapters → authenticated local API → persistent run coordinator → provider, tool, memory, skill, scheduler and storage modules → LM Studio or explicitly configured external services.

The coordinator owns state, permissions, budgets, cancellation, leases, checkpoints and completion. The model proposes actions; validated application code decides whether and how to execute them.

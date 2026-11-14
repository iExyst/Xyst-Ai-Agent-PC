# Pinned Hermes model-provider core audit

Reviewed 2026-11-14 against Hermes core commit `1e208b280deb18f01d015b0acdf3ef3552d08039`. This compares the pinned provider abstraction and registry with X.A.A.'s shipped provider boundary. It is an architecture audit, not provider runtime acceptance or a claim of parity.

## Pinned reference files

| Source | SHA-256 | Audited behavior |
| --- | --- | --- |
| [`providers/base.py`](https://github.com/NousResearch/hermes-agent/blob/1e208b280deb18f01d015b0acdf3ef3552d08039/providers/base.py) | `d0a8c867ae0e870ad617b4da2ab49b3302cc7f107508ebf9828c96a5aeb000dc` | Declarative `ProviderProfile` includes endpoint/auth metadata, streaming/model-list/health support, credential hooks, error classification, vision and request capabilities, process adapters, model metadata, and provider-specific setup surfaces. |
| [`providers/__init__.py`](https://github.com/NousResearch/hermes-agent/blob/1e208b280deb18f01d015b0acdf3ef3552d08039/providers/__init__.py) | `377ad0cf1b4b7817bd47b6cd8f6a2671e528ef14cc6ef263467d91b9180b5fcc` | Registry combines bundled profiles with per-HERMES_HOME user profiles and Python entry-point providers. It supports profile overrides, lazy discovery, refresh, and profile-scoped lookup. |

The profile is a shared description and hook collection, not proof that every registered provider implements every capability. Capability flags, hooks, fallback metadata, and provider-specific setup remain part of the contract.

## X.A.A. implementation at this revision

- `LMStudioProvider` owns the OpenAI-compatible chat/stream transport plus LM Studio native model metadata, lifecycle, tokenization, and scanned-document operations.
- `OpenAICompatibleProvider` subclasses `LMStudioProvider`. It reuses the chat transport but explicitly disables exact token counting, model load/unload, and scanned-PDF OCR. Its endpoint is owner-selected; remote endpoints require HTTPS, while HTTP is restricted to loopback.
- `ProviderRouter` dispatches the unchanged LM Studio model IDs and the `openai-compatible::` namespace. It currently knows those two adapters directly; it is not a provider plugin registry.
- The authenticated API and persisted settings likewise expose two fixed provider profiles. The desktop has an explicit setup flow for those profiles. It does not import arbitrary Python providers from a profile or plugin directory.
- `ModelInfo` currently reports context, size, loaded state, vision, and trained-for-tool-use metadata. Tool use still requires the harmless diagnostic and runtime validation; advertised metadata alone does not grant execution.

## Scope decision and gaps

Keep LM Studio as the default and retain an explicit, statically wired adapter boundary for now. Do not copy Hermes' user-code import or pip entry-point discovery into the Windows application: the pinned reference shows those as upstream capability surfaces, while X.A.A.'s installer, credential boundary, permission model, and extension lifecycle have not established a safe provider-plugin trust model. Adding dynamic provider discovery now would introduce an unverified code-loading path and would not close a user-facing release gate.

The current optional OpenAI-compatible adapter is a real alternate transport, but the provider surface is incomplete: there is no common declarative capability profile, adapter registration API, provider-owned authentication/setup hook contract, or multi-provider acceptance matrix. Its fixed two-provider implementation must remain described as partial. A future change should introduce a typed adapter contract only alongside a real third adapter or a focused capability that consumes it; a protocol-only scaffold would not count as implementation.

The audit does not test the external OpenAI-compatible server, send a model request, or validate any additional provider. Live provider/model acceptance remains deferred to the final LLM-testing phase as directed.

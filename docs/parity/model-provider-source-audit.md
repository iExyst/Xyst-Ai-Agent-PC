# Pinned optional model-provider source audit

Reviewed 2026-11-14 against the catalog-selected `NousResearch/hermes-official-plugins` commit `83f0a896b3518c7131181838e340c9a80c79524d`, subdirectory `claude-subscription-directsdk`, version 0.3.4. The catalog manifest is `plugin-catalog/claude-subscription-directsdk.yaml` at Hermes core commit `1e208b280deb18f01d015b0acdf3ef3552d08039`. This is a source review of one optional provider candidate; it does not implement or validate that provider in X.A.A.

## Reviewed source

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `plugin.yaml` | `ae4f7cd3b325cb5c9397d7f09d93269a11b3733bcc977cd3c0ef847d26854c17` | Declares an experimental model provider, MIT license, Hermes 0.21.4 minimum, `claude` executable dependency, and optional executable/config-directory environment variables. The install command is documented, not automatic. |
| `__init__.py` | `e84d62a1331137049397d7f09d93269a11b3733bcc977cd3c0ef847d26854c17` | Registers the provider, model metadata, setup discovery and error mapping. It routes requests through an external CLI profile and exposes its model list and selected usage information. |
| `directsdk.py` | `daac8aafe41e554c25480301446f7003873d21f2acd053366c0b47de7d2011fd` | Converts the host chat request to Claude Code stream-json, creates a request-scoped temp directory and local admission relay, disables native tools/session persistence, and owns child-process lifecycle and cancellation. |
| `admission.py` | `fe6f9faa4cd0e5d7887343af5f0ca396f083320df467a8145081fcb9676033a6` | Binds the relay to loopback with an ephemeral port and random route, limits each native process to one upstream request, forwards to HTTPS, captures bounded response state, and suppresses HTTP request logging. |
| `directsdk_setup.py` | `0c5d98db905b394afda6e08e4b046445f347c3d17af2f29e81ac2f465910d0c8` | Queries the installed CLI for login and model-picker state; the plugin does not implement a separate OAuth credential store. |
| `model_catalog.py` | `fd7f1c1af30ac6f335b1cc225ec5e9b70b42c03cc751eea0e14ad6f5b2fcfe40` | Pins aliases and context/capability metadata; account-discovered IDs can also appear in the picker. |
| `inert_mcp.py` | `6c5a0f64e4470ae90c6171bd83e2b537f641ff6d06cd76b3d49edba227274cb6` | Exposes the request tool schema to the CLI while refusing to execute tool calls; Hermes remains responsible for tool validation and execution. |
| `README.md` | `2555d4889077dc20bd8b822875f5f36118b160d1a4d9f8c2899c303b6663183e` | Describes account usage, telemetry defaults, supported proxy behavior, version constraints and upstream self-reported live qualification. These are upstream claims, not independent X.A.A. evidence. |

## Behavior and trust boundaries

- The provider runs the user's separately installed and authenticated official Claude Code CLI. Its prompt and tool traffic goes to Anthropic through that CLI; using a subscription does not make the provider local. The manifest identifies the candidate as experimental and requires an external executable. The provider inherits the CLI account's entitlement, limits and potential usage-credit charges; its local list-price estimates are not proof of the actual subscription charge.
- The core catalog manifest has an empty `platforms` list, while the plugin README claims Linux, macOS and Windows support. Treat that metadata mismatch as unresolved rather than inferring support or lack of support; X.A.A. has not run this provider on any platform.
- The child process receives the host request and history. Its working directory and inherited environment are user-scoped. The adapter checks inherited Anthropic API-key, auth-token, endpoint and cloud-backend overrides when using the normal inherited environment path; callers that explicitly inject `env=` bypass that guard, so an embedding host must not treat the constructor as a policy boundary.
- The request-scoped temporary directory contains the system prompt, tool schema and CLI settings. Cleanup is best-effort to tolerate a child retaining files briefly. A host adapter would need a clear local-data retention boundary and cleanup failure reporting.
- The command disables Claude Code's built-in tools, slash commands, auto-memory, CLAUDE.md loading, compaction and session persistence. An inert local MCP server carries Hermes tool schemas, while the host retains approval and tool execution. The relay binds to `127.0.0.1`, uses an ephemeral port and random route, accepts only HTTPS upstreams (or a loopback HTTP test fixture), suppresses request logging, and blocks a second upstream request from the same native process. The adapter disables native request retries and kills its owned process tree on cancellation.
- The telemetry and feature-flag switch defaults on in the manifest. Turning it off disables selected nonessential CLI traffic, but may reduce which models the CLI lists. This behavior needs to be disclosed and controlled by the owner if a similar adapter is ever offered.
- The relay honors standard HTTP CONNECT proxy variables for HTTPS. An unsupported SOCKS or TLS-to-proxy value is warned about and ignored, after which the adapter connects directly; this is a fail-open proxy policy and is unsuitable wherever proxy use is a security requirement. X.A.A. should fail closed when an explicitly required network route is unavailable.
- The README reports upstream fixture and real-account qualifications, including cancellation and streaming. We inspected those test sources but did not run them, sign in, install the CLI, contact Anthropic, or make a model request. Those claims do not establish X.A.A. compatibility or runtime behavior.

## X.A.A. disposition and limits

The catalog candidate remains `planned`, Phase 9. This audit classifies one entry in the 90-item `models` catalog category; it does not establish that every catalog item is a provider, does not evaluate all model candidates, and does not add a provider to X.A.A. LM Studio remains the default. Any later external-process provider needs an explicit remote-data and account-usage notice, owner-selected account/profile, credential ownership boundary, model capability/health checks, complete cancellation, retry/idempotency semantics, model-free fixture coverage, and a real provider acceptance deferred to the final LLM-testing phase. The upstream provider's permissive proxy fallback must not be copied.

No external executable, credential, model or inference service was used. The audit is limited to the pinned candidate source and its declared behavior; dependency installation, signature trust, current-vendor compatibility, account entitlement, charge accounting, and clean-environment behavior remain unverified.

## Groq provider

Reviewed 2026-11-14 against the catalog-pinned `AhmetArif0/hermes-groq-provider` commit `b5be0388c2b4cae8ed6a30c7371aaa1330731ee8`, subdirectory `groq-provider`, version 1.0.0. Catalog manifest SHA-256: `012d68116e63968dedc89a744d5be396a42cab442a28d972eff60f8f8d760592`. The source checkout is preserved at `../../.acceptance/groq-provider-source-b5be0388/`; no plugin code or test was executed.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `plugin.yaml` | `3a5a12a80929fbad519b52a20f0d686982abeb803a300ea91a7e4d2cedbe95d0` | Declares one MIT model-provider plugin, requires Hermes 0.21.5+, and names `GROQ_API_KEY`. |
| `__init__.py` | `8a6279579bf286de75d9f1555dedab6d014b396885e8e310a326fdcdd3b75fba` | Registers one `ProviderProfile`, maps reasoning levels for two model-ID prefixes, and filters the returned model list by excluded ID substrings. It imports only Hermes provider APIs and typing. |
| `README.md` (root and plugin copy) | `35cc4f12c669586376470fc69605eec1b3046cfdcdf75cbf9164aa45e13389a5` | Describes remote Groq inference, key setup, cost/rate constraints, and maintainer-reported API tests dated 2026-09-29. Those live-behavior statements are upstream claims, not evidence independently verified by X.A.A. |
| `LICENSE` | `ebb10510516bcfff892bee99840b4225b63f77087243a505ed45b61e7016573` | MIT license. This applies to the candidate, not automatically to X.A.A. |
| `tests/test_profile.py` | `6b89d4dde2e2a69bde833334a344fb3425832762659861393ce833c526c39793` | Unit fixtures cover effort mapping, filtering, fallback IDs, and registration using a stand-in Hermes profile and recorded model catalog. |
| `tests/test_hermes_integration.py` | `7a00e5844969c32617e37499d73d77210bdb0f7d210e817918a410e4f8de5c95` | The declared E2E tests use a local catalog server and Hermes' fake loopback LLM with a dummy key; the source does not declare tests against a live Groq endpoint. They install the candidate into temporary test homes and spawn Python subprocesses. These are upstream test designs, not executions performed here. |
| `tests/test_packaging.py` | `e0b951850f7a65839d5c266d56fb428bf0dfb46f12f7e873c1211f513b79f636` | AST checks assert the plugin imports only provider APIs and does not directly read environment variables, open connections, create processes, or touch files. |
| `.github/workflows/tests.yml` | `5cb681beb9743ee5370943c559c214005197b7b5918e1064135a2b7e2518accc` | CI declares unit runs on Ubuntu, macOS, and Windows with Python 3.11/3.14, plus Hermes 0.21.5 and `main` fixture integration and plugin validation. CI status was not queried for this audit. |

## Behavior and trust boundaries

- The plugin registers one model provider and no tools, hooks, commands, or background workers. Its single production source file does not itself read `GROQ_API_KEY`, create files, spawn processes, or open sockets. Hermes reads the key and its own OpenAI-compatible client sends model-list and inference requests to Groq.
- `model.base_url` can override the default Groq endpoint. Therefore the configured endpoint receives the credential and prompts through Hermes; the plugin does not bind the key to Groq's hostname. An X.A.A. adapter must make endpoint and credential routing explicit and must not silently reuse secrets across endpoints.
- `runs_in_hermes` is an allow-by-default substring blacklist (`whisper`, `orpheus`, `prompt-guard`, `allam`) rather than capability validation. Newly added or renamed models can enter the picker without proving chat, tool, or context support. The recorded-catalog tests validate only the fixture's current IDs. X.A.A. should use explicit discovered capability metadata or a carefully reviewed allowlist.
- Reasoning behavior is hard-coded by model ID prefix for `openai/gpt-oss-` and `qwen/qwen3.8-`. Unknown models omit the field. This is a vendor-specific compatibility map; it is not a general reasoning capability contract. The mapping lowers unsupported requested levels and cannot establish current vendor acceptance without live checks.
- The plugin declares no tools or local execution surface. Using it sends prompts and conversation content to a remote inference service, and use is subject to the owner's Groq account, limits, and charges. The README's rate-limit and plan recommendations are maintainer observations from 2026-09-29, not current X.A.A. guarantees.
- The workflow source defines cross-platform unit and Hermes fake-provider integration jobs, but no workflow result was inspected. Its fixtures do not prove current live API/model compatibility.

## X.A.A. disposition and limits

The catalog candidate remains `planned`, Phase 9. This review classifies two of the 90 model-category candidates (Groq and Claude); it does not establish that the rest are providers or evaluate their source. It does not add dynamic provider loading or a Groq adapter to X.A.A. The earlier provider-core audit records why arbitrary plugin imports remain deferred until a provider-code trust model and capability contract exist. Live provider and model testing remains deferred to the final LLM-testing phase as requested.

No plugin, test, dependency, executable, account credential, live endpoint, or model was used. This is a pinned-source review only; installed behavior, current vendor model metadata, authentication routing, account entitlement, pricing, and live compatibility remain unverified.

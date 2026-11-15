# Pinned optional model-provider source audit

This document records source-only reviews of selected optional provider candidates at their individual catalog-pinned commits. The catalog itself is pinned to Hermes core commit `1e208b280deb18f01d015b0acdf3ef3552d08039`. These reviews do not implement or validate providers in X.A.A.; live provider and model testing remains deferred to the final LLM-testing phase.

## Abstract provider

Reviewed 2026-11-15 at the catalog-pinned repository commit `e322e42578586af1c376b7e836dfce437308682a`. Hermes catalog manifest: `plugin-catalog/abstract-provider.yaml`, SHA-256 `e2b5034bd532b86f916b4bcb4dd4d05910925cd96027e102d90bcc78d897d830`. The manifest requires Hermes `>=0.22.2`, declares `ABSTRACT_API_KEY`, and states that it registers a model provider only; the catalog pins the source repository rather than distributing its code.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/abstractpbc/hermes-abstract-provider/blob/e322e42578586af1c376b7e836dfce437308682a/plugin.yaml) | `cf7ac7785e285bf6532f823dac0a0af643223a6509288635222d18655624d5b2` | Declares `kind: model-provider`, version 1.0.0, and the secret `ABSTRACT_API_KEY`. |
| [`__init__.py`](https://github.com/abstractpbc/hermes-abstract-provider/blob/e322e42578586af1c376b7e836dfce437308682a/__init__.py) | `dd75aee1a7776ddf3704733dfaa480399e9ce47c9099e3b20f18e853878f508a` | Registers one `ProviderProfile` using `codex_responses`, a fixed three-model list, no live model listing, no vision, and a 65,536-token output default. |
| [`README.md`](https://github.com/abstractpbc/hermes-abstract-provider/blob/e322e42578586af1c376b7e836dfce437308682a/README.md) | `489ec4d1f3feecb7b8b6d7f7ce22c5b3e6e08133a800a9954620559ab82da758` | Describes an OpenAI Responses API endpoint, text-only model set, API key configuration, remote account billing, and a 2 MiB request-body limit. Model names and price values are upstream declarations, not verified service behavior. |
| [`LICENSE`](https://github.com/abstractpbc/hermes-abstract-provider/blob/e322e42578586af1c376b7e836dfce437308682a/LICENSE) | `109a64add57bc9dc1980fd221ff642398506155977e8a4e7dc200bbe6ebdb13c` | MIT license for the candidate source. |

### Behavior and X.A.A. disposition

The plugin's README says it only registers a provider; Hermes' own client sends prompts, context, and tool definitions to `https://api.abstract.inc/v1` with `ABSTRACT_API_KEY`, and usage is billed to the Abstract account. The plugin selects the OpenAI Responses API `codex_responses` mode rather than Chat Completions, so X.A.A.'s current OpenAI-compatible chat adapter cannot directly represent it. Its static model list avoids treating the service's asynchronous lanes as synchronous completions. This is upstream-declared behavior from the pinned source, not independent endpoint or billing verification.

The candidate remains planned for Phase 9 and is not implemented. A compatible X.A.A. adapter would require an explicit Responses API transport, a remote-data and account-cost notice, OS-vault credential storage, bounded request/response handling, cancellation and retry semantics, deterministic protocol fixtures, and final live-provider acceptance after the requested LLM-test deferral. No plugin was installed, no key or account was used, and no request was sent to the inference service.

## Claude subscription provider

Reviewed 2026-11-14 against the catalog-selected `NousResearch/hermes-official-plugins` commit `83f0a896b3518c7131181838e340c9a80c79524d`, subdirectory `claude-subscription-directsdk`, version 0.3.4. The catalog manifest is `plugin-catalog/claude-subscription-directsdk.yaml` at the Hermes core pin.

### Reviewed source

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

### Behavior and trust boundaries

- The provider runs the user's separately installed and authenticated official Claude Code CLI. Its prompt and tool traffic goes to Anthropic through that CLI; using a subscription does not make the provider local. The manifest identifies the candidate as experimental and requires an external executable. The provider inherits the CLI account's entitlement, limits and potential usage-credit charges; its local list-price estimates are not proof of the actual subscription charge.
- The core catalog manifest has an empty `platforms` list, while the plugin README claims Linux, macOS and Windows support. Treat that metadata mismatch as unresolved rather than inferring support or lack of support; X.A.A. has not run this provider on any platform.
- The child process receives the host request and history. Its working directory and inherited environment are user-scoped. The adapter checks inherited Anthropic API-key, auth-token, endpoint and cloud-backend overrides when using the normal inherited environment path; callers that explicitly inject `env=` bypass that guard, so an embedding host must not treat the constructor as a policy boundary.
- The request-scoped temporary directory contains the system prompt, tool schema and CLI settings. Cleanup is best-effort to tolerate a child retaining files briefly. A host adapter would need a clear local-data retention boundary and cleanup failure reporting.
- The command disables Claude Code's built-in tools, slash commands, auto-memory, CLAUDE.md loading, compaction and session persistence. An inert local MCP server carries Hermes tool schemas, while the host retains approval and tool execution. The relay binds to `127.0.0.1`, uses an ephemeral port and random route, accepts only HTTPS upstreams (or a loopback HTTP test fixture), suppresses request logging, and blocks a second upstream request from the same native process. The adapter disables native request retries and kills its owned process tree on cancellation.
- The telemetry and feature-flag switch defaults on in the manifest. Turning it off disables selected nonessential CLI traffic, but may reduce which models the CLI lists. This behavior needs to be disclosed and controlled by the owner if a similar adapter is ever offered.
- The relay honors standard HTTP CONNECT proxy variables for HTTPS. An unsupported SOCKS or TLS-to-proxy value is warned about and ignored, after which the adapter connects directly; this is a fail-open proxy policy and is unsuitable wherever proxy use is a security requirement. X.A.A. should fail closed when an explicitly required network route is unavailable.
- The README reports upstream fixture and real-account qualifications, including cancellation and streaming. We inspected those test sources but did not run them, sign in, install the CLI, contact Anthropic, or make a model request. Those claims do not establish X.A.A. compatibility or runtime behavior.

### X.A.A. disposition and limits

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

The catalog candidate remains `planned`, Phase 9. This review classifies three of the 90 model-category candidates (Abstract, Groq, and Claude); it does not establish that the rest are providers or evaluate their source. It does not add dynamic provider loading or a Groq adapter to X.A.A. The earlier provider-core audit records why arbitrary plugin imports remain deferred until a provider-code trust model and capability contract exist. Live provider and model testing remains deferred to the final LLM-testing phase as requested.

No plugin, test, dependency, executable, account credential, live endpoint, or model was used. This is a pinned-source review only; installed behavior, current vendor model metadata, authentication routing, account entitlement, pricing, and live compatibility remain unverified.

## GitHub Copilot providers

Reviewed 2026-11-14 against the catalog-pinned `NousResearch/hermes-labs-plugins` commit [`346cfe972f72b6a966e84c1004cf8742554b193d`](https://github.com/NousResearch/hermes-labs-plugins/tree/346cfe972f72b6a966e84c1004cf8742554b193d/github-copilot), subdirectory `github-copilot`. The catalog manifest SHA-256 is `f42aa525e9e290c3de293baac29d081df1dd3f5f170962f1eec59a43fb8164f8`. The exact checkout is preserved at `../../.acceptance/github-copilot-source-346cfe972f72/`; it was read statically only.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `plugin.yaml` | `90232abebfe858bf0d7870387168c9a6333ee1508ac25354a91096bc2aecff5e` | Declares the MIT `github-copilot` model-provider plugin, version 1.0.0. |
| `__init__.py` | `d3035a5640a5d9cf384a18b303fdd7784a4e248bb158b66de966704ddc517b0f` | Registers two providers only when the host Hermes core no longer bundles Copilot and exposes the generic provider seams this plugin needs; otherwise it stays inert. |
| `copilot/activation.py` | `630c457f687248fcf7ab7ee05927a0071bbce618b3053363286351fecb77e461` | Gates GitHub token exchange/catalog calls on explicit Copilot configuration, a Copilot-specific token, a Copilot auth-pool row, or a user-started flow. `GH_TOKEN`/`GITHUB_TOKEN` alone do not opt the user in. |
| `copilot/credentials.py` | `d38b10d4ca26a686548d060a87c9ead26936e592fd88f30a3d8674145f8cd13e` | Resolves secrets through Hermes, performs GitHub device-code and short-lived Copilot-token exchange, refreshes credentials, and persists an exchanged token cache in Hermes home with mode 0600. |
| `copilot/catalog.py` | `7869a1678e506aa73996b0cff27a0915745685af3b18d1e12bab6222c4ca0381` | Queries account model metadata from GitHub Copilot and filters advertised endpoints/capabilities; caches account-specific results. |
| `copilot/setup.py` | `1a2248f42c048b7cb1e8338d662f0e0ede1bc6a65e9b2f0af444a7d692bf2d27` | Provides device-code/manual-token setup, account selection, model picker and model-selection persistence through Hermes setup APIs. |
| `copilot/wire.py` | `af9c82ec088a551038062368fbd4849f8fc2151bca71312d32a6d1d26982396e` | Selects Copilot wire modes and editor/request headers based on model and hostname. |
| `copilot/responses.py` | `f042ccc809603da8c94a4a081eba21ea5b2a8fae0cd1edb2774724346d8567be` | Declares reasoning and message-carrier behavior for the Responses route and replays opaque reasoning carriers only to Copilot hosts. |
| `copilot/helpers.py` | `5deed06bc3d9d1018ff47cbf33ead48f84394efa2573c2ff36af20d5a6972244` | Parses hosts by hostname boundary and removes bearer authorization on cross-origin catalog redirects. |
| `copilot_acp/__init__.py` | `09648689e90acdca1d79108e2db4d3c41cd642127beeaf11e3be618f5ade404e` | Declares a separate `copilot-acp` external-process provider. Catalog lookup starts a short-lived ACP session and obtains enabled model IDs from the installed CLI. |
| `copilot_acp/client.py` | `335ee1365e4051ca4788c566758acb208bd27407fdf0970a7145573154dbb0fc` | Adapts Copilot-specific errors and model metadata around Hermes' generic ACP subprocess client; command and arguments may be overridden through environment variables. |
| `copilot_acp/catalog.py` | `77fc215ac5496766b8dfc3e9d4e34bd3e922df7afa415f14a655505d3ceb1f18` | Memoizes successful and failed ACP model-session probes for five minutes and thirty seconds, respectively. |
| `copilot_acp/setup.py` | `9972d25e1d9da5c943374542930446bb43c71adac349cb3fa0cb8ead779bfcc9` | Describes the external CLI command, auth ownership and model-selection setup flow. |
| `README.md` | `61a05108b3181b5f71276ded7d9c34ee51fa9a4350f47f8e4467a1d51181ebf6` | Discloses the two providers, GitHub/Copilot endpoints, credentials and data paths, account attribution headers, and that ACP delegates to the installed Copilot CLI. These are maintainer declarations, not independent runtime evidence. |
| `LICENSE` | `a23b5098f8cf7d1570221373b7a9e5385920f1caa14f6f1e045eb7cb81053ed5` | MIT license. This applies to this candidate only. |
| `tests/test_activation_gate.py` | `754b3c4b82cf34e4a7ceb963ace16c5f8ddc112538201f27ccca8912d69e0181` | Records calls to assert shared GitHub tokens alone do not trigger Copilot traffic. |
| `tests/test_rule11_no_cli_credentials.py` | `f5fbe4db5dcabd927f38b760c3d29a4660352090b41c62c4dcaf01053c4d540e` | Asserts the plugin does not read `gh`/Copilot CLI token files or spawn `gh`. |
| `tests/test_copilot_token_exchange.py` | `5636c8c367076bcfbc6ed90a9da49aeba1a9effe8200ce0547a3318755339432` | Mocks HTTP and time for token exchange, cache, retry, and error handling. |
| `tests/test_copilot_acp_client.py` | `65dd34c6fc32aa3d563b93b7a55d11b8d06d3b49bdb2e979bd22d4859b76a148` | Uses fake ACP protocol responses and checks local file-operation boundaries in the client seam. |

## Behavior and trust boundaries

- The candidate is two distinct routes. `copilot` sends inference to GitHub Copilot or the account-specific plan host; `copilot-acp` starts the user's installed `copilot --acp --stdio` CLI and delegates the conversation through ACP. Neither route is local inference.
- For `copilot`, device-code login obtains a GitHub token. Hermes-managed env/auth-pool credentials are exchanged at `api.github.com` for a short-lived Copilot token; model discovery and inference send account data to GitHub Copilot endpoints. The token exchange result and account API base URL are persisted in `$HERMES_HOME/.copilot_jwt.json` with a requested `0600` mode. Windows ACL behavior, cache removal and backup/export handling were not tested here.
- GitHub's shared `GH_TOKEN` or `GITHUB_TOKEN` is not sufficient by itself to authorize background GitHub contact. The reviewed activation code first requires a Copilot selection, a Copilot-specific setting or an explicit Copilot flow. This is a useful opt-in pattern for X.A.A. external providers; it does not prove that every host call site obeys the gate.
- `COPILOT_API_BASE_URL` can override the provider endpoint, and enterprise endpoint information may come from the exchange response/token. The chosen endpoint receives inference requests and the exchanged credential. X.A.A. would need explicit endpoint display, origin validation and credential separation before offering this path.
- The ACP profile deliberately does not read the Copilot CLI's private token files; the CLI owns its login. However, the CLI is an external executable selected by `PATH` or override variables, can use its own account settings, and receives conversation content. A source review cannot establish which tools, files or network requests a particular installed CLI build will access.
- The README's provider behavior, supported model catalog, billing attribution, eligibility, and compatibility are upstream claims. Test sources use mocks/fixtures for token exchange, activation, and ACP behavior; they do not establish current GitHub policy, subscription entitlement, charge behavior, current endpoint compatibility, or clean-machine Windows credential storage.

## X.A.A. disposition and limits

The candidate remains `planned`, Phase 9. This review classifies four of the 90 `models` category candidates (Abstract, Claude, Groq, and GitHub Copilot); 86 remain pending classification/source review. It does not add OAuth/device login, GitHub token exchange, an account pool, remote Copilot routing, or external-process ACP to X.A.A. Those would require owner-selected remote-account consent, OS-protected secret storage, strict URL/origin and credential routing, account/cost notices, process allowlisting, complete cancellation and cleanup, capability-aware model discovery, model-free fixture acceptance, and the final live-provider acceptance after the requested LLM-test deferral.

No plugin, test, CLI, credential, account, live endpoint, or model was used. This is pinned-source evidence only; provider terms, current GitHub account/policy eligibility, authentication outcomes, token-cache access controls, installed CLI behavior, billing, and runtime compatibility remain unverified.

# Pinned optional model-provider source audit

This document records source-only reviews of selected optional `models`-category candidates at their individual catalog-pinned commits. The catalog itself is pinned to Hermes core commit `1e208b280deb18f01d015b0acdf3ef3552d08039`. These reviews do not implement or validate providers in X.A.A.; live provider and model testing remains deferred to the final LLM-testing phase.

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

## Ace Data Cloud provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`568763ee21fe081562bb9bcd91e7323f8f81d6ff`](https://github.com/AceDataCloud/AceDataCloudModelProviderHermes/tree/568763ee21fe081562bb9bcd91e7323f8f81d6ff). The Hermes catalog manifest is `plugin-catalog/acedatacloud.yaml`, SHA-256 `06f20708526e28850e1a2d7d3f528fa4db55b572e77a56c729d12f49866ddecf`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/AceDataCloud/AceDataCloudModelProviderHermes/blob/568763ee21fe081562bb9bcd91e7323f8f81d6ff/plugin.yaml) | `d14bd61f2e4b294892d559b8e755720aa4e78e3ce498b0e8f1afa4fc4d725b67` | Declares model-provider plugin version 1.0.2, Hermes `>=0.21.5`, and `ACEDATACLOUD_API_KEY`. |
| [`__init__.py`](https://github.com/AceDataCloud/AceDataCloudModelProviderHermes/blob/568763ee21fe081562bb9bcd91e7323f8f81d6ff/__init__.py) | `c046b86e0c6838fd1630c06ea256bb1f1ade007f679fc6f3b0c5f36ce6f0724a` | Registers one OpenAI-compatible Chat Completions provider at `https://api.acedata.cloud/openai`; advertises only `gpt-4.1-mini` and `gpt-4.1`, both with tool support, and filters discovered IDs against that fixed allowlist. |
| [`README.md`](https://github.com/AceDataCloud/AceDataCloudModelProviderHermes/blob/568763ee21fe081562bb9bcd91e7323f8f81d6ff/README.md) | `0149ee4fe697e79c017c84ef057cfa5d4cf54ff4014fe5b254dfb23b213ce4af` | Describes remote account/API-key setup and separate media products. Those media tools are not registered by this provider. Runtime, pricing, and account behavior remain upstream claims. |
| [`LICENSE`](https://github.com/AceDataCloud/AceDataCloudModelProviderHermes/blob/568763ee21fe081562bb9bcd91e7323f8f81d6ff/LICENSE) | `72d30730d068e067be58878959bac2450f5a05f2b2e3c365cdc13a0181635c3c` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. Its Chat Completions transport is structurally closer to X.A.A.'s current adapter than the Abstract Responses API plugin, but live compatibility and account billing are unverified. Any future adapter still needs explicit remote-data and cost disclosure, protected credential storage, bounded HTTP behavior, cancellation/retry semantics, deterministic fixtures, and live-provider acceptance after the requested LLM-testing deferral. No code was installed or executed and no credential or inference request was used.

## AIHubMix provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`b55e916362ebd0528c5d8981d12f3b8f7268b847`](https://github.com/AIhubmix/hermes-provider-aihubmix/tree/b55e916362ebd0528c5d8981d12f3b8f7268b847), subdirectory `aihubmix`. The Hermes catalog manifest is `plugin-catalog/aihubmix.yaml`, SHA-256 `58f9cf5eaf14f1f415298295653200cc0317ee1dab3957561047d73af1b191d3`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/AIhubmix/hermes-provider-aihubmix/blob/b55e916362ebd0528c5d8981d12f3b8f7268b847/aihubmix/plugin.yaml) | `5adb45872c81435e8928df1b9cddeb3c5981750dfa74567e96e17299672d75ae` | Declares model-provider plugin version 0.1.0. |
| [`__init__.py`](https://github.com/AIhubmix/hermes-provider-aihubmix/blob/b55e916362ebd0528c5d8981d12f3b8f7268b847/aihubmix/__init__.py) | `d3d6ffaa66bd354fc05c35aef19bcc25041dc0eeeacd0e514d065febc21c37c7` | Registers an OpenAI-compatible Chat Completions provider with `AIHUBMIX_API_KEY` and optional `AIHUBMIX_BASE_URL`. It fetches a remote catalog, filters models advertising tool-calling support, falls back to `/v1/models`, then uses a curated static list if discovery fails. It declares vision support for relayed multimodal requests. |
| [`README.md`](https://github.com/AIhubmix/hermes-provider-aihubmix/blob/b55e916362ebd0528c5d8981d12f3b8f7268b847/README.md) | `b4ae62a707b0cbffde18b678e06891a3e21c371dab88e1f079d87d48f7ba73d6` | Describes an external aggregator account/API token and broad model catalog; the service relays requests to upstream model vendors. Model selection, tool/vision capability metadata, and compatibility are service declarations, not independently verified behavior. |
| [`LICENSE`](https://github.com/AIhubmix/hermes-provider-aihubmix/blob/b55e916362ebd0528c5d8981d12f3b8f7268b847/LICENSE) | `7852a493e56bd4bc95cee4388ce7f9d162fa67385057c79e4243cd8222c70aaa` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. Dynamic model discovery and vision are materially different from X.A.A.'s current fixed local model discovery and local-first policy; adoption would require explicit per-run cloud routing and disclosure, provider-specific credential protection, capability checks against the selected upstream route, bounded discovery/inference behavior, and deterministic protocol fixtures before any final live acceptance. No plugin was installed or run and no key, account, or inference service was used.

## AI Stupid Level provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`ba908770f2e97b0a85cc1f5a83240cbe94c8dfa9`](https://github.com/StudioPlatforms/aistupidlevel-sdk/tree/ba908770f2e97b0a85cc1f5a83240cbe94c8dfa9), subdirectory `integrations/hermes/aistupidlevel`. The Hermes catalog manifest is `plugin-catalog/aistupidlevel.yaml`, SHA-256 `ef7fe9d718c50e27078735aae648688230f4d02a9a6e80fe6cf2f12e90d01c65`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/StudioPlatforms/aistupidlevel-sdk/blob/ba908770f2e97b0a85cc1f5a83240cbe94c8dfa9/integrations/hermes/aistupidlevel/plugin.yaml) | `59238c9a232a57ba0847c169afaa7aed06a3aefd3030758f6ec94e3a7db54180` | Declares model-provider plugin version 0.1.0. |
| [`__init__.py`](https://github.com/StudioPlatforms/aistupidlevel-sdk/blob/ba908770f2e97b0a85cc1f5a83240cbe94c8dfa9/integrations/hermes/aistupidlevel/__init__.py) | `4ebf33f3232625a159a53e25b240f4838e97012954d63adca2d911bd94a5266b` | Registers a Chat Completions router at `https://aistupidlevel.info/v1`, using `ASL_ROUTER_KEY` and optional `ASL_BASE_URL`. Live model IDs are discovered from the authenticated model endpoint; fallback IDs are routing modes. The profile declares text-only input. |
| [`README.md`](https://github.com/StudioPlatforms/aistupidlevel-sdk/blob/ba908770f2e97b0a85cc1f5a83240cbe94c8dfa9/integrations/hermes/aistupidlevel/README.md) | `26cb7a56d022f0f9df39edd2db6b932651367444e3a780d971f8eaa5272f1aa0` | Describes benchmark-based routing across user-configured vendor keys, with prompts/tool definitions/results and router key sent to AI Stupid Level; it says model charges remain on the user's vendor accounts and prompts are not stored unless logging is enabled. These are upstream claims, not independently verified. |
| [`LICENSE`](https://github.com/StudioPlatforms/aistupidlevel-sdk/blob/ba908770f2e97b0a85cc1f5a83240cbe94c8dfa9/LICENSE) | `6c2d03f817fd3f260a3e047c7e0401bab74cf65461f7d1a9ac44a6835d2140e5` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. Its routing modes can change the underlying remote model while sharing user-supplied vendor keys, a routing/account behavior X.A.A. does not currently model. A future adapter would need explicit cloud routing, vendor-key handling and usage disclosure, provider-specific secret boundaries, endpoint validation, bounded requests and cancellation, deterministic fixtures, and final live acceptance after the requested LLM-testing deferral. No plugin was installed or executed and no router key, vendor key, account, or inference request was used.

## AnyAPI provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`e5b7b985b5aa56bc585ef1924858978696eb0c3a`](https://github.com/es697/hermes-provider-anyapi/tree/e5b7b985b5aa56bc585ef1924858978696eb0c3a), subdirectory `anyapi`. The Hermes catalog manifest is `plugin-catalog/anyapi.yaml`, SHA-256 `f6a8c58519fb32495b9eef58d1cce2d1d663837b3e79566cab2d8b9a8d6e56bb`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/es697/hermes-provider-anyapi/blob/e5b7b985b5aa56bc585ef1924858978696eb0c3a/anyapi/plugin.yaml) | `3d2c96f3d82ed5d2bbd04b85091817c9a3b00413b73f69ac7a383d01f2c5bd29` | Declares model-provider plugin version 0.1.0. |
| [`__init__.py`](https://github.com/es697/hermes-provider-anyapi/blob/e5b7b985b5aa56bc585ef1924858978696eb0c3a/anyapi/__init__.py) | `95a800e93f17eceddf0b0abbb0168cca6a3ef16647b3e536e1e36830c4e4f115` | Registers an OpenAI-compatible Chat Completions provider at `https://api.anyapi.ai/v1`, using `ANYAPI_API_KEY` and optional `ANYAPI_BASE_URL`. Catalog discovery filters routes tagged for tool calling; on unavailable/empty filtered results it falls back to the generic model list. The profile declares vision support for relayed multimodal requests. |
| [`README.md`](https://github.com/es697/hermes-provider-anyapi/blob/e5b7b985b5aa56bc585ef1924858978696eb0c3a/README.md) | `5a218662fc1a1987359dc0226659026cd3a0f4b2dcaaac21a51537d7d6e53763` | Describes an external aggregator account, provider-prefixed dynamic model IDs, network model discovery, and that the plugin itself reads/writes no files or launches commands. Service routing, privacy, billing, and endpoint behavior are not independently verified. |
| [`LICENSE`](https://github.com/es697/hermes-provider-anyapi/blob/e5b7b985b5aa56bc585ef1924858978696eb0c3a/LICENSE) | `c6ae79419976301d72ee638e5685a04fae27083f3704162a9a8c6b616190d18a` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. Its dynamic cloud model catalog and declared vision support would need explicit per-run remote-data disclosure, route-specific capability checks, protected credentials, endpoint/origin validation, bounded discovery and inference, deterministic fixtures, and final live-provider acceptance after the requested LLM-testing deferral. No plugin was installed or executed and no credential, account, or inference request was used.

## AnyRouter provider

Reviewed 2026-11-15 at catalog-pinned commit [`86bc17dd61ade9baa6f2fb7d440979b55359a907`](https://github.com/duyet/hermes-provider-anyrouter/tree/86bc17dd61ade9baa6f2fb7d440979b55359a907), subdirectory `anyrouter`. Catalog manifest SHA-256 `d5acce82b5be5d056f466bf4f24fe1b5cb90d3038f7c88df41ecf654f66c6434`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `anyrouter/plugin.yaml` | `edb187c5b6722a6a83a59674389e4301778f517bd994b58561421bed7fe3b75b` | Declares an OpenAI-compatible model-provider. |
| `anyrouter/__init__.py` | `2afe1be0ff6d2930dd9defb93fb13773a13fda01452529966a37234c18159277` | Uses `ANYROUTER_API_KEY`, default `https://anyrouter.dev/api/v1`, optional custom base URL, and opt-in app-attribution headers. It filters public catalog entries to those advertising tool calls, keeps a process-global model cache, and forwards session IDs, provider preferences and reasoning in `extra_body`. A custom base URL bypasses AnyRouter-specific catalog interpretation. |
| `README.md` | `d2ae4a6a543e23f243eb671298efbaa1e312d067d9b8070e5a53f74bda065db0` | Describes meta-model routing, upstream model selection, sticky sessions, credits and optional attribution. These are service and maintainer claims, not independently tested here. |
| `LICENSE` | `ad1e4905f4c34fbfe2439aa3c2171ae258ef4846abc0aa131fccf5c0d7e52451` | MIT license for this candidate source. |

This candidate remains planned for Phase 9. Prompts, tool calls, the API key and routing preferences go to the configured gateway, which may route across upstream providers. X.A.A. would need explicit destination/cost disclosure, credential-origin binding, safe catalog fetching and cache behavior tied to endpoint/account settings. No API key, catalog request or inference was used.

## Adaptive Inference routing middleware

Reviewed 2026-11-15 at the catalog-pinned repository commit [`d6f4e201ba28602189872746922e527c58ed0cbd`](https://github.com/midhatn/adaptive-inference/tree/d6f4e201ba28602189872746922e527c58ed0cbd), subdirectory `plugins/hermes`. The Hermes catalog manifest is `plugin-catalog/adaptive-inference-routing.yaml`, SHA-256 `b665ab2f5c27d361541d35c055cb9c0008cc3e35e5ac61fe38709b79b75d079b`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/midhatn/adaptive-inference/blob/d6f4e201ba28602189872746922e527c58ed0cbd/plugins/hermes/plugin.yaml) | `d2f2dcee2463eb4b02d8a15c1712fc4cbf37c47fe174544c8af282ba9c3a60ea` | Declares opt-in request middleware and lifecycle hooks, no tools, environment variables, Python dependencies, provider registration or credential handling; disabled by default. |
| [`__init__.py`](https://github.com/midhatn/adaptive-inference/blob/d6f4e201ba28602189872746922e527c58ed0cbd/plugins/hermes/__init__.py) | `2a65d81edb39bb13623081ae761ab9bd086d43e6db3717bcc9b9bc3c1fbc05aa` | Tracks up to 2,048 host-announced child session aliases in memory. For an exactly matched configured endpoint and Chat Completions request, it may add `X-Adaptive-Class: background`; it does not change endpoint, prompts, model, tools, credentials, or dispatch. |
| [`README.md`](https://github.com/midhatn/adaptive-inference/blob/d6f4e201ba28602189872746922e527c58ed0cbd/plugins/hermes/README.md) | `4cedc6b9c0e891a94f6868218506ca2a1a50f2b24683fee9fa05b770b0a0f666` | Describes opt-in delegated/cron/dedicated-profile classification through an already configured gateway. Ordinary interactive requests remain unchanged. Cron marking depends on a newer public SDK capability; auxiliary calls require explicit endpoint configuration. |
| [`LICENSE`](https://github.com/midhatn/adaptive-inference/blob/d6f4e201ba28602189872746922e527c58ed0cbd/plugins/hermes/LICENSE) | `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30` | Apache-2.0 license for the candidate plugin source. |

This is a model-category scheduling middleware candidate, not a provider adapter. It remains planned for Phase 9 and is not implemented. The plugin's middleware itself makes no service call; when enabled, the already configured gateway receives the same request with a classification header, so that existing gateway's queueing and resource behavior govern outcomes. X.A.A. would need an explicit supported background-routing contract and tests against its own run/worker identities before adopting comparable behavior. The upstream tests were not executed and no gateway or inference endpoint was contacted.

## Aixy inference and MCP setup command

Reviewed 2026-11-15 at the catalog-pinned repository commit [`38422435bb065bd6649f9d1288506b57790ab0eb`](https://github.com/aixy-tech/aixy-hermes-plugin/tree/38422435bb065bd6649f9d1288506b57790ab0eb). The Hermes catalog manifest is `plugin-catalog/aixy.yaml`, SHA-256 `505151daf10daaa7f5034081904e4edd46fb7d252d2ec45542c59ccf0ccb0b0c`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/aixy-tech/aixy-hermes-plugin/blob/38422435bb065bd6649f9d1288506b57790ab0eb/plugin.yaml) | `29b1931e42e7c5626ff1d0fca900c1cb33adbf09725630ea8ccb5ea39fae0c3b` | Declares a standalone setup plugin, version 0.1.2, Hermes `>=0.22.5`, with an optional `AIXY_API_KEY` secret. |
| [`__init__.py`](https://github.com/aixy-tech/aixy-hermes-plugin/blob/38422435bb065bd6649f9d1288506b57790ab0eb/__init__.py) | `c9aa00ff980767f10dc65a0e3fa7f02703b777aa98ae2d2113e36ea2a58da846` | Import only registers the explicit `hermes aixy` CLI command; it does not read credentials, alter configuration, or contact a service. |
| [`aixy_setup.py`](https://github.com/aixy-tech/aixy-hermes-plugin/blob/38422435bb065bd6649f9d1288506b57790ab0eb/aixy_setup.py) | `a034ce98582e24235b158b65958076120f16bd6326973c125449e9e3aabe5685` | Interactive setup validates HTTPS/loopback URLs, blocks catalog redirects, caps model-catalog reads at 4 MiB, and saves keys through Hermes' secret writer. It updates the selected Hermes profile only after explicit confirmation and sends no generation request. Optional MCP setup invokes Hermes' own `mcp add` OAuth flow in a child process; it is separately selectable. |
| [`README.md`](https://github.com/aixy-tech/aixy-hermes-plugin/blob/38422435bb065bd6649f9d1288506b57790ab0eb/README.md) | `4f4ccc20a5454a475bc5bb8e560e8dc1e70348b7f2743ddc53b40c4c00098496` | Describes configuration of an external Aixy inference gateway, session-affinity header, and optional OAuth-connected MCP tools; no separate telemetry/background service is declared. |
| [`LICENSE`](https://github.com/aixy-tech/aixy-hermes-plugin/blob/38422435bb065bd6649f9d1288506b57790ab0eb/LICENSE) | `11e962f173d524d439db6a52f27246965e8cab709848081df6468318e384477f` | MIT license for the candidate source. |

This is a setup workflow, not an inference provider implementation or model adapter. It remains planned for Phase 9 and is not implemented. The model-catalog request transmits a project key to the explicitly entered remote endpoint; inference later uses that saved endpoint. The optional MCP flow can perform browser OAuth and write MCP configuration/credentials through Hermes. X.A.A. would need owner-directed endpoint and account consent, OS-protected credentials, clear separation between setup and inference, and its own isolated MCP OAuth/setup lifecycle. No setup command, account, key, OAuth flow, catalog endpoint, or inference request was used here.

## Cloudflare Workers AI provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`f6821d058d504ee6324b0d3217564b93124c09f9`](https://github.com/AhmetArif0/hermes-cloudflare-workers-ai-provider/tree/f6821d058d504ee6324b0d3217564b93124c09f9), subdirectory `cloudflare-workers-ai-provider`. The Hermes catalog manifest is `plugin-catalog/cloudflare-workers-ai-provider.yaml`, SHA-256 `199e4baa94591b74f27ca5c5fef00eb380012d40fd144f1b6e4ec1c9b7653e05`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/AhmetArif0/hermes-cloudflare-workers-ai-provider/blob/f6821d058d504ee6324b0d3217564b93124c09f9/cloudflare-workers-ai-provider/plugin.yaml) | `0b01c4a465b53a06c1fef54dc12f88e0389045dcaf1117f1b64f04bcb6881ab9` | Declares model-provider plugin version 1.0.0, Hermes `>=0.21.5`, and distinct account-scoped base-URL and API-token variables. |
| [`__init__.py`](https://github.com/AhmetArif0/hermes-cloudflare-workers-ai-provider/blob/f6821d058d504ee6324b0d3217564b93124c09f9/cloudflare-workers-ai-provider/__init__.py) | `fd0054e1c251c60cd8c47963f6b32ca38f233d5fc528874bc839268145c25792` | Registers an OpenAI-compatible provider with a fixed 16-model catalog, context and vision/reasoning metadata, model-specific reasoning-effort mapping, and paid-plan error classification. It disables generic live-model and health probes because the source says Cloudflare's compatible `/models` route returns 405. |
| [`README.md`](https://github.com/AhmetArif0/hermes-cloudflare-workers-ai-provider/blob/f6821d058d504ee6324b0d3217564b93124c09f9/cloudflare-workers-ai-provider/README.md) | `5e95eb7197622a7a09d4ccda1444bd301c010dca4c057532b8d52e344775e22a` | Describes account-ID-bound Workers AI endpoints and a Cloudflare token. Live model capabilities, plan eligibility, and paid/free behavior in the README are maintainer-reported observations, not independently verified here. |
| [`LICENSE`](https://github.com/AhmetArif0/hermes-cloudflare-workers-ai-provider/blob/f6821d058d504ee6324b0d3217564b93124c09f9/LICENSE) | `90640ae33c9eb935814e391d0e9b1b2b1035100217faf170d89b84b5dfe1a87b` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. It sends inference to the user's Cloudflare account and has a catalog frozen to the reviewed source revision rather than live provider discovery. X.A.A. would need explicit remote-data/account disclosure, separate credentials and endpoint validation, capability-aware selection, protocol fixtures for reasoning and plan errors, bounded requests, and final live acceptance after the requested LLM-testing deferral. No source was installed or executed and no Cloudflare account, token, or model endpoint was used.

## Chutes provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`d1165cdd5c65a1179c559e9169a428adb9a36b42`](https://github.com/TheStreamCode/hermes-chutes-provider/tree/d1165cdd5c65a1179c559e9169a428adb9a36b42). The Hermes catalog manifest is `plugin-catalog/chutes-provider.yaml`, SHA-256 `67c30fda3e20e3a2a13b61aa670d5ebc59c63f38832f0077ed93cdff79bfc8a6`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/TheStreamCode/hermes-chutes-provider/blob/d1165cdd5c65a1179c559e9169a428adb9a36b42/plugin.yaml) | `653d3da34f8d090ef25a673ecff8b2db664be1ffc821063b10c3ee1008a0376d` | Declares model-provider plugin version 0.1.5 and required secret `CHUTES_API_KEY`. |
| [`__init__.py`](https://github.com/TheStreamCode/hermes-chutes-provider/blob/d1165cdd5c65a1179c559e9169a428adb9a36b42/__init__.py) | `9f254a990ff7d8bf4960c9a20a97d5aec7865115d2be9ad9490e9804e3ca02c4` | Imports the directory provider and registers its profile. |
| [`hermes_chutes_provider/__init__.py`](https://github.com/TheStreamCode/hermes-chutes-provider/blob/d1165cdd5c65a1179c559e9169a428adb9a36b42/hermes_chutes_provider/__init__.py) | `a813f50d40c0f0abb6199fef9f0d7db9aa10bdb9d0483930ff32b9094c55fcf8` | Registers an OpenAI-compatible Chat Completions provider at `https://llm.chutes.ai/v1`, with optional `CHUTES_BASE_URL`. It reads a bounded live catalog, keeps entries advertising tool support, and exposes two routing aliases when discovery is unavailable. |
| [`README.md`](https://github.com/TheStreamCode/hermes-chutes-provider/blob/d1165cdd5c65a1179c559e9169a428adb9a36b42/README.md) | `1fd279446243a4cffb056404f3f3e919010b985c95ce93a2eabeb699d2deba56` | Describes the remote Chutes API, bearer-key setup, live model list and aliases; pricing/account behavior and current catalog compatibility are not independently verified. |
| [`LICENSE`](https://github.com/TheStreamCode/hermes-chutes-provider/blob/d1165cdd5c65a1179c559e9169a428adb9a36b42/LICENSE) | `fea0160be3242f61e4869634f452efde0d966570db83d6ed338d47c664268c21` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. It sends inference and model-discovery traffic to a remote Chutes endpoint; the catalog has dynamic capability filtering with alias fallback rather than a fixed list. X.A.A. would need explicit cloud routing, protected credentials, bounded live discovery, route-capability checks, and deterministic protocol fixtures before final live acceptance after the requested LLM-testing deferral. No plugin, key, account, or endpoint was used.

## CoreWeave provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`799027698ca0171c8912df076649a67a25266c20`](https://github.com/coreweave/serverless-inference-for-hermes/tree/799027698ca0171c8912df076649a67a25266c20). The Hermes catalog manifest is `plugin-catalog/coreweave.yaml`, SHA-256 `44aef05f7e2fe8a19dd950b961bdac760e3687e20e21cf39788c0c8bef3fe5d2`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/coreweave/serverless-inference-for-hermes/blob/799027698ca0171c8912df076649a67a25266c20/plugin.yaml) | `0f606eafcf4cf0c8e5b60984d0557f56a9e039d465af65e2cb41f4da854c65de` | Declares model-provider plugin version 0.1.0; SPDX declares MIT. |
| [`__init__.py`](https://github.com/coreweave/serverless-inference-for-hermes/blob/799027698ca0171c8912df076649a67a25266c20/__init__.py) | `5d197d2ed2a3fca343bb7e668607f5af9f57302832610f9e6461651952a1f9c1` | Registers an OpenAI-compatible Chat Completions profile at `https://api.inference.wandb.ai/v1` using `COREWEAVE_API_KEY`; it adds no transport, catalog fallback, model list, or runtime dependency. |
| [`README.md`](https://github.com/coreweave/serverless-inference-for-hermes/blob/799027698ca0171c8912df076649a67a25266c20/README.md) | `a2c02ee27d88ff66e615853410e52e3c678254355994720927a95a04c4055b87` | Describes Hermes-managed live model discovery and remote prompt/tool-result delivery. Its authenticated live smoke report is maintainer evidence, not X.A.A. verification. |
| [`LICENSES/MIT.txt`](https://github.com/coreweave/serverless-inference-for-hermes/blob/799027698ca0171c8912df076649a67a25266c20/LICENSES/MIT.txt) | `3e42152985770ce2d26eccab29e2da8c1feb87467e60b109972ab9f130c9742f` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. It uses a remote W&B-hosted inference endpoint and per-account model discovery. X.A.A. would need explicit cloud-data and account disclosure, protected credentials, endpoint validation, live model capability handling, bounded transport behavior, and final acceptance after the requested LLM-testing deferral. No plugin, key, account, discovery request, or inference request was used.

## ngrok AI Gateway provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`27baaa0e85e09581f05084fc318a0755774162e1`](https://github.com/ngrok/hermes-ngrok/tree/27baaa0e85e09581f05084fc318a0755774162e1), subdirectory `model-providers/ngrok`. The Hermes catalog manifest is `plugin-catalog/ngrok-ai-gateway.yaml`, SHA-256 `447d50c4a4490de02fea385b538c3b86560aaae8fc3d51918c74417f489efc71`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/ngrok/hermes-ngrok/blob/27baaa0e85e09581f05084fc318a0755774162e1/model-providers/ngrok/plugin.yaml) | `10fd3e8f01b33bd3263a32ea70a88bf44b93f2e7869a9810a02b1345ae5a23cb` | Declares model-provider plugin version 1.0.0, Hermes `>=0.21.6`, and secret `NGROK_AI_GATEWAY_KEY`. |
| [`__init__.py`](https://github.com/ngrok/hermes-ngrok/blob/27baaa0e85e09581f05084fc318a0755774162e1/model-providers/ngrok/__init__.py) | `d8bdf82b298cd59cb9ed9af87f5b182e2c4df532af5cd72420d430d04104ebad` | Registers an OpenAI-compatible gateway profile at `https://gateway.ngrok.ai/v1` with live account-specific model discovery, no static fallback models, a user-agent header, and explicit mapping for selected model-not-found, entitlement, billing, rate-limit, and retry errors. Supports `NGROK_AI_GATEWAY_BASE_URL`. |
| [`README.md`](https://github.com/ngrok/hermes-ngrok/blob/27baaa0e85e09581f05084fc318a0755774162e1/model-providers/ngrok/README.md) | `d7fcbb4d708de0ffbbaeb571722737c2272621177a57f487f63643e4a13a944a` | Discloses that chat and model-list requests, with the access key, go to the gateway; routing/failover uses that account's configuration. |
| [`LICENSE`](https://github.com/ngrok/hermes-ngrok/blob/27baaa0e85e09581f05084fc318a0755774162e1/LICENSE) | `e4f4a8ca6594edda418eccb7d1e0aac152e2ac8c29645fbc8ffab744bbd98ad5` | MIT license for the repository candidate. |

The candidate remains planned for Phase 9 and is not implemented. It exposes an account-configured remote gateway, live model discovery, provider failover, and account-credit states that X.A.A. does not currently model. Adoption would require explicit remote routing and cost disclosure, protected credentials, endpoint-origin restrictions, account-scoped model capabilities, bounded request handling, fixture coverage for failover decisions, and final live acceptance after the requested LLM-testing deferral. No plugin, key, account, catalog request, or inference endpoint was used.

## Together AI provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`d5267d5d1e1bf98097b13546a739bd8dab8db7ec`](https://github.com/pepijndevos/hermes-together-provider/tree/d5267d5d1e1bf98097b13546a739bd8dab8db7ec). The Hermes catalog manifest is `plugin-catalog/together.yaml`, SHA-256 `9df6a82377803a5a1bbe58650e21f76663de145ed77d9beb6228253a46b5ac71`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/pepijndevos/hermes-together-provider/blob/d5267d5d1e1bf98097b13546a739bd8dab8db7ec/plugin.yaml) | `ae2fb6475ff37e39a3f36c50d5aee220dd5de312a40156660739cb7b8432848e` | Declares model-provider plugin version 1.0.0, MIT, and `TOGETHER_API_KEY`. |
| [`__init__.py`](https://github.com/pepijndevos/hermes-together-provider/blob/d5267d5d1e1bf98097b13546a739bd8dab8db7ec/__init__.py) | `8107aa70266b8ff2e91ee9dc64b5daaf58ea4d9fc13782c70122dd817a12c82c` | Registers OpenAI-compatible Chat Completions at `https://api.together.xyz/v1`, with API-key auth, configurable `TOGETHER_BASE_URL`, dynamic model discovery and a five-model fallback list. An override handles Together's bare-list model response for model validation. |
| [`README.md`](https://github.com/pepijndevos/hermes-together-provider/blob/d5267d5d1e1bf98097b13546a739bd8dab8db7ec/README.md) | `3823b5dbaee1fed73807176ea4ec803c61039d3fe8ca08fd22f19d1ed0ae74a3` | Describes remote chat and model-list calls using the provided key; the plugin itself delegates credential resolution to Hermes and adds no separate transport or telemetry. |
| [`LICENSE`](https://github.com/pepijndevos/hermes-together-provider/blob/d5267d5d1e1bf98097b13546a739bd8dab8db7ec/LICENSE) | `78f5e22fe1359085da4dd85de0e79b0e80dd9247c36dcfc83f38738a85f77dda` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. The provider uses remote inference and dynamic model discovery, with a static fallback that can differ from current account availability. X.A.A. would need explicit cloud routing, protected credentials, model capability and availability handling, bounded transport, and deterministic tests before final live acceptance after the requested LLM-testing deferral. No plugin, key, account, catalog request, or model request was used.

## Telnyx provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`f53c20e80f84b4a880a7f2b4f1ed2f8b3fa1d11c`](https://github.com/team-telnyx/hermes-inference-provider/tree/f53c20e80f84b4a880a7f2b4f1ed2f8b3fa1d11c). The Hermes catalog manifest is `plugin-catalog/telnyx-provider.yaml`, SHA-256 `0cef7c1536adf15b5670328ef6aab53e99cbf7cb0d3b3e6f293d706870d40bad`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/team-telnyx/hermes-inference-provider/blob/f53c20e80f84b4a880a7f2b4f1ed2f8b3fa1d11c/plugin.yaml) | `da9d4077dad3751d15113d4029f00bac1274b01a85aa431184b34ce6206eaf5a` | Declares provider plugin version 0.1.0, Hermes `>=0.21.4`, and secret `TELNYX_API_KEY`. |
| [`__init__.py`](https://github.com/team-telnyx/hermes-inference-provider/blob/f53c20e80f84b4a880a7f2b4f1ed2f8b3fa1d11c/__init__.py) | `c176b3b23a4b32342d0597cd93a8e90da7c67d63e519459849100e1cd1395adb` | Registers Chat Completions at `https://api.telnyx.com/v2/ai/openai`. It filters live authenticated catalog rows to text-generation tasks, uses a four-model fallback, and leaves the default output-token cap unset because the source reports a tool-call compatibility error when the cap is combined with tools. |
| [`README.md`](https://github.com/team-telnyx/hermes-inference-provider/blob/f53c20e80f84b4a880a7f2b4f1ed2f8b3fa1d11c/README.md) | `91b6ee8c4f4cb1e690c5c18b0e200be761c9a8d2f3b2f9b4123f1ba22f5c68c9` | Describes authenticated live model discovery, hosted and proxied routes, fallback models, and pricing tagged per million tokens. Provider compatibility and pricing are upstream declarations, not independent verification. |
| [`LICENSE`](https://github.com/team-telnyx/hermes-inference-provider/blob/f53c20e80f84b4a880a7f2b4f1ed2f8b3fa1d11c/LICENSE) | `4ccfec7b1135a4ac670adb72199bfe9de9ca1933d5192b63b62e9fa91089eabf` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. It combines remote hosted and proxied model routes with live pricing/model metadata, and the source documents provider-specific output-cap/tool-call constraints. X.A.A. would need explicit remote routing and cost disclosure, protected credentials, safe catalog pricing normalization, model/route capability checks, and deterministic coverage of those request constraints before final live acceptance after the requested LLM-testing deferral. No plugin, key, account, catalog request, or inference endpoint was used.

## Runcrate provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`c0023f37820e05147f1dcd1cb8ebd712e6b122e2`](https://github.com/runcrateai/hermes-runcrate/tree/c0023f37820e05147f1dcd1cb8ebd712e6b122e2), subdirectory `model-providers/runcrate`. The Hermes catalog manifest is `plugin-catalog/runcrate-provider.yaml`, SHA-256 `95fb0eb886b516351a770ac84eac1c1c91821cbf81e114990c303e74683cfe1d`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/runcrateai/hermes-runcrate/blob/c0023f37820e05147f1dcd1cb8ebd712e6b122e2/model-providers/runcrate/plugin.yaml) | `e48764d3addcd0c68f84d86133c971cf845f9e4de257010b776f2052e4a56601` | Declares model-provider plugin version 1.0.0, Hermes `>=0.21.5`, and secret `RUNCRATE_API_KEY`. |
| [`__init__.py`](https://github.com/runcrateai/hermes-runcrate/blob/c0023f37820e05147f1dcd1cb8ebd712e6b122e2/model-providers/runcrate/__init__.py) | `07697b676abb0ff723aa63c883d36ef7b021186eec6ea8806eb150abf81890ae` | Registers OpenAI-compatible Chat Completions at `https://api.runcrate.ai/v1`, dynamic model listing, six fallback IDs, optional `RUNCRATE_BASE_URL`, and a reasoning-effort mapping. |
| [`README.md`](https://github.com/runcrateai/hermes-runcrate/blob/c0023f37820e05147f1dcd1cb8ebd712e6b122e2/README.md) | `2e0ac1718b2aa3569791feaa1990c3bc94ece962f7ccd48fac069a712836ebb3` | Describes account API-key setup and says free requests may be served by third-party model providers that handle prompts and outputs under their own terms; other catalog models are billed from a prepaid balance. These are upstream service declarations. |
| [`LICENSE`](https://github.com/runcrateai/hermes-runcrate/blob/c0023f37820e05147f1dcd1cb8ebd712e6b122e2/LICENSE) | `1660e92e87c38f49859e3cfc81fd69db6d22cb88a6abb2e420b319ea3874c082` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. In addition to explicit cloud routing and cost disclosure, any future integration must clearly disclose possible third-party inference routing, protect credentials, check current model capability/availability, bound transport behavior, and test reasoning parameter handling before final live acceptance after the requested LLM-testing deferral. No plugin, key, account, catalog request, or inference call was used.

## RuAPI dual-protocol provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`fbefc615d447729ccf85aec05e0ea27ecc6f3bd9`](https://github.com/RuAPI-ai/hermes-provider-ruapi/tree/fbefc615d447729ccf85aec05e0ea27ecc6f3bd9), subdirectory `ruapi`. The Hermes catalog manifest is `plugin-catalog/ruapi.yaml`, SHA-256 `0658ad3fa949a6957bdcce8431b2c235c3b131a3d76bea51098ef2df24b5861b`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/RuAPI-ai/hermes-provider-ruapi/blob/fbefc615d447729ccf85aec05e0ea27ecc6f3bd9/ruapi/plugin.yaml) | `f3395fd2692f803ec3a955de342a9958d8f1bdda3bb8c3691c66f3b615e1e87e` | Declares two RuAPI model profiles through one MIT plugin, Hermes `>=0.21.5`, and secret `RUAPI_API_KEY`. |
| [`__init__.py`](https://github.com/RuAPI-ai/hermes-provider-ruapi/blob/fbefc615d447729ccf85aec05e0ea27ecc6f3bd9/ruapi/__init__.py) | `4208f09792f1155a89a0206ece1031012660895dc66ab02d11342dc80ac65a9b` | Registers Claude models over native Anthropic Messages and other chat models over OpenAI-compatible Chat Completions. Hermes fetches one authenticated model list; the plugin filters it into provider-specific lists and declares selected model context/vision capabilities. |
| [`README.md`](https://github.com/RuAPI-ai/hermes-provider-ruapi/blob/fbefc615d447729ccf85aec05e0ea27ecc6f3bd9/ruapi/README.md) | `031281788593f105aa302a66879e1dfde63b04a946e75fb0a014edbd207af294` | Describes pay-as-you-go credit top-ups using USDT, remote account model availability and pricing, and both API protocols. These are service declarations, not account or billing verification. |
| [`LICENSE`](https://github.com/RuAPI-ai/hermes-provider-ruapi/blob/fbefc615d447729ccf85aec05e0ea27ecc6f3bd9/LICENSE) | `73fa2ea70da7595d5f3d8f8e6f53b7a07062fd7b4e4f58550a75abe03e62f658` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. It needs both Anthropic Messages and OpenAI Chat Completions transport semantics, plus one account key and remote billing/data disclosure. X.A.A.'s current provider surface would need protocol-specific adapters, capability checks, credential isolation, cost disclosure, bounded request behavior, deterministic fixtures, and final live acceptance after the requested LLM-testing deferral. No account, key, model list request or inference request was used.

## Safe AI Labs provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`3fa82ed3b8fc2aa52e96afeabdfbe19fa6a043fc`](https://github.com/nevertrust-ai/hermes-safeailabs-provider/tree/3fa82ed3b8fc2aa52e96afeabdfbe19fa6a043fc). The Hermes catalog manifest is `plugin-catalog/safeailabs-provider.yaml`, SHA-256 `fb6f31d44a9c6a07f48fd261cd7f3b448a294df3f8d31326f38e8d691629dc17`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/nevertrust-ai/hermes-safeailabs-provider/blob/3fa82ed3b8fc2aa52e96afeabdfbe19fa6a043fc/plugin.yaml) | `61a086ae9013edbf0cde99732081d1e946b8192eb0986f628e280d721f153a20` | Declares model-provider version 1.0.0, Hermes `>=0.22.0`, and secret `SAFE_AI_KEY`. |
| [`__init__.py`](https://github.com/nevertrust-ai/hermes-safeailabs-provider/blob/3fa82ed3b8fc2aa52e96afeabdfbe19fa6a043fc/__init__.py) | `c7c1a0830812891e29e210219eb2a5b61ef9bb7775b1ef76e8b66822de975216` | Registers one region-configurable OpenAI-compatible provider, with a single model ID and declared 1,048,576-token context plus tool, reasoning and vision flags; it declares no output token cap. |
| [`README.md`](https://github.com/nevertrust-ai/hermes-safeailabs-provider/blob/3fa82ed3b8fc2aa52e96afeabdfbe19fa6a043fc/README.md) | `656a1aa4357e7abd01160776529caec330f197395e7fc16a06ae6f6d9f82f2ce` | Describes regional endpoints, remote key/model-list/chat requests, and warns that plan-based token-rate delays can exceed Hermes' 180-second first-token timeout for very large prompts. These model limits and plan timings are upstream claims. |
| [`LICENSE`](https://github.com/nevertrust-ai/hermes-safeailabs-provider/blob/3fa82ed3b8fc2aa52e96afeabdfbe19fa6a043fc/LICENSE) | `41358d9727e029505a2ebbf1960471897c44d91d7f29e73169f316ca4864e3d7` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. Its single model, regional endpoint choices, declared million-token context, remote account key, and documented long time-to-first-token behavior all need explicit validation in X.A.A. Capability declarations and streaming timeout handling must be checked against the selected plan before live acceptance after the requested LLM-testing deferral. No plugin, key, account, catalog request, or model request was used.

## Fortytwo provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`91c8f521ab357530c91a9660bd8f2db37571c1c8`](https://github.com/Fortytwo-Network/fortytwo-hermes/tree/91c8f521ab357530c91a9660bd8f2db37571c1c8). The Hermes catalog manifest is `plugin-catalog/fortytwo-provider.yaml`, SHA-256 `0421114592af7dc84fb3b1f75fd75dab2caa5133acd2c054761c37df2638ab77`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/Fortytwo-Network/fortytwo-hermes/blob/91c8f521ab357530c91a9660bd8f2db37571c1c8/plugin.yaml) | `6490c7a51b6471017d0b8aeb1dde934d631e183204165980d4956bf5c2aa450a` | Declares model-provider version 1.0.0, Hermes `>=0.22.5`, and `FORTYTWO_API_KEY`. |
| [`__init__.py`](https://github.com/Fortytwo-Network/fortytwo-hermes/blob/91c8f521ab357530c91a9660bd8f2db37571c1c8/__init__.py) | `f007748ec8df832eb6237d42fff4fadb63cd8b37ff695b89f7c280a2d6e4b50d` | Registers a single OpenAI-compatible model at `https://api.fortytwo.network/v1`; declares streaming, tool, vision, reasoning and 262,144-token context support, and maps reasoning effort to the provider's four accepted values. Adds `HTTP-Referer` and `X-Title` attribution headers. |
| [`README.md`](https://github.com/Fortytwo-Network/fortytwo-hermes/blob/91c8f521ab357530c91a9660bd8f2db37571c1c8/README.md) | `6c0dda41e562bac8622e0a987a0504ba6a672b24b834b95b5b22aebbe34fb62f` | Discloses that prompts, images and tool results go to the remote API with the configured key. It declares no separate telemetry endpoint, while backend attribution reporting remains unresolved upstream. |
| [`LICENSE`](https://github.com/Fortytwo-Network/fortytwo-hermes/blob/91c8f521ab357530c91a9660bd8f2db37571c1c8/LICENSE) | `7c4271aa74d663f6af3923cd28d767b518a7657e4ba5e6132de956a8821ce175` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. It is a remote, single-model route with declared vision and reasoning capabilities and attribution headers. X.A.A. would need explicit cloud/data consent, protected credentials, capability validation, model-specific reasoning support, and clear handling of request attribution before final live acceptance after the requested LLM-testing deferral. No plugin, key, account, or inference endpoint was used.

## Requesty managed gateway provider

Reviewed 2026-11-15 at the catalog-pinned repository commit [`aa4e37d7f09dfc1e98b915d740d09e8b1b44c98d`](https://github.com/requestyai/hermes-provider-requesty/tree/aa4e37d7f09dfc1e98b915d740d09e8b1b44c98d), subdirectory `requesty`. The Hermes catalog manifest is `plugin-catalog/requesty.yaml`, SHA-256 `353e6754d2a012ace10623cbf72990faa515bcba43e886cb5ab115242bcbf009`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| [`plugin.yaml`](https://github.com/requestyai/hermes-provider-requesty/blob/aa4e37d7f09dfc1e98b915d740d09e8b1b44c98d/requesty/plugin.yaml) | `ab3841f1570ffd849dcf6aeaace3244a9d11443e6e16f4a1c0a67aa09b9a5124` | Declares model-provider version 0.1.0, Hermes `>=0.21.6`, and secret `REQUESTY_API_KEY`. |
| [`__init__.py`](https://github.com/requestyai/hermes-provider-requesty/blob/aa4e37d7f09dfc1e98b915d740d09e8b1b44c98d/requesty/__init__.py) | `bd4230ef412e35e404361c8854caba67d2fc549b99d88bd86cb03c50263dfa39` | Registers OpenAI-compatible Chat Completions through Requesty's managed gateway. It reads two public catalogs without credentials, caches policy/model metadata, warms the cache on a daemon thread, maps reasoning settings, adds the Hermes session ID as `requesty.trace_id`, and estimates cost from catalog pricing. It supports a base-URL override and declares vision. |
| [`README.md`](https://github.com/requestyai/hermes-provider-requesty/blob/aa4e37d7f09dfc1e98b915d740d09e8b1b44c98d/README.md) | `e00e84a0819789f9c61a07f80501b333780d767e2a34d2d2078e3e5f508ff1e1` | Discloses that prompts, tool calls, the API key and session trace ID go to the selected Requesty endpoint; catalog requests are public and unauthenticated. Managed routes may include regional routing and failover, according to the provider's service docs. |
| [`LICENSE`](https://github.com/requestyai/hermes-provider-requesty/blob/aa4e37d7f09dfc1e98b915d740d09e8b1b44c98d/LICENSE) | `34b714518901a68355338cbc77a32c0ff1a4c96489a78389c1a4759fbf6c6409` | MIT license for the candidate source. |

The candidate remains planned for Phase 9 and is not implemented. It adds server-side managed routing/failover, catalog-based cost estimates, a background catalog-refresh thread, and deliberate session tracing, all against a remote gateway. X.A.A. would need explicit routing/region and session-data consent, protected credentials, cost estimates clearly separated from billed charges, bounded discovery and lifecycle, and deterministic routing fixtures before final live acceptance after the requested LLM-testing deferral. No plugin, key, account, catalog request, or inference request was used.

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

The catalog candidate remains planned for Phase 9 and is not implemented. This document records selected pinned-source behavior only; it does not establish that all catalog entries are model providers or prove runtime compatibility. LM Studio remains X.A.A.'s default provider. External adapters need explicit remote-data and account-usage notices, owner-selected account/profile, a credential boundary, model capability and health checks, cancellation, retry/idempotency behavior, model-free fixtures, and live acceptance in the final LLM-testing phase. The candidate's permissive proxy fallback must not be copied.

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

## Antigravity subscription DirectSDK provider

Reviewed 2026-11-15 at catalog-pinned commit [`0d5a3160f69f0c3302e3f49d9d2820093a544116`](https://github.com/soyelmismo/hermes-antigravity-subscription/tree/0d5a3160f69f0c3302e3f49d9d2820093a544116). Catalog manifest SHA-256 `039d6ab073f78aeb21d0432cae11e4b4d4a6255fe3e9a04ba1b2896492ab16ce`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `plugin.yaml` | `6ffeeb5007b091f002200b32d372b86a74810e20906d8f604c8501711f2045c1` | Declares a model-provider for Linux, macOS and Windows, requiring Hermes >=0.21.4 and an external `agy` binary. |
| `__init__.py` | `999b495fa87bc5361ec6882edd2ba85998b412156b7061a0d310dce1450612e5` | Registers account-discovered models, reasoning-effort mapping, quota usage reporting, and error classification for context exhaustion, billing caps and account blocks. It opts into streaming only when the installed Hermes profile API supports it. |
| `client.py` | `a34a8eed2a6d25ec18c517f34263685b2351bcf666dd71b4f72319d05780d8e9` | Runs the external CLI in stream-json mode, keeps persistent multi-turn agy conversations, parses textual `<tool_call>` markup into Hermes tool calls, and bounds idle workers. Its docs describe Windows process-tree termination and best-effort cleanup of a temporary HOME. |
| `process.py` | `ab7a8ada7c569f1402733f962dbf468115edc8bc3093e2c56da65e94d2c45ad3` | Detects agy credentials and builds isolated child state; it may symlink, hardlink or copy token material into the temporary HOME. Cleanup code documents a possible residual Windows hardlink when the shared token is read-only. |
| `models.py` | `06312b1e0aefedc79c72c36c21f7f01a9e3f666435927f532d8c3fde5738c613` | Discovers the account's live model/effort list with a fallback catalog and one-hour cache. |
| `usage.py` | `2d09f78c2972c7a1d62101736c145570bb2cf1cf746b4f9773f0f03efadeee5a` | Supplies provider-specific subscription usage data for the CLI's usage/status surfaces. This depends on agy output and account behavior. |
| `README.md` | `d91aaad30fe7f15806c9cf30fe4d1f6ebb6fe0fe1eab8ee34bd9cfa127d50f96` | Discloses external account/quota use, model compatibility gaps, the reliance on textual tool-call parsing, and upstream trajectory/context limitations. It states some models may ignore the tool protocol and be terminated. |
| `LICENSE` | `43fd114783c4dab8fcddd3de9ab2ff29680bb2d77bcafbec437f0942a891cbfb` | MIT license for this candidate source. |

This candidate remains planned for Phase 9. The README's Windows support declaration is not independent compatibility evidence. X.A.A. would need explicit account/process consent, a safer token-location strategy, cleanup acceptance including the read-only hardlink case, and model-free tool-protocol verification; live model testing remains deferred. No CLI, plugin, account, token, usage endpoint or inference was used.

## Antigravity OAuth CLI provider

Reviewed 2026-11-15 at catalog-pinned commit [`466fafb9b6e8099735d79ed32f97005a477ba144`](https://github.com/neerazz/hermes-antigravity-oauth/tree/466fafb9b6e8099735d79ed32f97005a477ba144). Catalog manifest SHA-256 `605ceba1b05d55ad6b1431aad1e33a6e912cad86cfbbb38577d813488ce16fe9`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `plugin.yaml` | `921a0c777d6cd52597aec6da75d57059b35455f8a3fbe109a125f900134317c7` | Declares a model-provider for Linux, macOS and Windows; requires Hermes >=0.21 and the separate `agy` CLI. |
| `__init__.py` | `60aede54446a5d88a422cc2c6c320893114d8a10da00df6c8b147e3faea7672e` | Registers OpenAI-shaped chat completions over an external process, account model discovery, reasoning choices, vision metadata, and CLI-owned auth actions. |
| `auth.py` | `2fe771e6a4e6fedcbc85be06f5294d2551d922b86ecd10c87b7e21949d9f17cd` | Starts the official CLI's interactive sign-in in a temporary working directory, verifies with `agy models`, and delegates logout to agy. No OAuth token is stored in Hermes. |
| `process.py` | `b5aad3217580327dda698691102c12c1bfc2a1fc8d9296b7ad1cfcbf83a0007a` | Detects agy credentials through metadata/presence checks and isolates its child HOME. It symlinks or hardlinks the token into that temporary HOME, with `copy2` as a final fallback; the README's blanket claim that the plugin never copies the token conflicts with this fallback. Cleanup is best-effort, so Windows temporary-token residue needs careful acceptance. Windows process-tree cleanup uses `taskkill`. |
| `client.py` | `74013ca6000d71b5d5ed394fbf4d9a1a65b69cfcf92bd7974f947dc92a7c0348` | Runs agy as a child process for streaming, converts tool steps into Hermes calls, and has Windows-specific temporary-directory cleanup logic. The source documents a synchronous non-streaming call that blocks the event loop. |
| `README.md` | `0e6b66b783ad31f66ac3ed08fcf001ad24855a680c8151755fa1febf81fbb3b6` | Discloses CLI-owned account data, a private temporary HOME and explicit optional `npx` installer behavior; it describes credentials as never copied, which disagrees with the fallback in `process.py`. |
| `package.json` | `9df0ffd0ce61fffb405d414e739483d05675018d8b0d57e84213410283fe013e` | Declares a Node >=18 `npx` installer package. |
| `bin/cli.js` | `0bb373a45279a1e496bf7dd601691e376fde8f9fe28a8940f21230e18ad4d7db` | Optional `--yes` downloads the official installer to a temp file and invokes it through PowerShell with `-ExecutionPolicy Bypass`; plugin install and sign-in are separate child CLI calls. |
| `LICENSE` | `ccd98bdeec4e461b83d1c91f6d9e290861f84038ab639fd56e786c5352b14637` | MIT license for this candidate source. |

This candidate remains planned for Phase 9. Although its manifest lists Windows, source review is not live compatibility evidence; X.A.A. would need explicit external-account consent, secret-handling review of the copy fallback, reliable token cleanup, process allowlisting, and Windows acceptance before implementation. No CLI, plugin, account, token, installer or inference was used.

## Antigravity agy CLI provider

Reviewed 2026-11-15 at catalog-pinned repository commit [`f85003d3ce0de3844e2d5c23174d630ff3586e5d`](https://github.com/Realtyxxx/hermes-plugin-antigravity-agy/tree/f85003d3ce0de3844e2d5c23174d630ff3586e5d). Catalog manifest SHA-256 `e300851666b7180e13b1fcd3bcd172d1f68024c3191c610073705a807f4ca15c`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `plugin.yaml` | `a5d6afa5e9ff379912175f645d998e0a4f1d665f938a1939c97341af9109cf31` | Declares a model-provider with no environment credentials, but requires the separately installed `agy` CLI; explicitly limits the plugin to Linux and macOS. |
| `__init__.py` | `ca097a1b322a32731d6868a76150b901dbc082a17b223236f7085b00dbb42ed3` | Registers an external-process provider at `process://antigravity-agy`, uses the signed-in CLI's model picker and fallback catalog, and does not advertise a health check. |
| `transport.py` | `39ed07fedf766a99a438fa1ff9dca2ea76cc0e70df15e6db5c2e2ec73d8668e6` | Creates a private per-request agent workspace, disables the CLI agent's default components and inherited MCP, passes only a tool inventory, streams results, and terminates the process when relaying one Hermes tool call back for Hermes execution. It errors on a non-Hermes tool step. Text-only input is supported. Optional raw NDJSON tracing contains conversation data. |
| `inert_mcp.py` | `76ab52088402988c9ef79dd8a958e467b7ad35b584ecc5483a292f9fc7f02aa6` | Advertises the supplied Hermes tool manifest and records calls without executing or answering them. |
| `agy_setup.py` | `105750528c35ccb9eb77f9a58ca427e7df546d77993ae3d144a2ff2ee328348e` | Uses `agy -p=/quota` and `agy models` in a neutral temporary working directory for status and model discovery; authentication remains inside the CLI. |
| `model_catalog.py` | `04f7e16219d458b72d1bbd4146c3563762f920c76e5fcaf1ae81dc36b36ebfe3` | Provides model aliases and fallback metadata, declares context lengths as estimates, and omits vision capability. |
| `README.md` | `48124c325de129c752b25829d43ab36f6d9e675447790fd606dda7d77e1178e8` | Discloses Google subscription use, account terms, trace data, CLI-owned conversation records, prewarming processes, and limitations. Its default two prewarmed processes add memory overhead, reported upstream as about 416 MB per idle process. |
| `LICENSE` | `a1ef5185c31b5e28a1d98e468ce3f7e32a8f61cf1f768f183c6d1a989cde7d47` | MIT license for this candidate source. |

This candidate remains planned for Phase 9 and is not Windows-compatible according to its manifest. The CLI uses the account already signed into `agy`, and receives conversation content. X.A.A. would need explicit external-process and account consent, process lifecycle/isolation, data-retention disclosure and acceptance covering tool-call relay and cancellation before considering a port. No CLI, plugin, account, key, quota probe or inference was used.

## BharatCode provider

Reviewed 2026-11-15 at catalog-pinned commit [`f4cc1d1f2977963173b23b9ac6a19c3ca8851050`](https://github.com/BharatCodeAI/hermes-bharatcode/tree/f4cc1d1f2977963173b23b9ac6a19c3ca8851050), subdirectory `model-providers/bharatcode`. Catalog manifest `plugin-catalog/bharatcode.yaml`, SHA-256 `e578557a9a3532a1a00aad0bbaed848b2fdc749f597237b308859540a27364b7`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `plugin.yaml` | `75aa828ca2664a3728a72dd26e3e3c288b4e512ebbe0124f0b4ecd913d723d8c` | Declares a model-provider using `BHARATCODE_API_KEY`. |
| `__init__.py` | `31faa5b7cce5e67f9b09492e92af37b70881d9637adf3bd6b61f7dfe0c7679f1` | Uses an OpenAI-compatible API at `https://bharatcode.ai/api/model/v1`, a live model catalog and two fallback models; the fallback metadata declares vision and a 32,768-token output limit. |
| `README.md` | `046224ca5aedd3d1ed01cced6f16e717a6c440590e35b663395a9ea664e678aa` | Claims a student free tier for `.ac.in` and `.edu.in` addresses; eligibility and current offer were not verified. |
| `LICENSE` | `abd61cb3ec385ae9689cbf69f062dbaa78145d3d148ddd8a920f9d693ca66ec8` | MIT license for this candidate source. |

The candidate remains planned for Phase 9. Its API key, prompts and outputs would go to the remote BharatCode endpoint. No plugin code, account, key, catalog endpoint or inference was used.

The separate catalog entry `bharatcode-provider` points to the same pinned commit and source tree at [`thenabbu/bharatcode-hermes`](https://github.com/thenabbu/bharatcode-hermes/tree/f4cc1d1f2977963173b23b9ac6a19c3ca8851050). Its catalog manifest SHA-256, plugin manifest, provider module, README and root license match the hashes above exactly, so it is counted as a separately reviewed catalog entry but not as a second implementation.

## Hashference provider

Reviewed 2026-11-15 at catalog-pinned commit [`82700f5377a46f0e7bd482c907ed04090b6305be`](https://github.com/hashference/hashference-hermes-provider/tree/82700f5377a46f0e7bd482c907ed04090b6305be), subdirectory `hashference-provider`. Catalog manifest `plugin-catalog/hashference-provider.yaml`, SHA-256 `7c4a05a4ee92b78521bb3a3888df055122cd86a27cd547e4f51aae07d9b80bf4`.

| Pinned source | SHA-256 | Findings |
| --- | --- | --- |
| `plugin.yaml` | `8c9da0a7aa756f272f89a420bdecc03cfd6db12fa005bef3680e57925c2e7c52` | Declares a model-provider with a public model list; separate payment/MCP setup is not part of this provider plugin. |
| `__init__.py` | `8ed252d777564896bdb5a6d8c4458932426a4fba8a002a9970f67d679f96a319` | Queries the public catalog with a ten-minute cache, filters models without tool support and contexts below the configured minimum, applies declared input/output limits, and retains curated fallback models at `https://hashference.com/v1`. |
| `README.md` | `0deb980adddf5df6892ef4f5795fc322f158aada4e6dec221a0b629214968634` | Describes balance funding through mining, QUAI/Qi or staking and an optional separate CLI/MCP/payment flow; these are upstream claims and not provider-plugin behavior. |
| `LICENSE` | `d50887bafcd4e92b13e160d7bea37b8b969ebace3cd882ddad4fb800ed745d93` | License file at the pinned source. |

The candidate remains planned for Phase 9. No plugin code, account, payment flow, catalog request, key or inference was used.

## Source-review scope

This document records selected source at each catalog-pinned commit. These reviews do not implement or validate providers in X.A.A.; live provider and model testing remains deferred to the final LLM-testing phase as requested. Every reviewed candidate remains planned until X.A.A. implements and verifies the applicable acceptance requirements.

No plugin, test, dependency, executable, account credential, live endpoint, or model was used in these source reviews. Installed behavior, current vendor model metadata, authentication routing, account entitlement, pricing, and live compatibility remain unverified.

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

The candidate remains `planned`, Phase 9. These reviews classify twenty-eight of the 90 `models` category catalog entries (Abstract, Ace Data Cloud, Adaptive Inference, AIHubMix, AI Stupid Level, Aixy, AnyAPI, AnyRouter, Antigravity agy, Antigravity OAuth, Antigravity Subscription DirectSDK, BharatCode, BharatCode Provider, Chutes, Claude, Cloudflare Workers AI, CoreWeave, Fortytwo, Groq, Hashference, GitHub Copilot, ngrok AI Gateway, Requesty, RuAPI, Runcrate, Safe AI Labs, Telnyx, and Together); 62 remain pending classification/source review. BharatCode and BharatCode Provider resolve to the same source tree, so these are 28 reviewed catalog entries from 27 distinct source trees. The audit does not add OAuth/device login, GitHub token exchange, an account pool, remote Copilot routing, or external-process ACP to X.A.A. Those would require owner-selected remote-account consent, OS-protected secret storage, strict URL/origin and credential routing, account/cost notices, process allowlisting, complete cancellation and cleanup, capability-aware model discovery, model-free fixture acceptance, and the final live-provider acceptance after the requested LLM-test deferral.

No plugin, test, CLI, credential, account, live endpoint, or model was used. This is pinned-source evidence only; provider terms, current GitHub account/policy eligibility, authentication outcomes, token-cache access controls, installed CLI behavior, billing, and runtime compatibility remain unverified.

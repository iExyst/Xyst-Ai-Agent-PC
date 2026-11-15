# Pinned programmatic-interface and training-source audit

Reviewed 2026-11-14 against `NousResearch/hermes-agent` commit `1e208b280deb18f01d015b0acdf3ef3552d08039`, checked out in `.hermes-audit/`. This is a source inspection of selected interfaces and training-related utilities, not a claim of complete Hermes parity or an execution test of every path.

## ACP programmatic interface

The pinned package registers `hermes-acp = "acp_adapter.entry:main"` in `pyproject.toml`. `acp_adapter/entry.py` parses version, dependency-check, provider/model setup, and browser-setup options, then runs an ACP stdio server with the unstable protocol enabled. Its server constructs `HermesACPAgent`; the agent may delegate through `GatewayACPAgent`, which keeps MCP discovery and execution in the gateway daemon while the ACP process acts as a viewer.

The reviewed adapter methods cover initialization, authentication, session creation/load/resume/list/fork, cancellation, prompts, model and mode changes, and configuration options. Protocol support differs between direct and gateway implementations. Because ACP includes SDK-provided inherited methods and unstable protocol features, this audit does not infer unsupported behavior from methods absent in a local class; protocol-level coverage requires checking the pinned SDK and testing the negotiated capabilities.

## `hermes_platform` scope

The pinned `hermes_platform` package contains 16 tracked files. The inspected host summary gathers local OS, account, locale, and hardware facts. Its resolver code locates installed applications and can inspect or probe application availability. These are local platform-information and application-resolution facilities. The inspected package does not itself define a general public HTTP API or a training service.

## Evaluation and training-related utilities

The pinned `evals/` tree contains 236 tracked files, so it is a broad set of scripts and fixtures rather than one stable public evaluation API. `scripts/sample_and_compress.py` samples trajectory data from Hugging Face datasets, counts tokens, and sends entries into the trajectory compression utility. `trajectory_compressor.py` protects selected turns and tool-call pairs, summarizes eligible middle turns through a configured model, and writes compressed trajectory data. This supports dataset preparation and token-budget compression. The inspected files do not establish a complete fine-tuning trainer, provider-specific training integration, or a comparative benchmark protocol; those remain separate requirements.

## Pinned source digests

SHA-256 digests below identify the reviewed source files in the pinned checkout:

| Source | SHA-256 |
| --- | --- |
| `acp_adapter/entry.py` | `77ea948af74acff2b0b20192477080be7d56ff4072378c20745506169e167ba0` |
| `acp_adapter/server.py` | `d1118e723b5197300ae192420e4ba2b552ecde0eb6370e80527a834e6b786e62` |
| `acp_adapter/gateway_server.py` | `a832026abdff8f3ec06475de649d0b34cd41ee54e67a9350f31b5bbec762c57f` |
| `hermes_platform/host/summary.py` | `1aab2090af7ba83c9c082dd95a2688a9a117a2e460f785e99712371bd1cff53f` |
| `hermes_platform/resolver/app.py` | `ee5ff2a8d9d67534df22e722d7a7235e4c98810aeeedaf2d3948ea5d53db0063` |
| `trajectory_compressor.py` | `2cd9949f2eb2f86b5104b42435acad6b441ae7fea3dbc3d4c38d306c6b6ceff1` |
| `scripts/sample_and_compress.py` | `28702d5cf30f060ea0f25fce56645d85344e870b86aa8d3ac83f610d3d85f2c7` |

The local source hashes and tracked-file counts were checked against the pinned checkout. No model inference was performed for this audit.

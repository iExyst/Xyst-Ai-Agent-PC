# Batch evaluation runs

`xaa batch` runs up to 32 bounded cases through the authenticated X.A.A. API. Each case becomes an ordinary durable run. Batch mode does not add a second agent loop or bypass the run's permission and review controls.

## Input

Create a UTF-8 JSON document with `version: 1` and a `cases` array. Every case needs a unique `id`, `objective`, and `acceptance_criteria`. Optional `checks` are deterministic assertions over the public trajectory; the expected values are saved in the local manifest but are not sent to the model.

```json
{
  "version": 1,
  "cases": [
    {
      "id": "read-nonce",
      "objective": "Read nonce.txt and return the nonce.",
      "acceptance_criteria": ["Read the selected workspace file and return its contents."],
      "checks": [
        {"kind": "final_contains", "expected": "EXPECTED_NONCE"},
        {"kind": "tool_called", "expected": "workspace_read_file"},
        {"kind": "tool_completed", "expected": "workspace.read_file"},
        {"kind": "state_equals", "expected": "verifying"}
      ]
    }
  ]
}
```

Supported checks are:

- `final_equals`: the latest assistant message's text exactly matches `expected`; a message without text has an empty value.
- `final_contains`: the latest assistant message's text contains `expected` as a case-sensitive substring; a message without text has an empty value.
- `tool_called`: an assistant message proposed a tool with that model-visible function name, such as `workspace_read_file`.
- `tool_completed`: the same public tool call ID has both a `tool.intended` and `tool.completed` event, with the expected logical tool name, such as `workspace.read_file`.
- `state_equals`: the exported run state matches `expected`. A `verifying` result is a candidate awaiting user review; it does not mean the user accepted it.
- `artifact_count_equals`: the number of entries in the versioned artifact manifest equals the decimal `expected` count. This verifies that artifacts were recorded, not that their contents satisfy a semantic requirement.
- `artifact_sha256_exists`: at least one artifact manifest entry records the exact lowercase 64-character SHA-256 in `expected`. This checks the recorded identity only; it does not download the artifact or check its semantic contents.
- `artifact_media_type_exists`: at least one artifact manifest entry records the exact media type in `expected`, such as `image/png` or `text/plain`. This checks manifest metadata only; it does not inspect artifact bytes.

When a case has checks, `batch.json` records each check result and marks the case `passed` only when all checks pass. Cases without checks are `not_evaluated`. These checks cover exact text, substring, call proposal, successful tool completion, saved state, artifact count, recorded artifact digest, and recorded artifact media type; they do not interpret free-form acceptance criteria or replace user review.

## Run and resume

```powershell
uv run xaa batch cases.json --output .xaa-batch-results --model MODEL_ID --workspace C:\path\to\project
uv run xaa batch cases.json --output .xaa-batch-results --model MODEL_ID --workspace C:\path\to\project --resume
```

The first command requires a new output directory. Resume requires identical input bytes and run settings. The manifest is checkpointed atomically, each run submission has an idempotency key, and trajectories are capped at 10 MiB each. `batch.json` records run states, evaluation totals, trajectory paths, byte sizes, and SHA-256 hashes. Trajectories use the authenticated redacted run-export API and must declare `format_version: 1`; batch scoring rejects missing or unsupported versions.

## Reviewed text-only training export

After reviewing and accepting a candidate in the desktop, refresh its batch checkpoint with the same `xaa batch ... --resume` command. Export eligible cases locally, without an API token:

```powershell
uv run xaa export-training .xaa-batch-results --output reviewed-training.jsonl
```

Only cases whose refreshed manifest says `completed`, whose configured deterministic checks still pass, and whose trajectory integrity, version `1`, and accepted run identity/state match are included. The JSONL contains the public trajectory's run objective and final assistant text. It omits manifest objectives, tool proposals, tool transcripts, and all other events; any case with a tool proposal is skipped because flattening it would lose tool semantics. Pending/rejected reviews, unscored cases, and empty text are skipped. The command refuses to overwrite a file, exports at most 10 MiB, and prints the included IDs, skipped counts, output size, and digest. This is a local text-only dataset export, not provider-specific fine-tuning integration or upload. Checks that explicitly expect `verifying` will no longer pass after accepting a run; use checks that remain true at completed state for exportable examples.

## Compress conversational trajectories

Use `xaa compress-trajectories` for JSONL entries with a `conversations` or `messages` array of `{ "from": "human|gpt|tool|system", "value": "..." }` turns, or pass one native X.A.A. `xaa-trajectory` JSON export:

```powershell
uv run xaa compress-trajectories input.jsonl --output compressed.jsonl --model MODEL_ID --max-tokens 8192 --summary-tokens 256 --keep-last 4
```

For a single native X.A.A. export, pass its JSON file instead; the output is a derived JSON transcript:

```powershell
uv run xaa compress-trajectories run-export.json --output run-context.json --model MODEL_ID --max-tokens 8192
```

The model identifier must match an already-loaded LM Studio model. Compression uses the configured loopback LM Studio address (default `http://127.0.0.1:1234`) and the optional LM Studio credential in Windows Credential Manager. It does not use cloud providers, download a tokenizer/model, or load a model. Token counting uses LM Studio's installed model tokenizer. The first system, human, assistant, and tool turns and the requested recent tail are preserved verbatim. Assistant turns and their immediately following tool results stay together. The intervening middle becomes one `[CONTEXT SUMMARY]:` human turn; summary input is explicitly treated as untrusted, and every output record includes bounded compression metrics. For this short summarization request X.A.A. disables model reasoning so the output-token allowance remains available for the summary; ordinary agent inference retains the selected model's configured reasoning behavior.

A live LM Studio JSONL acceptance passed on Windows with `qwen/qwen3.5-9b` at 16,384 context. It compressed a synthetic 22-turn conversation from 1,886 tokens to 435 tokens in one summary call, preserved protected head/tail turns, and retained the required fixture facts ([report](../../.acceptance/live-trajectory-summary-20261128/report.json)). This verifies one local model and fixture; it does not measure semantic faithfulness across arbitrary trajectories. The native-export path has deterministic derivation coverage but has not had a separate end-to-end live-model acceptance.

The command accepts at most 10,000 JSONL entries, 10 MiB per input line, 256 turns per conversation, 100 MiB total output, and a 256 KiB summary region per entry. Output is published only after the full input succeeds and is never overwritten. If the protected context plus summary cannot fit the requested token budget, or the local model truncates the summary, the command fails without publishing partial output. The live JSONL acceptance above verifies one input path; native-export live summarization remains unverified.

For a native JSON export, the command writes `xaa-compressed-trajectory` JSON with the source run metadata, source-file SHA-256, and a derived conversation view. It includes public assistant messages, the run objective, review feedback, and public tool-call completion markers. Redacted exports do not include tool result text, so the derived transcript says when a result is unavailable; it never invents one. Multimodal messages are rejected instead of flattened. This output is for analysis and context compression, not replay or restoration; keep the original export as the authoritative event history. Both formats are processed locally through the explicitly loaded loopback model. The live acceptance exercised JSONL input; native-export summarization remains unverified end to end.

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

- `final_equals`: the last non-empty assistant message exactly matches `expected`.
- `final_contains`: the last non-empty assistant message contains `expected` as a case-sensitive substring.
- `tool_called`: an assistant message proposed a tool with that model-visible function name, such as `workspace_read_file`.
- `tool_completed`: the same public tool call ID has both a `tool.intended` and `tool.completed` event, with the expected logical tool name, such as `workspace.read_file`.
- `state_equals`: the exported run state matches `expected`. A `verifying` result is a candidate awaiting user review; it does not mean the user accepted it.

When a case has checks, `batch.json` records each check result and marks the case `passed` only when all checks pass. Cases without checks are `not_evaluated`. These checks cover exact text, substring, call proposal, successful tool completion, and saved state; they do not interpret free-form acceptance criteria or replace user review.

## Run and resume

```powershell
uv run xaa batch cases.json --output .xaa-batch-results --model MODEL_ID --workspace C:\path\to\project
uv run xaa batch cases.json --output .xaa-batch-results --model MODEL_ID --workspace C:\path\to\project --resume
```

The first command requires a new output directory. Resume requires identical input bytes and run settings. The manifest is checkpointed atomically, each run submission has an idempotency key, and trajectories are capped at 10 MiB each. `batch.json` records run states, evaluation totals, trajectory paths, byte sizes, and SHA-256 hashes. Trajectories use the authenticated redacted run-export API.

## Reviewed text-only training export

After reviewing and accepting a candidate in the desktop, refresh its batch checkpoint with the same `xaa batch ... --resume` command. Export eligible cases locally, without an API token:

```powershell
uv run xaa export-training .xaa-batch-results --output reviewed-training.jsonl
```

Only cases whose refreshed manifest says `completed`, whose configured deterministic checks still pass, and whose trajectory integrity and accepted run state match are included. The JSONL contains the public trajectory's run objective and final assistant text. It omits manifest objectives, tool proposals, tool transcripts, and all other events; any case with a tool proposal is skipped because flattening it would lose tool semantics. Pending/rejected reviews, unscored cases, and empty text are skipped. The command refuses to overwrite a file, exports at most 10 MiB, and prints the included IDs, skipped counts, output size, and digest. This is a local text-only dataset export, not provider-specific fine-tuning integration or upload. Checks that explicitly expect `verifying` will no longer pass after accepting a run; use checks that remain true at completed state for exportable examples.

## Compress conversational trajectories

Use `xaa compress-trajectories` for JSONL entries with a `conversations` or `messages` array of `{ "from": "human|gpt|tool|system", "value": "..." }` turns, or pass one native X.A.A. `xaa-trajectory` JSON export:

```powershell
uv run xaa compress-trajectories input.jsonl --output compressed.jsonl --model MODEL_ID --max-tokens 8192 --summary-tokens 256 --keep-last 4
```

The model identifier must match an already-loaded LM Studio model. Compression uses the configured loopback LM Studio address (default `http://127.0.0.1:1234`) and the optional LM Studio credential in Windows Credential Manager. It does not use cloud providers, download a tokenizer/model, or load a model. Token counting uses LM Studio's installed model tokenizer. The first system, human, assistant, and tool turns and the requested recent tail are preserved verbatim. Assistant turns and their immediately following tool results stay together. The intervening middle becomes one `[CONTEXT SUMMARY]:` human turn; summary input is explicitly treated as untrusted, and every output record includes bounded compression metrics.

The command accepts at most 10,000 JSONL entries, 10 MiB per input line, 256 turns per conversation, 100 MiB total output, and a 256 KiB summary region per entry. Output is published only after the full input succeeds and is never overwritten. If the protected context plus summary cannot fit the requested token budget, or the local model truncates the summary, the command fails without publishing partial output. No live-model acceptance has been run for this capability yet.

For a native JSON export, the command writes `xaa-compressed-trajectory` JSON with the source run metadata, source-file SHA-256, and a derived conversation view. It includes public assistant messages, the run objective, review feedback, and public tool-call completion markers. Redacted exports do not include tool result text, so the derived transcript says when a result is unavailable; it never invents one. Multimodal messages are rejected instead of flattened. This output is for analysis and context compression, not replay or restoration; keep the original export as the authoritative event history. Both formats are processed locally through the explicitly loaded loopback model, and no live summarizer acceptance has been run yet.

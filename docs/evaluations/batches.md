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

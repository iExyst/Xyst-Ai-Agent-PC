# Local-model baseline

Measured 2026-11-03 against the running local LM Studio installation. No cloud requests, downloads or production default selection. Exact discovered model identifiers are below; model weight files and quantizations were not fingerprinted.

| Model identifier | Read nonce | Targeted edit | Total passed | Schema-valid / parseable proposals | Median request seconds | Median trial seconds |
|---|---:|---:|---:|---:|---:|---:|
| `qwen2.5-0.5b-instruct` | 2/2 | 0/2 | 2/4 | 8/8 | 0.199 | 1.245 |
| `qwen3-0.6b` | 1/2 | 0/2 | 1/4 | 7/7 | 0.695 | 2.203 |
| `qwen/qwen3.5-9b` | 2/2 | 2/2 | 4/4 | 11/11 | 1.699 | 11.211 |

Each trial used a fresh capability diagnostic, SQLite journal and random fixture. Read trials required reporting the exact nonce without modifying bytes. Edit trials required a targeted replacement preserving the nonce, newline and neighboring file. Passing requires a successful requested tool result, exact expected bytes, the coordinator's verifying state and no unresolved tool intent. This is fixture success, not user acceptance or autonomous completion. A post-run journal audit confirmed all seven passing trials had successful requested tool results; the harness now explicitly enforces that stronger condition.

Models ran sequentially at context length 16,384, with six coordinator iterations and 45 seconds per trial. Request timings and proposal counts include the fresh diagnostic; model loading is measured separately. All recorded completion envelopes were valid. Schema-valid proposals alone did not predict task success.

The 0.5B edit failures ended with exhausted iteration budget and a filesystem-worker interruption; the latter's underlying cause remains unclassified. The 0.6B failures included two failed capability diagnostics and an empty final response after read/replace proposals; that edit did not satisfy the expected bytes. Failed trials remain in the report and journals.

Hardware: Windows 11 build 26200, processor identifier `AMD64 Family 26 Model 68 Stepping 0, AuthenticAMD`, NVIDIA GeForce RTX 5080 with 16,303 MiB reported GPU memory, driver 621.01. After-trial host GPU memory ranged from 3,963–3,968 MiB for the 0.5B trials, 5,774–5,793 MiB for 0.6B and 10,114–10,128 MiB for 9B. These are host-wide point snapshots, including background usage; they are neither model-attributed allocations nor peaks. Host RAM snapshots are in the raw report.

Evidence: `.acceptance/92dcb1db-e855-4741-95e8-dde01ac3f5df/report.json`, `outcome-audit.json` and per-trial SQLite journals/workspaces. Local evidence is excluded from normal source staging. Reproduce with `uv run python scripts/benchmark_local_models.py`; optional `--models` accepts already-installed identifiers and `--repetitions` accepts 1–10. This invokes real model loading, inference and unloading.

Four trials per model are insufficient to choose a production default. Streaming, cancellation, recovery, complex coding, multi-agent reliability, repeated load behavior and per-process peak resources remain unevaluated by this benchmark. The UI therefore requires an explicit model choice.

Harness evolution after this baseline: capability diagnostics now have a fixed 512-token output cap and run-triggered diagnostic usage is journaled. The recorded 12 trials predate those changes and have not been rerun; compare future results using their recorded policy rather than assuming identical conditions.

## Refreshed comparison (2026-11-07)

A repeat with the current runtime and the same three installed model identifiers completed all 12 trials at the 16,384-token requested context: `.acceptance/a5144fab-5e9d-4f65-ab40-e87aae067e24/report.json`. Results were `qwen2.5-0.5b-instruct` 1/4, `qwen3-0.6b` 0/4, and `qwen/qwen3.5-9b` 1/4. All 19 parseable tool proposals were schema-valid, but this did not predict task completion. Median request latencies including diagnostics were 6.57 s, 6.42 s, and 11.93 s; mean trial durations were 31.22 s, 26.35 s, and 50.03 s. The two small models had four `ProviderError` trials combined; the 9B had two `TimeoutError` trials. The 0.6B read produced a candidate without the required nonce. No model passed either targeted edit in this refreshed sample.

These results are not directly comparable to the 2026-11-03 baseline: LM Studio already had multiple model instances loaded before the run, including two 9B instances; host GPU memory samples were around 15.4 GiB of 16.3 GiB. This was not a controlled idle-machine comparison, and host snapshots are not peak or per-model measurements. The follow-up one-repetition 9B run after the harness fix also failed both trials (`.acceptance/ad33a895-0772-448a-b994-d431395a95e3/report.json`), with one provider error and one timeout while reusing an existing 16,384-context instance. It did not load or unload that instance. The preloaded instance counts for the three benchmarked models were restored after the first refreshed attempt; current configurations are recorded in LM Studio itself, not asserted from benchmark outcomes.

The harness now reads LM Studio's native loaded-instance inventory, reuses a preexisting instance (preferring the requested context), records the selected instance/context, and unloads only an instance it loaded itself. This avoids changing an existing instance's lifecycle and reports a different existing context honestly. A default remains unselected. The newer sample shows that the old four-trial 9B success is not a reliability guarantee; an idle, serialized, repeated benchmark with process-attributed peak resource metrics is still needed before recommending a default.

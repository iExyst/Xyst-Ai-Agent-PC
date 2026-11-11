# Xyst AI Agent (X.A.A.)

Windows-first desktop agent under development. The native app, local engine and CLI use LM Studio for journaled workspace work, bounded specialist assignments and explicit result review. The full release remains incomplete.

## Status

- Current work: durable execution, workspace tools, desktop workflows, and a partial ACP stdio bridge; several phase gates remain incomplete
- Completion is not expressed as a single percentage until the broad phase gates can be weighted defensibly; see the phase assessment in `docs/progress.md`.
- Hermes audit pin: `1e208b280deb18f01d015b0acdf3ef3552d08039` (official `NousResearch/hermes-agent`, audited 2026-11-03)
- See the [user guide](docs/user-guide.md) for operating instructions, and `docs/progress.md` for evidence and next gates.
- The initial editor integration uses the authenticated run API over ACP stdio; see [ACP integration](docs/integrations/acp.md) for its scope and current token handoff limitation.

## Unsigned Windows development installer

The latest successful [Engine checks run](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions) provides an installer artifact (14-day retention); its name identifies it as signed or unsigned. The current build is unsigned because no signing certificate is configured. Its setup bundles the engine; Python and Node are not runtime prerequisites. LM Studio remains a separate installation. The disposable hosted Windows test verifies installation, actual GUI/API startup, shutdown, uninstall and data preservation. This remains a development build; upgrade and clean normal-user-machine acceptance are incomplete. Read the [0.1.1 development build notes](docs/releases/0.1.1.md) and [installer evidence and limitations](docs/releases/windows-installer.md).

## Development use

Build the native development bundle with `npm ci`, `uv sync`, then `npm run build:native:dev`. Launch `build/native-dev/xaa-desktop.exe`; it starts its bundled engine and connects automatically. Closing the native app stops that engine and keeps saved database records. The bundle is for development and has not passed installer or clean-machine acceptance.

After connecting, explicitly choose a discovered local model; the app does not select the first model automatically. See [the local-model baseline](docs/evaluations/local-model-baseline.md) for measured results and limitations.

Run `uv sync`. Set `XAA_API_TOKEN` to a randomly generated secret of at least 32 characters in both engine and client environments. Start the engine with `uv run python -m xaa.api` (loopback port 8765).

Use `uv run xaa models`, then submit `uv run xaa run "Read evidence.txt and report its contents" --model MODEL_ID --workspace C:\path\to\project`. Inspect the returned run ID with `uv run xaa status RUN_ID` or `uv run xaa events RUN_ID`. Pause, resume and cancel commands operate on that same run. Observe mode permits reads; workspace mode permits existing-file edits. Final candidates await explicit review in the desktop. Saved runs can be reopened without repeating execution.

For an interactive terminal conversation, run `uv run xaa chat --model MODEL_ID`. Each turn is a separate durable coordinator run, and its bounded transcript is saved in the engine's SQLite conversation history so `xaa chat --model MODEL_ID` resumes it after restart. Use `--session NAME` for separate conversations; a session is tied to its model, workspace and mode. `/reset` clears that session's context while preserving its durable runs, and `/quit` exits. Ctrl+C requests cancellation of the active turn. `--reset` clears a saved session before starting.

Optional coordinator response streaming: set `$env:XAA_STREAM_RESPONSES='1'` before starting the engine or launching a freshly rebuilt native bundle. Partial text is shown separately from final candidates. Streamed tool arguments must assemble and validate completely before execution. This remains opt-in: real-model checks have included both a successful search/read/answer and an empty final-answer failure. Specialist responses currently use non-streaming inference. The protocol follows [LM Studio's streamed tool-call format](https://lmstudio.ai/docs/developer/openai-compat/tools#streaming).

If LM Studio requires authentication, use `uv run xaa credentials set` to enter its token privately into Windows Credential Manager, then restart the engine. Remove it with `uv run xaa credentials delete`. The engine reads this credential directly from the OS vault.

Use `uv run xaa backup` to create an integrity-checked `.xaa-backup` bundle containing the SQLite database and managed artifacts. Stop X.A.A., then restore offline with `uv run xaa restore-bundle C:\path\to\backup.xaa-backup --destination C:\new-data\XAA`; set `XAA_DATA_DIR=C:\new-data` before starting the engine. The restore destination must not already exist. The older `xaa restore` command accepts a database-only snapshot and does not restore artifact files. See [backup and restore](docs/releases/backup-restore.md).

For bounded evaluation batches, prepare a version 1 JSON file with a `cases` array (`id`, `objective`, `acceptance_criteria`) and run `uv run xaa batch cases.json --output .xaa-batch-results --model MODEL_ID --workspace C:\path\to\project`. Optional per-case `checks` score exact/contained final text, proposed/completed tools, and saved state without adding expected values to the model prompt. Add `--resume` to continue the same checkpoint after interruption. See [batch input and scoring](docs/evaluations/batches.md) for the check schema and report semantics.

After explicitly accepting reviewed candidates, refresh the same batch with `--resume` and use `uv run xaa export-training .xaa-batch-results --output reviewed-training.jsonl` to write a local, reviewed text-only JSONL. Tool-using trajectories are excluded; see the [training export limits](docs/evaluations/batches.md#reviewed-text-only-training-export).

Optional reported-token stop threshold: pass `--max-total-tokens 20000` to `xaa run`. This blocks further work based on saved provider usage; it is not a hard inference ceiling and includes run-triggered capability diagnostics. See [token threshold behavior](docs/architecture/token-thresholds.md).

Use `--max-output-tokens 512` to request a generated-token cap on each parent/specialist model call. Capped responses require usage data; truncated responses cannot execute tools. This separate limit does not cap prompt tokens.

In the desktop, expand **Run limits** before submitting an objective. **Run resources** shows the active run’s saved limits and measured token usage, with unknown calls reported separately. Form edits affect new objectives.

Capability checks use a separate 512-token output cap per request. Truncated checks leave tools disabled, and run-triggered diagnostic usage is included in the reported token threshold.

Plans may give specialists tighter reported-token allowances and output caps. The plan view displays these settings; exhausted allowances block dependents and cannot be raised by the specialist. See [token policy](docs/architecture/token-thresholds.md).

Count a fixture prompt against an already-loaded model with `uv run xaa tokens "Say hello." --model MODEL_INSTANCE_ID`. This uses the bundled tokenizer worker and returns count/context metadata without prediction. See [tokenizer service](docs/architecture/tokenizer-service.md) for scope and the pinned prerelease SDK dependency.

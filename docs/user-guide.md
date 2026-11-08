# Xyst AI Agent user guide

**Development preview · updated 2026-11-05**

Xyst AI Agent (X.A.A.) is a Windows-first local agent application. It sends model requests to the LM Studio server you configure, records runs in SQLite, and requires explicit review before accepting agent results. The product is still under development; this guide describes behavior currently present in the repository and does not claim full Hermes parity or production release readiness.

## Requirements and installation

- Windows 10 or later, with Microsoft WebView2 available.
- LM Studio installed separately, with its local server started and a model loaded when you want to run an objective.
- A model that supports the required tool calls for the selected workflow. X.A.A. discovers model IDs; it does not download or select a model automatically.

The current Windows installer is an unsigned development build. Open the repository's [GitHub Actions runs](https://github.com/iExyst/Xyst-Ai-Agent-PC/actions), select a successful **Engine checks** run, and download its `xaa-windows-installer-unsigned` artifact while it is retained. The installer bundles X.A.A.'s engine; Python and Node are not runtime prerequisites. Installer upgrades and clean normal-user-machine acceptance remain incomplete. See [installer scope and evidence](releases/windows-installer.md).

For a development build, follow the commands in the repository [README](../README.md). Do not run the source checkout as if it were an installed production release.

## First launch

1. Start LM Studio's local server. The default address is `http://127.0.0.1:1234`; the desktop setup panel can change it to another HTTP loopback address.
2. Connect the desktop to the local engine using its API token. The renderer holds that token in memory for the current window.
3. In **LM Studio setup**, check the connection, discover models, and select the exact model ID to use.
4. Run the harmless structured tool diagnostic. Tool use stays unavailable when the diagnostic fails.
5. Choose a project folder with the workspace picker.
6. Start the **read-only onboarding demonstration**. It reads project documentation in Observe mode. Inspect its answer and evidence, then submit the explicit final review.

If LM Studio requires authentication, save its token through **LM Studio setup** or `uv run xaa credentials set`. X.A.A. stores that provider credential in Windows Credential Manager. Restart the engine after using the CLI credential command. See [provider settings](architecture/provider-settings.md).

## Run a project objective

1. Select the project workspace and choose a model.
2. Enter a specific objective and observable acceptance criteria. Optional run limits and an exact file-content check are available in the desktop.
3. Choose an operating mode:
   - **Observe** allows workspace reads and does not grant file edits.
   - **Workspace** allows supported file creation and edits within the selected workspace. New files require an existing parent directory and do not overwrite an existing destination.
4. Grant optional capabilities only for the next run. Isolated commands require Workspace mode and a passing sandbox readiness check; the command runs against a private copy, has no network access, and its changes are discarded. Browser, web-search and MCP tools have separate per-run controls.
5. Watch the saved run state and events. You can pause, resume or cancel a run from the desktop. The CLI has matching run-control commands.
6. Inspect the answer, task candidates, file evidence, command results and validation artifacts. A candidate is not an accepted result. Use final review to accept or reject it. For code changes, select current successful project-validation evidence when the run requests it.

The desktop can attach one PNG or JPEG image up to 3 MiB to a new objective. The engine checks the selected model's advertised vision capability, stores the image with the run, and verifies its checksum before resending it after restart. Image input uses the default context estimate, which assigns a fixed allowance to each image; actual image token use varies by model and image. Exact text-token admission does not count image tokens and refuses image runs. A real local Qwen3.5 9B run correctly counted two red circles in a synthetic image; this single example does not establish general visual accuracy across models or image types. See [live image acceptance](releases/evidence/live-image-vision.json).

The desktop can also attach one PDF, DOCX, XLSX, UTF-8 text file or Markdown document up to 4 MiB. PDFs must contain extractable text and have at most 50 pages; OCR for scanned PDFs is not supported. Scanned PDFs may contain no extractable text, as described in [pypdf's text extraction documentation](https://pypdf.readthedocs.io/en/latest/user/extract-text.html). DOCX and XLSX extraction read only bounded XML parts from the ZIP package and never extract archive members to disk. Spreadsheet extraction is limited to 50 worksheets, 10,000 cells, and 8 MiB of uncompressed worksheet XML; it includes cached cell values and does not evaluate formulas or reproduce workbook formatting. All extracted text is limited to 48,000 characters, kept as an inspectable run artifact, and included in the prompt as untrusted reference material. The original document and extracted text are both retained with checksums and restored with a backup; original PDF, DOCX and XLSX files can be downloaded from the run's artifact list. A real Qwen3.5 9B acceptance returned an unseen marker from a generated PDF; see [live PDF acceptance](releases/evidence/live-document-reading.json). These individual cases do not establish broad document accuracy. Text input uses the normal selected-model prompt and exact text-token admission where configured; image-plus-document attachments are not accepted together.

Specialists work on bounded assignments. Their file edits are isolated proposals until the user previews and integrates them. Existing workspaces are not changed by an isolated command; explicit file integration is a separate reviewed operation.

## Saved runs, memory and reusable procedures

The **Saved runs** panel reopens prior run records without repeating execution. Conversation search is available from run history.

In **Memory preferences**, save, edit, search, export or delete preferences that you want the parent agent to recall. In **Specialist identities and model routes**, choose a discovered model, edit role instructions, and save or delete explicit per-role memories. Routes and instructions are snapshotted into newly saved plans. Saved memories are bounded context; they do not grant tools or permissions.

**Reusable procedures** are versioned text procedures. A procedure trial is shown as unverified data. Only procedures that have the required evaluation evidence can be activated for reuse. Review the procedure's version and declared requirements before selecting it for a run.

## Optional integrations

- **Web search:** configure a Brave Search key in the integration panel and explicitly allow search for the next run. Queries are sent to Brave; returned content is untrusted. Search permission does not authorize opening result pages.
- **Managed browser:** approve exact hostnames for the next run and enable browser reads. X.A.A. uses a fresh Edge session without your saved browser profile. It reads visible page content and can capture screenshots; it does not submit forms, click buttons or download files.
- **MCP:** configure and connect an MCP server. Discovered tools default to no permission. Map read/write permissions, and select any write tool explicitly for the next run. Treat server descriptions and tool results as untrusted. See [MCP setup and limits](architecture/mcp.md).
- **Automations and Telegram:** schedules run through the local engine. Telegram is text-only and requires a configured bot plus private-account pairing. A scheduled reply is sent only to a linked account selected as its destination. Review [Telegram setup, delivery and recovery](channels/telegram.md).

These integrations require their own credentials or local services. A fixture test does not prove compatibility with a real third-party server or account.

## Command-line use

Set `XAA_API_TOKEN` for the CLI and start the local engine before issuing authenticated commands. Useful commands include:

```powershell
uv run xaa models
uv run xaa run "Read the project overview and summarize it" --model MODEL_ID --workspace C:\path\to\project
uv run xaa status RUN_ID
uv run xaa events RUN_ID
uv run xaa history --scope conversation --query "deployment details"
uv run xaa export RUN_ID --output C:\path\to\run.json
uv run xaa batch C:\path\to\cases.json --output C:\path\to\batch-results --model MODEL_ID --workspace C:\path\to\project
```

Batch JSON cases can include deterministic checks for final text, proposed/completed tool calls, and run state; the report does not auto-approve candidates. Use `--resume` with the same input and settings after interruption. See the [batch evaluation guide](evaluations/batches.md) for the input schema and scoring rules.

After accepting a candidate, refresh its batch checkpoint with `--resume`. `xaa export-training BATCH_DIR --output FILE.jsonl` then exports only accepted, check-passing, text-only examples. It works offline and locally, skips tool-use traces, and will not overwrite an existing file. See the [training export details](evaluations/batches.md#reviewed-text-only-training-export).

For interactive terminal conversation:

```powershell
uv run xaa chat --model MODEL_ID --session project-notes
```

Each turn creates a durable run. The named session stores up to six recent user/assistant pairs in the local database, bounded to 8 KiB of context. The session is tied to its model, workspace and mode. Use `/reset` or restart with `--reset` to clear session context; saved runs remain. Ctrl+C requests cancellation of the current run. Full candidate text remains in the run history when saved chat context is clipped.

## Pause, cancel and recover

- Use pause/resume when the run should retain its saved state and continue later.
- Use cancel to stop a run. Cancellation cannot reverse an external action that already completed.
- If a command or MCP write ends with an unknown outcome, inspect the saved event and reconciliation controls before deciding what to do. X.A.A. avoids automatically repeating ambiguous external writes.
- Create a portable backup before a significant upgrade or data move. `uv run xaa backup` writes a `.xaa-backup` bundle containing the SQLite snapshot and referenced managed artifacts.
- Restore a bundle offline into a new, nonexistent directory:

```powershell
uv run xaa restore-bundle C:\path\to\backup.xaa-backup --destination C:\new-data\XAA
$env:XAA_DATA_DIR = 'C:\new-data'
```

The bundle restore validates the database, schema, foreign keys and artifact hashes. The older `xaa restore` command restores a database-only snapshot and does not restore artifact bytes. Keep backups secure because they contain run, chat and memory data. See [backup and restore](releases/backup-restore.md).

## Local data and security

By default, the engine stores its database under `%LOCALAPPDATA%\XAA\xaa.sqlite3`, with managed artifacts and backups alongside it. Setting `XAA_DATA_DIR` changes the data root. Removing the application does not automatically remove user data.

The local API is authenticated and binds to loopback by default. Do not expose it to a network. Keep the API token private; the desktop keeps its copy in renderer memory. Provider credentials are stored in the operating-system credential store. Workspace files, web content, command output, MCP metadata and saved memories are data, not permission grants.

## Current limits

X.A.A. is not a production release. The installer is unsigned; version-to-version upgrade and clean normal-user-machine recovery acceptance remain open. Live interactive CLI model use, broad optional-provider routing, media/document support, several Hermes capability families, session unification between CLI and desktop, and full packaged/release acceptance are incomplete. Consult the [progress record](progress.md) and [capability matrix](parity/hermes-matrix.md) for the verified scope and remaining gaps.

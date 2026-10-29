# Xyst AI Agent PC — new conversation handoff

Updated 2026-10-29. The full project is **not complete**. This handoff describes verified state and the quickest useful next actions; it does not replace either original brief.

## Start here

1. Read `C:\Users\hicks.GAMERX\Documents\Xyst Ai Agent\RESUME_CHECKPOINT.md`. It is append-only history: later entries supersede older status statements.
2. Read this handoff, `docs/brief-traceability.md`, and `docs/verification-history.md` in the active PC worktree below. Recheck Git and live process/PR state before acting.
3. Preserve all existing uncommitted changes. Continue in the active worktree, not the dirty original workspace. Do not reset, clean, broadly copy, or overwrite other worktrees.

Target repository: https://github.com/iExyst/Xyst-Ai-Agent-PC

| Location | Verified state at this handoff | How to use it |
| --- | --- | --- |
| `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-pr81-review` | Detached HEAD `db45ae8b995482213a58e36e08182625f5f8e8d6`; substantial uncommitted rerun candidate | Primary continuation workspace. Safeguard and inspect its diff before changing branches. |
| `C:\Users\hicks.GAMERX\Documents\Xyst Ai Agent` | Dirty original workspace, separate old source history | Preserve; only checkpoint appends have been authorized here for this continuation. |
| `C:\Users\hicks.GAMERX\Documents\Xyst Ai Agent PC Sync` | HEAD `12fb2b569952a4dac34ed467d10998e24407ccf7`; four untracked visual artifacts in `apps/cli` | Do not assume it is current or completely clean. Preserve `preview.png`, `vision-ground-truth.pdf`, `vision-ground-truth.png`, `vision-proof.pdf`. |
| `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-pr72-acceptance-bfe9e6a` | Detached HEAD `8e069951d58379f8c15c67168b8c84315bd38ddc` | Reference checkout for draft integration PR #72; revalidate before using. |
| `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-pr81-linux-check` | Detached HEAD `3e823d03770a91fea187509f4c1d3a817886f892` | Older Linux dependency/test checkout. Its results do not cover the current candidate. |

Run `git worktree list --porcelain` for the full inventory. In the active worktree, remote `pc` points to **Xyst-Ai-Agent-PC**; `origin` points to **Xyst-Ai-Agent**, a different repository. Use the correct remote explicitly.

Original controlling briefs:

- Detailed specification, 456 lines: `C:\Users\hicks.GAMERX\.codex\attachments\0c1ebea4-133c-4ed6-b0ea-2e48ebd3aaee\pasted-text-1.txt`; SHA-256 `F5A6E185B2E189C6CC041055F86FCA3177C9CB84372983A6CEB44EE2BD8A328E`.
- Ordered build sequence, 150 lines: `C:\Users\hicks.GAMERX\.codex\attachments\7be7e25b-acf2-4385-8ee2-74c3f831138a\pasted-text-1.txt`; SHA-256 `398D60554F1D427F152D1135BFF26E04F9EF97133EF9EB49BA105CCBE0EA9AC0`.

## What exists

The TypeScript/Node runtime is shared by a CLI and Electron/React Control Room. Implemented areas include persistent sessions and missions, SQLite migrations, scheduling and service support, delegated task graphs, model selection and LM Studio integration, Hugging Face discovery/downloads, browser/public API tools, GitHub tools, document generation/reading, memory, configuration/access profiles, process tools, recovery controls, and Windows/Linux package candidates. The actual desktop development build has previously been shown.

Rendered PNG pixels now reach vision models through `office_preview`; this has bounded live PDF evidence. Public API retrieval, scheduled polling, selected coding/document tasks, and a two-model mission have live evidence. The two-model run showed overlapping requests and persisted task completion; it does not prove sustained reliability or simultaneous GPU execution.

Many automated Windows/Linux checks and package smoke checks passed at specific commits. Packaging evidence includes selected clean-install, renderer, CLI/desktop shared-state, and service cases. Current packages remain unsigned and unpublished. Prior WSLg and Ubuntu KVM results do not establish bare-metal Linux or all supported distributions.

## What this continuation found and changed

### Windows process timeout

The original roughly 57-second test stall required manual termination and remains an invalid cancellation acceptance run. Its exact trigger is still unexplained.

PR #81 bounds a hanging Windows `taskkill` helper, falls back to terminating the direct child, and explicitly reports uncertainty about descendants. Repeated unattended timeout tests and full suites passed. A controlled helper that slept for 60 seconds caused rejection after about 5.3 seconds without manual termination. That verifies the bound and uncertainty report; it does not prove descendant cleanup when the helper hangs.

### Stale command replay after external edits

A live JSONL coding session exposed a real product gap: a test file was corrected outside Xyst, but a resumed identical `run_process` returned the earlier failed test output without a new execution row. The replay hash uses a revision derived from Xyst-recorded mutations, so external edits did not invalidate it. An independent 7/7 test run passed, but the resumed Xyst session never reached accepted completion; this mission remains a non-pass. The initial model completion also missed two contract defects and must not count as acceptance.

Requirement C2 was downgraded to partial and that audit correction was pushed in PR #81 at `db45ae8`.

### Uncommitted command rerun candidate

Current source changes implement an explicit, one-use authorization for a completed `run_process` command. SQLite transactions retain the old action, record a new running intent, move the replay head, and consume the authorization once. While the new head is running or uncertain, lookups do not replay the old result. Authorizing a rerun invalidates the old result as completion evidence. Reconciled retries of a tracked head also preserve prior records.

The candidate adds:

- `agent authorize-rerun <session-id> <tool-call-id>` and a desktop tool-log button with command details and a side-effect confirmation.
- `agent recover-rerun <session-id> <tool-call-id>` plus a desktop abandoned-rerun review action. Each claimed rerun records its owner hostname and PID. Recovery requires that PID to be absent on the same host, then marks the action uncertain. It does not authorize another execution or establish whether descendants had effects.
- Migration 16 tables `command_replay_heads`, `command_rerun_authorizations`, and `command_rerun_owners`; session-deletion handling; completion-evidence checks; transaction, concurrent-process, crash and external-edit tests.

Code files modified: `apps/cli/src/index.ts`, `apps/desktop/src/main.ts`, `apps/desktop/src/renderer.tsx`, `packages/runtime/src/agent.ts`, `packages/runtime/src/runtime.test.ts`, `packages/runtime/src/storage.ts`. New untracked code: `packages/runtime/src/command-rerun.ts`. `docs/missions.md`, verification history and this handoff also contain local documentation changes. **None of this rerun candidate has been committed or pushed.** Do not treat PR #81's green checks as covering it.

## Latest verification and known limits

- Last full Windows runtime invocation completed normally: **211 tests, 209 passed, 2 skipped, 0 failed, 40.49 seconds**. Command: `npm test -w @xyst/runtime` after building runtime. No manual child termination. Skips: Windows symlink privilege and missing LibreOffice.
- Rerun tests include two actual Node processes contesting one authorization, one winner, an abrupt owner-process exit after durable claim, refusal to recover a live owner, explicit quarantine after owner exit, retained old evidence, and an AgentRunner external-edit rerun.
- Runtime build and CLI/desktop typechecks passed during implementation. A desktop production build passed before the latest owner-recovery changes; rebuild current source before treating the desktop candidate as built.
- Preserve failures: an incorrect direct invocation of `dist/runtime.test.js` failed 8 tests (source-relative child imports and migration fixtures). The subsequent correct npm invocation failed 3 migration fixture tests because the new owner table had not been dropped when constructing older-schema fixtures. The fixtures were corrected, all three focused tests passed, then the full npm suite passed as above. Neither failed run is an acceptance pass.
- CLI authorization has a disposable-profile probe, but the complete rerun/recovery flow has no live model-backed CLI or interactive desktop acceptance yet. Earlier desktop Computer Use attempts failed to activate the window; no mission ran, so those attempts are not desktop acceptance.

Read-only review identified concrete unfinished items:

1. Desktop can quarantine a rerun but has no session reconciliation control for the resulting uncertain action. CLI `agent reconcile <session> <call> completed|retry` exists. Complete the desktop path before claiming parity.
2. PID reuse conservatively blocks recovery, but the current error says the owner is alive and to stop it. An unrelated process may have reused the PID. Improve that wording and review host/process identity guarantees; never infer descendants stopped.
3. `agent.ts` now conditions completion updates on `status='running'` but does not inspect the update count before emitting `tool.completed` and saving output. Review and test ownership-change/recovery races so an obsolete runner cannot report completion after losing authority.
4. Review migration 16 against the actual integration base; historical/legacy replay, retry reconciliation, shared history deletion and evidence invalidation need final review. Candidate-created disposable version-16 databases predate the owner table; do not reuse them blindly after editing an unpublished migration.
5. Command rerun does not automatically detect external edits or refresh every earlier file-read result. Broader stale-evidence behavior remains open under C2.

## GitHub state and merge boundary

At handoff, remote PC `main` is `4340570af85ae80996452358cf200d1a2f0d6601`. PRs **#61–#81 are open**, with **#72 draft**. PRs #58–#60 were authorized and already merged. No merge authorization for these later open PRs was granted.

Current stack:

```text
main -> #77 -> #78 -> #79 -> #80
                         -> #81
```

#80 and #81 are siblings. Heads: #77 `00bbaa2`, #78 `8f9a1a1`, #79 `0fecfd4`, #80 `ee1746d`, #81 `db45ae8`. #81 was freshly confirmed open/unmerged with all eight Windows/Linux push/PR checks successful. #77–#80 had eight passing checks at their last recorded inspection; recheck current heads before integration or merge. #72 is a separate integration draft at `8e06995`, and includes additional integration work/mission evidence; inspect its diff rather than assuming it represents every later PR.

The rerun feature is separate from #81's timeout fix. Choose deliberate branch/PR placement after review; do not blindly push this unfinished feature onto the timeout PR. Local integration and preparation can proceed before requesting final merge approval. Present the exact reviewed PRs/commits and current checks when asking for authorization.

## Fastest route to completion

1. **Finish this concrete correctness gap.** Safeguard the local diff, address the review findings above, and complete one real saved-session flow: initial test failure -> external edit -> explicit authorization -> fresh execution row -> current successful test output -> accepted completion. Then exercise owner crash -> uncertain quarantine -> user reconciliation -> resume. Use both CLI and the actual desktop. Keep fixtures/profiles outside repository source; do not manually repair model outputs and count that as a model pass.
2. **Create one integrated candidate.** Inventory each open PR's unique changes and dependencies, compare with draft #72, and combine accepted work in an isolated `codex/` integration branch. Resolve migration/source/test conflicts explicitly. Avoid duplicate cherry-picks or broad workspace copies. Build/test the combined candidate on Windows and Linux and inspect hosted checks for its actual commit. Obtain the appropriate authorization before merging the open PRs; review post-merge workflows too.
3. **Drive acceptance through real user workflows.** Use fixed independent acceptance criteria for representative coding, delegated coding, issue-to-PR/review, research/API, and document-create/inspect missions. Include pause/cancel/resume and restart cases. Inspect output files, actual command execution, saved evidence and GUI state. Record every failed attempt and repair the product when evidence reveals a defect. Repeat sufficiently to evaluate sustained behavior, rather than accumulating more trivial one-off passes.
4. **Close the original requirement matrix.** Work through A1–G3 in `brief-traceability.md` against both briefs. Remaining categories include model lifecycle/resource contention and long sessions; broader document/visual cases; settings/access effects; integration permissions/auth/rate limits/cancellation; memory quality; process/recovery edge cases; Windows logon/reboot and native Linux behavior; native screen reader/physical keyboard/accessibility acceptance; documentation accuracy. Some existing implementations need acceptance, others may need fixes. Do not infer completion from test count.
5. **Complete production delivery.** Prepare final install/update/rollback checks on the integrated version. Signing certificate/secrets and a production update host are not configured; acquire owner configuration only when the concrete release candidate and instructions are ready. Verify signed Windows artifacts, Linux packages, metadata/checksums and a real published update path. Signed production release cannot be claimed without this evidence.

Use parallel agents only for independent, clearly scoped work with separate edits/workspaces; keep one owner for integration and evidence. Stop repeating checks after they pass unless a new change or concrete concern warrants rerunning them. Keep checkpoint and verification history current after meaningful milestones.

Useful commands from the active PC worktree (Node >=22 required):

```powershell
git status --short
git diff --check
git diff
git worktree list --porcelain
npm run build -w @xyst/runtime
npm test -w @xyst/runtime
npm run typecheck
npm run desktop:build
gh pr view 81 --repo iExyst/Xyst-Ai-Agent-PC --json headRefOid,state,mergedAt,statusCheckRollup
```

Use the source/npm runtime test command, not `node --test packages/runtime/dist/runtime.test.js`: some tests intentionally launch source-relative TypeScript fixtures. Full root `npm test` additionally runs CLI and release tests when the integrated candidate is ready.

## Completion standard and working preferences

The old statement “four of 32 groups verified” is stale after C2 was downgraded. The current audit explicitly labels three groups verified (A1, E5, F4), each with bounded evidence. Neither count is a reliable completion percentage. No defensible overall percentage is available.

Do not declare the project complete until every original requirement has adequate current evidence. Missing credentials, signing or physical platform access must remain explicit blockers; they do not become passes. Earlier failed model runs remain failures.

The user wants concise progress updates, persistence toward a usable integrated application, and context compaction around 50% usage. No manual compaction trigger or enforceable context threshold is exposed to this agent; do not promise one. Keep this handoff/checkpoint current and minimize repeated tool output. The last runtime test session has now exited successfully; no test wait from that invocation remains pending.

## Command-rerun review follow-up · 2026-10-29

The three concrete correctness findings in the earlier handoff review are now addressed in the uncommitted candidate: the desktop session tool log provides explicit reconciliation choices for quarantined reruns; the recovery error accounts for PID reuse; and a tool result is not accepted or emitted as completed if its durable `running` row was changed before finalization. Runtime/CLI/desktop typechecks passed. The current Windows runtime suite passed 211 tests (209 passed, 2 host-specific skips, 0 failures). The earlier failed attempts remain recorded above. There is no dedicated completion-race regression yet, and the candidate remains uncommitted/unpushed with no exact-head hosted checks. Live resumed CLI and desktop acceptance after external edits and owner crash, Linux validation, and final migration/legacy replay review are still required.

## Completion ownership race regression · 2026-10-29

A deterministic runtime regression now forces recovery to mark a command uncertain while its process is still executing. The stale runner rejects with `EXTERNAL_ACTION_UNCERTAIN`, does not overwrite the uncertain row, and emits no `tool.completed`. The focused case passed; full Windows runtime suite is now 212 tests (210 passed, 2 host-specific skips, 0 failures); runtime/CLI/desktop typechecks and `git diff --check` pass. This remains uncommitted local evidence. Live CLI/desktop recovery acceptance, Linux validation, hosted checks and integration branch placement are still open.

## Live CLI external-edit rerun acceptance · 2026-10-29

A real saved Xyst CLI session using Qwen 3.5 9B in disposable profile `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-rerun-acceptance-profile` passed the command-rerun flow. The initial call returned `BEFORE_EDIT` and completed verified. After external modification of `probe.js`, the user explicitly authorized a one-use rerun; the resumed session executed the identical command under a new tool-call ID, returned `AFTER_EXTERNAL_EDIT`, preserved the original call record, and completed verified against the fresh result. Session ID `70836d19-1d55-4c36-b0fc-2328d2dbebf3`. This is bounded CLI evidence only. Owner-crash live recovery, desktop acceptance, Linux validation, hosted checks, and integration remain outstanding.

Owner-crash acceptance was attempted after the successful external-edit rerun, but failed before crash injection: authorizing a second rerun of the new completed call returned `Only a completed process action in this session can be authorized for rerun`. No owner was terminated, no quarantined call was created, and no pass is counted. Investigate stored row eligibility/replay authorization lineage before resuming this flow. Preserve this failed attempt in `verification-history.md`.

Correction: the rejected authorization targeted the original call, not the current replay head; it was a test-harness targeting error, not a product defect. Authorization of the current head succeeded. The subsequent background launcher/poller failed to start a new agent turn because of quoting errors and did not reach crash injection. No owner was killed. Owner-crash acceptance remains open; see verification history for exact details.

Live owner-crash flow then failed before child start with `FOREIGN KEY constraint failed` after current-head authorization succeeded. SQLite inspection showed no new run_process call or rerun owner, no side-effect file, and no FK-check violations; no process was killed. This is a candidate failure, not acceptance. Diagnose authorization consumption across the changed workspace hash and add regression coverage before retry. Details in `verification-history.md`.

## Repeated external-edit reruns · 2026-10-29

The deterministic AgentRunner external-edit regression now passes two sequential authorize/rerun cycles with three distinct process-call IDs and outputs `before`, `after`, and `after second external edit`; prior records are retained. Full current Windows runtime suite is 212 tests (210 passed, 2 host-specific skips, 0 failed), and runtime/CLI/desktop typechecks pass. This narrows the unresolved live failure: the real model-backed session later returned `FOREIGN KEY constraint failed` before adding a new action, despite the deterministic repeated-rerun path passing. Preserve the failed live attempt; do not count owner-crash acceptance. Exact scope in `verification-history.md`.

Root-cause correction for the live CLI FK failure: SQLite trace shows it occurs in `SessionStore.addMessage` while inserting the next user message, because the expected parent session row is absent in the isolated profile. It happens before rerun authorization consumption/tool-call execution. The session existed and was listed earlier, but disappeared before this resume; cause unknown. This is not evidence of a rerun FK defect. No child ran or owner was terminated. See verification history.

Latest live owner-crash injection: the resumed CLI created running rerun `Rv5haths1B4nAYcffpy1YNlpfO9VwtOp`, with owner PID 30628 and replay head pointing to it. That exact PID was force-terminated. CLI recovery then rejected the call as not a running rerun head, while an immediate SQLite read still showed it running and headed. No quarantine/reconciliation pass. Preserve as failed acceptance and diagnose CLI eligibility/database scope. Do not repeat injection until understood.

Follow-up: the recovery helper itself was then invoked directly from worktree source against that same isolated profile/call and successfully marked the abandoned rerun `uncertain` after the owner PID was gone; delayed side-effect file remained absent after its expected timer. This is helper/API-level evidence only. It does not clear the CLI eligibility query discrepancy and is not a CLI or desktop owner-crash acceptance. See verification history.

A fresh synthetic fixture in disposable profile `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-rerun-cli-recovery-fixture` confirms `agent recover-rerun` transitions a valid head-linked running process with absent owner PID to `uncertain`. This is CLI fixture evidence only; it does not resolve the real-session CLI lookup discrepancy or satisfy model-backed owner-crash acceptance. Details in verification history.

Desktop current-source build passed, including runtime/CLI build, desktop typechecks, Electron main/preload compile, and Vite renderer. The isolated-profile Control Room opened and accessibility state showed runtime ready/Qwen 3.5 9B/one saved session. Computer Use could not interact: activation failed, state refresh recovered once, then indexed click failed with `coordinate input geometry is unavailable`. Stopped per skill guidance; no saved session opened and no desktop task ran. This is neither product pass nor product failure. See verification history.

C2 traceability was updated to distinguish the bounded rerun successes from still-open stale file-read evidence, real owner-crash recovery, and desktop interactive acceptance. The row now remains explicitly **Partial**.

## Full root-suite verification · 2026-10-29

The exact root command `npm test` passed on the current candidate: runtime 212 tests (210 pass, 2 host skips), CLI 28/28, release verification 5/5. This expands local Windows evidence beyond the runtime-only suite. Hosted exact-head checks and Linux validation are absent, and real owner-crash CLI acceptance remains failed/unresolved. See verification history.

## Branch placement update · 2026-10-29

Candidate is now on local `codex/command-rerun-recovery`, based on exact PR #81 head `db45ae8b995482213a58e36e08182625f5f8e8d6` (PR #81 remains open and targets `codex/restricted-path-fail-closed`). Full tracked diff and untracked source/handoff were snapshotted under `%TEMP%\xyst-command-rerun-snapshot`. All source/doc changes remain uncommitted; nothing is pushed and no PR is created. The feature is intentionally stacked on #81 pending resolution of live owner-crash/CLI recovery discrepancy.

The synthetic CLI recovery fixture now also covers explicit reconciliation: `agent recover-rerun` quarantined a second head-linked row to `uncertain`; `agent reconcile ... retry` returned retry authorization, and SQLite showed `retry_allowed`. This is fixture-only; no agent command was retried, and it does not clear the real-session CLI discrepancy.

Session-deletion coverage was strengthened: after claiming a rerun, deleting its owning session removes its replay authorization, head, owner row, and tool-call records; shared-scope replay history still blocks deletion. Focused test, workspace typechecks, and `git diff --check` passed. This does not resolve live owner-crash recovery.

## Reconciliation-path retry regression · 2026-10-29

- Updated `command rerun authorization is consumed once and preserves the original action` to drive `uncertain` → `retry_allowed` through `AgentRunner.reconcileExternalAction`, the shared reconciliation path used by interactive clients, rather than directly mutating the DB to authorize retry. The focused test passed: 1 passed, 0 failed. This deterministic test does not resolve the separate real-session CLI owner-crash lookup failure or desktop interaction gap.

- Follow-up verification: `npm run typecheck` passed for runtime build and CLI/runtime/desktop typechecks; `git diff --check` passed. CLI inspection confirms recovery delegates directly to `AgentRunner.recoverAbandonedProcessRerun`; the user-visible rejection text originates from the transactional row lookup in `quarantineAbandonedCommandRerun` (requires same-session external `run_process`, owner row, replay-head link, and `running` status). The earlier live SQLite observation did not preserve a query transcript proving all these predicates simultaneously, so the discrepancy remains unlocalized.

## Real resumed CLI after owner crash: retry timed out after effect (Windows, 2026-10-29)

- Read-only inspection confirmed the crashed call `Rv5haths1B4nAYcffpy1YNlpfO9VwtOp` was a `run_process` external action in the expected session with its owner row and replay-head link. Its persisted status was already `uncertain` after the earlier direct quarantine; that snapshot cannot establish why the earlier CLI recovery command rejected it while it was still running.
- Fresh disposable CLI owner-crash fixture passed: with the actual built CLI and isolated APPDATA, `agent recover-rerun` returned `uncertain` and SQLite confirmed the status transition.
- For the real saved session, inspected `probe.js`: it waits 30 seconds, appends `done` to `crash-side-effect.log`, then writes `CRASH_CHILD_FINISHED`. Before retry reconciliation the file was absent and no matching child process was found. CLI `agent reconcile ... retry` succeeded. A real `agent resume` caused the model to issue the same run_process again, but the run_process 30,000 ms timeout raced the script's 30-second completion: CLI returned `EXTERNAL_ACTION_UNCERTAIN` and tool call `aEquV5a0GJViT7usDBOHHmHjR74YXxox` persisted as uncertain with `Process timed out after 30000 ms`. Subsequent inspection found the file containing `done` and no live probe process, proving the external effect occurred although stdout was lost. This failed model-backed attempt remains recorded as failed/uncertain; do not repeat it. Reconcile this new call as completed from the file evidence and retain this timeout as a distinct acceptance failure. This does not explain the separate historical unattended Windows timeout stall.

## Real authorized owner-crash recovery and CLI resume (Windows, 2026-10-29)

- In a fresh real Qwen3.5 9B CLI session, completed `run_process` call `jULbhQ8iEEi9ycYVnCCjHWwm47j0ouAh` produced `OWNER_CRASH_FRESH_OK`; persisted `task_complete` was `verified:true`. After externally editing the same script (same executable, args, and cwd) to a 15-second marker probe, CLI `agent authorize-rerun` issued one-use authorization `9c732afe-8f32-41ec-bc3f-6a4eef85fd18`.
- Resumed the saved session and waited until authorized rerun `CDn8bsmKzxneuvX9xqzeLzd4TlfR4XRm` was durably `running` with owner PID 9380. Terminated that exact CLI owner. After waiting beyond the probe delay, inspected the isolated workspace and found no marker file or matching live process; the recorded tool call remained running with its owner row.
- Invoked the actual built CLI: `agent recover-rerun <session> <call>` returned `uncertain`. Inspected the no-effect evidence, then `agent reconcile ... retry` returned `retry`. A real `agent resume` issued retry call `A3xvFKFntZforam2LyoFdLIIjk1v3cI4`, which completed with exit code 0, stdout `OWNER_CRASH_REAL_RERUN_OK`, and 15,054 ms duration. Persisted `task_complete` `CNCzBeobQfCQxGqStmFaqQlvl0NsNruN` returned `verified:true` against that fresh call. This is bounded Windows CLI acceptance after an owner crash, not sustained reliability evidence.
- Other attempts in this sequence remain failures in the saved acceptance session history: a 30-second probe hit the 30,000 ms timeout while its side effect completed (then reconciled completed); a completed-session follow-up did not issue a call and ended unverified; one longer probe was syntactically invalid due a literal `\\n`, and a follow-up on a completed session did not issue the authorized rerun. The earlier real-session `recover-rerun` lookup rejection is still unexplained; do not remove these failed attempts from reliability records.
- Desktop interaction remains unverified. The handoff and checkpoint should continue to mark C2 partial.

- Full candidate verification after the retry-reconciliation regression and documentation update: root `npm test` passed on Windows: runtime 212 tests (210 passed, 2 host-specific skips), CLI 28/28, release verification 5/5. The existing runtime timeout test also passed; this does not negate the real 30-second acceptance timeout above. `npm run typecheck` passed after the code/test change; `git diff --check` passed. No hosted exact-candidate checks or Linux run exist.

## Integration status correction · 2026-10-29

The local integration branch has advanced beyond this handoff's earlier branch-placement entries. `codex/integrate-pr72-through-command-rerun` combines draft #72 with the unique changes from #73–#76 and #80 (including their #77–#79 dependencies) and the command-rerun candidate from #81; the rerun tables use schema migration 17 because integrated #72 already owns migration 16. This integration is local only; no remote branch/PR has been created and no later PR has been merged.

The actual CLI owner-crash acceptance later in this handoff passed: an owner was killed while a rerun was running, the CLI quarantined it, retry was explicitly reconciled after checking for no effect, and resumed execution produced fresh output with `task_complete verified=true`. Retain all recorded failed attempts and the unresolved earlier lookup discrepancy. Desktop reconciliation remains unverified.

Integrated Windows checks: root `npm test` runtime 243 (240 pass, 3 host-specific skips, 0 fail), CLI 29/29, release verification 5/5; `npm run typecheck` passed; `npm run desktop:build` passed. No exact combined-head hosted checks or Linux run yet. See the final `verification-history.md` entry for scope and remaining blockers.

## Integrated candidate verification addendum · 2026-10-29

The committed local candidate `be276c7` also passed on WSL Ubuntu 24.04 with Node 24.22.0: runtime 243 (236 pass, 7 platform skips, 0 failures), CLI 29/29, release verification 5/5, typecheck, and desktop build. Published PR heads #72–#81 each had eight passing Windows/Ubuntu verify/package checks at the latest read-only inspection; none covers `be276c7`. A renewed desktop UI attempt did not reach Xyst controls: Computer Use screenshots showed an unrelated app despite the Xyst AX window name, and an Electron error dialog reported EPIPE. Keep desktop reconciliation acceptance open.

## Review candidate update · draft PR #82

The integrated branch is published as draft [PR #82](https://github.com/iExyst/Xyst-Ai-Agent-PC/pull/82), targeting `main`, exact head `c256176af9c5fe9529326d5e1fa3a2dd07bcf1d3`. PR #72, #73–#81 remain open; #82 consolidates #72 with unique #73–#76/#80 changes and command-rerun recovery. Exact-head GitHub push and pull-request workflows passed Windows/Ubuntu verification and Linux/Windows package jobs (8/8); local Windows and WSL Linux suites/typechecks/desktop builds passed. #82 is a draft and was not merged. It is ready for human review; outstanding project requirements and C2 gaps remain. See latest verification-history addendum and PR body.

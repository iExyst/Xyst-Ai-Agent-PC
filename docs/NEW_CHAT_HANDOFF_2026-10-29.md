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

## Desktop recovery and review closure addendum · 2026-10-29

The old “What this continuation found” review list predates integration and subsequent fixes. Current source state: (1) the desktop tool-log path now has quarantine plus explicit completed/no-effect reconciliation controls; bounded real-renderer use is recorded below; (2) same-host PID existence still fails closed, and the message now explains possible PID reuse instead of asserting that the command owner is alive; (3) tool completion checks that exactly one `running` row was updated before emitting `tool.completed` or saving the result, with a regression proving a recovery quarantine cannot be overwritten. Integration-schema/session deletion/retry lineage fixes and external-edit read-hash revalidation are also covered by the local tests. The wider command rerun feature remains partial: external edits are not automatically discovered for every action/evidence type, and owner identity cannot establish descendant state.

A fresh Desktop acceptance completed in session `4d37189f-f1dd-461d-a95f-447504dc56aa`: initial `run_process` `mEwKBQmCsTyBXwOsoQh9LNNh1EzFU9HG` and `task_complete verified:true`; external edit of `phase3.txt`; one-use UI authorization; live rerun owner PID 31440 verified by host and command line, terminated; absent side-effect marker; renderer reload; UI recovery to `uncertain`; inspected no-effect confirmation and retry; resumed call `uAcsawrSe2hS1n1ifDirxuvYPfceNj5k` returned `OWNER_CRASH_DESKTOP_RETRY_OK`; `task_complete MkCEUrJ2AFfxmp6Kn7kge1aBIXBaRUDF verified:true`. This is one bounded CDP-controlled actual renderer flow. At the first inspection the active task was still locked; a renderer reload preceded its recovery controls, so that run alone did not establish whether reload was necessary. A second fresh session `e958ca26-07b9-4222-9356-dfcf4d2adf91` showed natural recovery without reload: after owner PID 53620 was killed and its guarded child exited, the renderer cleared `Agent task active` and enabled Continue within about 12 seconds; the tool-log `Review abandoned rerun` button was enabled at the later refresh. That session then quarantined the call, confirmed no effect, retried as `8jvJ7jHXOc10sC4LQeMt0EoEcJv1ZrIY` (`OWNER_CRASH_RETRY_4_OK`), and completed verified as `BTZqGsgBQwNjSWHZmwLeTnHwls1uLZn5`. A stale tool-log selection initially showed the prior probe3 command before the probe4 session data finished loading; SQLite confirmed no probe4 authorization from that click, then refreshing the session and authorizing the displayed probe4 input succeeded. Preserve this UI/harness timing and all prior failed attempts in `docs/verification-history.md`. The natural unlock is bounded evidence, not sustained recovery evidence; characterize the transient delay further.

A separate real Desktop smoke exposed a completed-run IPC leak: CLI child `21336` remained alive after verified `task_complete`, leaving the renderer locked. Current uncommitted fix disconnects the parent IPC channel in the CLI `agent run`/`agent resume` `finally` block. Real smoke session `826092a6-f7a0-49aa-885f-670f9d1b263d` passed after the fix; the child exited and active task indicator cleared. Root Windows tests: runtime 244 (241 pass, 3 platform skips), CLI 31/31, release 5/5; workspace typecheck and desktop build passed. This uncommitted candidate is on PR #82's integration branch but is not covered by the last hosted checks on `e4ad4c3`.

Next: publish this source/doc candidate to draft PR #82, wait for exact-head hosted checks, characterize the Desktop transient lock-clear delay and continue representative acceptance against both briefs. Keep #82 draft; do not merge. The historical real CLI recovery-selector discrepancy, original unattended Windows timeout stall, sustained acceptance, and signed production delivery remain unresolved.

## Continuation addendum · ordinary command output evidence · 2026-10-29

- Current PC worktree branch is `codex/integrate-pr72-through-command-rerun`, source commit `ada91f74e955f76161400d17ea51e1f3e84b2eeb`. `pc` targets Xyst-Ai-Agent-PC; `origin` is the separate repository. PR #82 remains open/draft and must remain unmerged without explicit authorization.
- Reproduced a task-completion false positive: an objective explicitly required stdout exactly `EXPECTED`; a successful `run_process` printing `WRONG` was accepted. Added inference for quoted stdout exact/equality/match and include/contain assertions, enforced against successful exit-0 process results both during evidence mapping and final criterion validation. Requirements remain active when `run_process` is excluded from allowed tools. Regressions prove wrong-output rejection, exact and substring matching, and fail-closed behavior without the process tool.
- Verification: focused regressions passed; final Windows root `npm test` passed runtime 246 (243 passed, 3 platform skips), CLI 31/31, release 5/5; `npm run typecheck`; `npm run desktop:build`; `git diff --check`. No Linux/hosted checks cover this edit yet.
- Source/test commit `ada91f7` was pushed to draft PR #82. All eight exact-head hosted jobs passed: push CI `40867681683`, PR CI `40867690009`, push Desktop packages `40867681671`, PR Desktop packages `40867690004`. Next: record this evidence in the final PR/checkpoint, keep #82 draft, and continue the requirement audit while preserving recorded failures. Do not claim overall completion.

### Exact-head verification follow-up · 2026-10-29

PR #82 head `8808d3bdc57ee04d3a421b5aa7d2e03ee9cf8914` passed all eight hosted push/PR Windows/Ubuntu verification and Linux/Windows packaging jobs. Windows package checks include clean-install renderer and live mission-service lifecycle smoke. Run IDs: CI `40857551670`, `40857561063`; packages `40857551863`, `40857561032`. PR #82 remains OPEN/DRAFT, merge state CLEAN; no merge was performed. See `docs/verification-history.md` for the bounded Desktop owner-crash recovery and IPC fix.

### Desktop crash-recovery attempt 5 · 2026-10-29

A fresh isolated Control Room session `6df47868-36ac-41b4-8cd0-daf5742d23a0` ran `owner-crash-probe5.cjs` as baseline call `1N41GSNCbtjHsWpATgFyDyMEvBvC5g9l`, returned `OWNER_CRASH_BASELINE_5_OK`, and completed `task_complete verified:true` as `JJNeYXmjXYZ0Tsh773ANqCRrWYZClPsZ`. After externally editing `phase5.txt`, the attempt stopped at UI authorization: the CDP renderer stopped answering evaluations after the one-use authorization control was clicked; SQLite showed no authorization and no rerun, and no process was terminated. Computer Use listed six Electron windows sharing the same `@xyst/desktop` title, so no ambiguous target was activated or manipulated. This is an incomplete harness attempt, not owner-crash acceptance. Preserve the successful baseline and failed setup separately; prior bounded no-reload recovery remains the only repeat proving natural unlock.

## Continuation addendum · stale directory-list evidence · 2026-10-29

- In the active PC worktree, completion verification now reruns selected `file_list` evidence using the original persisted input through the access-checked tool, then compares normalized path/type entries. An out-of-band create is rejected; the deterministic regression then obtains a fresh listing and completes. This covers directory-membership changes, not modifications to file contents when the listing remains identical.
- Windows checks on this local source passed: root `npm test` runtime 247 (244 pass, 3 skips), CLI 31/31, release 5/5; typecheck, desktop build, focused regression, and `git diff --check`. No Linux or exact-head hosted checks cover this diff yet. Source/test and documentation edits are local and uncommitted. Continue by reviewing, publishing to the existing draft PR #82 under the previously authorized workflow, and checking all exact-head jobs. Do not merge. The project remains incomplete and the recorded failures/gaps remain active.
- Exact source-head hosted checks passed 8/8 at `63145d7b4850e21f81750bd60b5c85254c85370f`: push CI `40870371193`, PR CI `40870382286`, push Desktop packages `40870371195`, PR Desktop packages `40870382293`. Windows/Ubuntu verification and Linux/Windows packaging passed; Windows smoke checks passed. PR #82 remains OPEN/DRAFT, with no merge. The documentation update itself will trigger another exact-head check set.

## Continuation addendum · stale office-preview evidence · 2026-10-29

- The next C2 gap was stale visual evidence: `office_preview` records SHA-256 for its source document and rendered PNG, but completion previously did not verify either artifact remained unchanged. Completion now reauthorizes and rehashes both, failing closed on unverifiable state and requiring a fresh render after edits.
- A deterministic AgentRunner regression generates a PDF, externally replaces it after preview, verifies completion rejection, renders again, externally alters the preview PNG, verifies another rejection, and completes only after a fresh final render. This targets visual claims after source/raster mutation; broader live visual acceptance remains open.
- Current local Windows checks passed: runtime 248 (245 pass, 3 skips), CLI 31/31, release 5/5; typecheck, desktop build, `git diff --check`. This source/doc diff is local and uncommitted at this point; it has no Linux or hosted exact-head checks. The existing draft PR #82 remains unmerged. Next: review the full diff, publish the tested change to the same draft branch, verify all exact-head jobs, and continue requirement-by-requirement acceptance. No completion claim.
- Source commit `1ff5fe061f20984e079b6b7eb487cef4d9d44050` passed all eight exact-head hosted checks: push CI `40872765993`, PR CI `40872773589`, push packages `40872766010`, PR packages `40872773492`. Windows/Ubuntu verification and Linux/Windows packaging passed, including Windows clean-install renderer and mission-service smoke. PR #82 remains OPEN/DRAFT with no merge. The following documentation-only commit must receive its own current-head checks.


## Continuation addendum · remote observation invalidation · 2026-10-29

The saved state in the opening section is historical. Current active PC worktree is `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-pr81-review`, branch `codex/integrate-pr72-through-command-rerun`, head `ef919b8970a9e7c20f1d600748b70daa15977d0b`; before this addendum it was clean. PR #82 is OPEN/DRAFT targeting `main`, CLEAN, unmerged. `pc` points to Xyst-Ai-Agent-PC and `origin` to a different repository. The command-rerun implementation is already part of this integrated branch; do not restore or copy the old uncommitted snapshot described earlier.

- New C2 change: selected GitHub observations become stale after a later completed GitHub mutation in the same repository; browser observations become stale after later browser state actions. Regressions prove same-repository GitHub invalidation, preservation of unrelated repository evidence, and browser snapshot invalidation after click. Changes are local/uncommitted and are not covered by the current hosted checks for `ef919b8`.
- Verification: focused regressions 2/2; root `npm test` passed runtime 250 (247 passed, 3 host-specific skips), CLI 31/31, release verification 5/5; root `npm run typecheck` and `npm run desktop:build` passed; `git diff --check` passed. Do not describe this as live acceptance of GitHub/browser behavior.
- Next: review the full diff, update C2 and verification history, commit/push to the existing draft PR #82, and verify all exact-head hosted jobs. Keep PR draft and unmerged. Preserve the unresolved CLI recovery-selector discrepancy and original unattended Windows timeout stall. Continue the brief matrix and real sustained acceptance; no overall completion claim.

## Exact-head check update · 2026-10-29

The remote observation invalidation source commit `333eda4491dba3fd1eb236158e67702e61f87890` passed all eight hosted checks: push CI `40876186180`, PR CI `40876196850`, push packages `40876186184`, and PR packages `40876196855`. Windows/Ubuntu verification and Linux/Windows packages passed, including Windows install/renderer and mission-service smoke. PR #82 remains OPEN/DRAFT, CLEAN and unmerged. A docs follow-up is being prepared and will require its own exact-head checks. The broader C2, sustained acceptance, CLI selector, Windows timeout, accessibility/platform, and signed release gaps remain open.

## Continuation addendum · completion-time refresh for GitHub/browser reads · 2026-10-29

The current remote branch was at docs commit `959ab99a7c7d60709dac60f3593d8db97e7bc92b` before this local edit; the active worktree is now dirty with the source/test/documentation change below. PR #82 is OPEN/DRAFT, CLEAN, unmerged at the published head until the next push.

- Selected GitHub observations and browser snapshots/tab lists are re-run through the current tool context at completion. The provider-redacted result must exactly match its persisted evidence; out-of-band changes, unavailable read tools, access/network/browser errors, malformed inputs, and results reaching 30,000 characters fail closed and ask for a fresh or narrower query. Existing invalidation after later in-Xyst mutations remains.
- Two new AgentRunner regressions simulate out-of-band GitHub issue-title and browser-page text changes, require a fresh read, then complete successfully. Four focused remote evidence regressions passed 4/4.
- Full Windows root suite passed runtime 252 (249 passed, 3 host-specific skips), CLI 31/31, release verification 5/5; root typecheck, Desktop build, and `git diff --check` passed. Exact hosted checks on `959ab99` do not cover this diff.
- Next: review full source/doc diff, commit and push to existing draft PR #82, wait for exact-head checks, update PR description and checkpoint. Do not merge. Continue C2/other matrix gaps and sustained acceptance; do not declare overall completion.
## Continuation update · web evidence freshness · 2026-10-29

The published PR #82 head remains `79f9111e080058bd345186ea1ad2a29babba241c`, OPEN/DRAFT and unmerged; the integration worktree is now locally modified with an additional C2 slice. `task_complete` re-executes cited `web_fetch` and `web_search` calls against their saved inputs, compares all redacted semantic output except the changing `retrieved_at` timestamp, and fails closed on changes, malformed output, refresh errors, unavailable tools, or oversized output. Truncated/unsuccessful page fetches are rejected before refresh. One selected observation is refreshed only once even if it satisfies multiple criteria. Three added dynamic AgentRunner regressions and two related truncated-fetch cases pass (5/5). Windows root `npm test` passes runtime 255 (252 pass, 3 host skips), CLI 31/31, release 5/5; typecheck, desktop build, and `git diff --check` pass. These changes are not committed or hosted-verified. Next: inspect the entire diff, commit and push to existing draft PR #82 under prior authorization, then await exact-head hosted Windows/Ubuntu and Linux/Windows package checks. Do not merge. Continue representative end-to-end acceptance and the remaining two-brief audit; C2 and the project remain partial.

## Continuation update · live research exposed missing answer · 2026-10-29

Published PR #82 head `bff81656435f2d1e764dd46083f7627bd90730d6` passed all eight exact-head hosted checks: verify push `40880955571`, verify PR `40880967295`, package push `40880955367`, package PR `40880967284`. During isolated real CLI acceptance, Qwen3.5 9B correctly fetched the public repository API and persisted `task_complete verified:true` but returned only generic completion text, omitting the requested description/default branch. This is an acceptance failure preserved in `verification-history.md`, session `10d7d8a1-9325-4780-a19d-22f8a9629aae`. The local follow-up adds a response-presence guard for web-research/report requests and preserves answer text after verified completion. Deterministic coverage passed; a second real run on rebuilt source produced “description is null” and branch `main`, with a failed empty completion followed by a successful verified completion (session `dde62b64-2085-41cc-9266-cfacf13773a6`). This does not semantically validate every answer claim or establish sustained reliability. Current local Windows `npm test` passes runtime 256 (253 passed, 3 skipped), CLI 31/31, release 5/5; typecheck, desktop build, and diff check pass. Answer-guard source/tests are uncommitted after `bff8165`; no hosted check covers them. Next: inspect full diff, commit/push to PR #82, update PR description, verify exact-head hosted checks, then continue representative workflows and brief audit. Do not merge.

## Continuation addendum · live Qwen3.5 9B coding task and saved-session resume · 2026-10-29

- Active integration source remains unchanged at `ca9c06d5fe93a60b95ea330b9501f7e792600af4`; PR #82 is still open/draft/unmerged with its eight exact-head checks green. A new Windows CLI acceptance used a fresh isolated profile/workspace under `%TEMP%\xyst-live-c1-byte-format` and did not modify the repository or original workspace.
- Initial model-backed session `70704b6b-a65c-457e-bacb-3ba05edbfb92` attempted a byte-size formatter and `node:test` suite. Preserve three failing test attempts (7/10, 8/10, 9/10) and the 18-turn `limit` result with no completion. After the agent hit the limit, an independent test command passed 10/10, but that is not a model pass.
- Resumed the same saved CLI session. Qwen3.5 9B ran the exact requested tests to 10/10, read both final files, and persisted `task_complete verified:true` (`IbFyCNuavKxP9dB5o4IiJHMO0gYr1zZ2`, `dS6QHqqEPoxo07RMZ2A5CJF18J7J0K9O`, `ioIV36VQSkrv1Axq8ZOCvamscWaAhSrV`, completion `SeZi3kK8M1tE6vgBU2nMxbOr5hQZxCGe`). A prior completion citation attempt using hashes/content rather than evidence IDs was rejected and remains a failure. This is bounded resumed acceptance after substantial self-correction, not first-pass or sustained reliability; details and individual failures are in `docs/verification-history.md`.
- The first invocation took about 24 minutes to reach the turn limit; resume took about four minutes. LM Studio showed the 9B instance generating while CPU remained below all-core saturation on the GPU-backed local backend. Do not infer general performance from this single run. C1 remains partial. No merge; next: publish the reviewed documentation update to draft PR #82, verify all eight exact-head jobs, then continue representative acceptance and the two-brief matrix.

## Continuation update · native desktop authorization result · 2026-10-29

- Active worktree remains at `39febba8cfd1c14449e98f0f85037ec1f7c16118` on `codex/integrate-pr72-through-command-rerun`; PR #82 is OPEN/DRAFT, CLEAN, and unmerged at that head. The new verification-history, traceability, and handoff addenda are local and uncommitted; no source changed and no exact-head checks cover these doc edits.
- In the isolated packaged session `a17f32d5-7a88-4637-80ee-9648adf9752b`, native selection of current replay head `6XOwEnG2yxlKc80OrEAAloSbyvj3OCxv` and confirmation successfully persisted pending authorization `91c91a27-4a2a-42f4-b562-80663a368db0`. The composer could not be populated through Computer Use, so no desktop resume, new tool call, owner termination, or owner-crash recovery occurred. The background watcher was stopped before it detected a candidate; the original marker was restored. Details are in `verification-history.md`.
- The installed `release/win-unpacked` package remains older than the saved-transcript renderer fix at `228bf89` and displays `[object Object]`. Do not count it as current-renderer acceptance. Use a fresh disposable profile for the next native UI attempt or explicitly handle the pending test authorization.
- Continue with the remaining current-source desktop acceptance and the two-brief requirement audit. Keep C2 partial; preserve all failed model/tool attempts and the unexplained timeout stall. PR #82 stays unmerged without explicit authorization; signing and production update hosting remain release prerequisites.

## Continuation update · MCP read-name spoofing and migration 18 · 2026-10-29

- Review found arbitrary MCP server tools were classified as built-in reads by suffix (`_read_resource`, etc.), allowing a server-defined action such as `mcp_docs_append_read_resource` to avoid external-action approval and be completion-refreshed as if it were a read. MCP setup now returns the exact built-in observation-tool names; only those are exempted/refreshed. A denied fake suffix action is covered by AgentRunner regression.
- Added migration 18 to convert ambiguous still-running legacy `mcp_*` rows to external/uncertain and fill execution scope. Completed legacy history is unchanged. Dynamic server-defined MCP tool outputs still are not safely re-executable at completion, so C2 remains partial.
- Local Windows verification: focused MCP regressions 5/5; runtime 266 (263 pass, 3 platform skips, 0 fail); CLI 31/31; renderer 4/4; release 5/5; root typecheck; desktop production build; diff check. Source and documentation are uncommitted at this checkpoint and have no Linux or exact-head hosted checks.
- Next: inspect full candidate diff, commit/push to draft PR #82 under the existing preparation workflow, record exact-head checks, and continue representative acceptance. Keep PR open/draft and unmerged; preserve recorded model failures and the unexplained Windows timeout stall.

### Exact-head result · MCP classification fix

Commit `f13a59bec7ed3e3cb6fd3057256fc26216ca35e0` passed all eight exact-head jobs: push CI `40937582459`, PR CI `40937592354`, push Desktop packages `40937582475`, PR Desktop packages `40937592289`. Windows/Ubuntu verification and Linux/Windows packaging passed; Windows clean-install renderer and live mission-service lifecycle smoke passed. The PR #82 body now reflects `f13a59b`, its exact checks and remaining acceptance gaps. PR #82 remains OPEN/DRAFT, CLEAN, and unmerged. No source or docs edits are local after this result except this append-only handoff update, which must be included in a later docs commit with its own exact-head checks.

## Continuation · operator-declared read-only MCP tools · 2026-10-29

- Added bounded `read_only_tools` to MCP stdio/HTTP/SSE server configuration. Names must be unique, must fit `allowed_tools` when that list exists, and must resolve to an actually exposed discovered tool; setup fails closed otherwise. Only exact explicitly configured dynamic tools receive the read-observation classification and completion-time refresh. This is an operator trust assertion; a dishonest or misconfigured server may still mutate state, so the setting is only for side-effect-free tools. All other dynamic server tools remain external actions.
- Deterministic AgentRunner coverage simulates an out-of-band change to a dynamic lookup, rejects stale completion, and completes only after a fresh result; its approval callback also ensures the configured observation is not treated as an external action. Discovery/config tests cover suffix-spoofing resistance, unknown names, duplicate names and `allowed_tools` consistency.
- Local Windows verification: focused dynamic/discovery tests 2/2; root `npm test` runtime 266 (263 pass, 3 platform skips), CLI 31/31, renderer 4/4, release checks 5/5; root typecheck, desktop production build and diff check all pass. An initial compile caught two issues (exact optional types in the helper signature and a missing `callTool` in a fake client); both were fixed before the passing run. This does not provide live remote MCP semantics acceptance.
- Source and docs are locally modified on `codex/integrate-pr72-through-command-rerun` at `a4fce54c55d856c62129ec75f39b17b906b00465`; PR #82 is OPEN/DRAFT/CLEAN/unmerged, and no current hosted checks cover these edits. Next: commit and push to #82, capture exact-head hosted check IDs/results and update PR details/checkpoint. Continue the remaining two-brief acceptance, unresolved Windows timeout and CLI selector investigations, native UI/accessibility and platform checks, and release-hosting prerequisites. Do not merge or declare project completion.

## Exact-head result · operator-declared read-only MCP tools · 2026-10-29

Commit `c8232b6eefccf5982f4b7c4d1d4454a93f742182` passed all eight hosted jobs: push Cross-platform CI `40940576251`, PR Cross-platform CI `40940587443`, push Desktop packages `40940576287`, and PR Desktop packages `40940587520`. Both Windows and Ubuntu verification passed; Linux and Windows packages passed. Both Windows package jobs passed clean-install renderer and live mission-service lifecycle smoke. Updated PR #82's description to identify `c8232b6`, current local evidence, exact run IDs, the explicit operator trust boundary, and the remaining project requirements. PR #82 remains OPEN/DRAFT/CLEAN and unmerged. The local source/doc changes in `c8232b6` are published and exact-head verified; this handoff result addendum itself is local and needs inclusion in a follow-up docs commit/check set. Continue the requirements audit; don't merge or declare overall completion.

## Live MCP read-only refresh acceptance · DeepWiki · 2026-10-29

Against source head `c8232b6`, an isolated live AgentRunner connected to `https://mcp.deepwiki.com/mcp`, discovered `read_wiki_structure`, and configured only that exact tool under both `allowed_tools` and `read_only_tools`. The real `facebook/react` query ran twice (initial observation and completion refresh), persisted with `effect='read'` and `status='completed'`, and `task_complete` persisted `verified:true`. Session `5b477b45-56a3-42c9-9b0a-24b29ed44cd1` used a deterministic provider fixture without a live model, credentials, or writes; its temporary SQLite profile was removed. This is one transport/tool-specific live read check, not proof of server semantics, live-model reliability, or general MCP acceptance. Full details are in `docs/verification-history.md`.

## Continuation update · current-source native desktop composer · 2026-10-29

Built the published `d276e22` source as a Windows package at `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-desktop-native-acceptance-20261029`, launched against isolated profile `xyst-native-ui-profile-20261029` and workspace `xyst-native-ui-workspace-20261029`. Window id `4264062` was uniquely matched to this package executable, not the existing installed package. Its Control Room rendered after an initial blank observation and showed runtime ready/connected to LM Studio. This package startup observation does not prove session execution.

A disposable `phase.txt` containing `phase-A` was created in the isolated workspace. The native task composer could not be populated: `set_value` timed out; after fresh window observation and clicking the visible composer, `type_text` returned with the field still empty. No session, model request, process execution, rerun, owner crash, or recovery was produced, and no model attempt is counted. See the matching verification-history entry. The isolated profile/workspace remain for diagnosis. Continue native UI and accessibility acceptance; earlier bounded CDP evidence remains distinct. Do not claim this attempt as acceptance.

## CLI external-edit rerun continuation · 2026-10-29

Current-source CLI session `d73d8904-17fd-4453-8d7f-d994652aa4d0` in isolated profile/workspace ran a successful exact-output baseline (`phase-A`) and persisted `task_complete verified:true`. The first `node -p` probe was rejected because it emitted a trailing newline; a later corrected run emitted exact stdout. After external edit to `phase-B`, one-use rerun authorization `99ce111e-46a8-450a-b4e7-82a3847ad7b8` was consumed exactly once. Rerun `ywBlFXcpcASw5h6Ec3rWOeBdenS6qDEa` returned exact `phase-B`, with matching fresh read `OCPI7zi3e1MJk1d67rQQX708jmXMoIl3`. Completion correctly rejected this output because the session retained the original exact `phase-A` criterion. The resumed run ended at turn limit after invalid evidence-ID and generation-length failures; it is not a completed external-edit session. The authoritative detailed record, including the compaction-summary discrepancy, is in `docs/verification-history.md`. Keep the CLI selector mismatch, original Windows timeout stall, native desktop input limitation, and broad acceptance requirements open.

## Follow-up · value-independent CLI rerun session · 2026-10-29

A second isolated Qwen3.5 9B CLI session with a value-independent criterion completed a verified `phase-A` baseline. After external edit to `phase-B`, one-use rerun authorization was consumed; its new process call (`FVKa3HBbl6p5ft5n2qn1zd9601tTcs7q`, `is_rerun=1`) and matching fresh `file_read` (`c2gPx7ce2VJFza0YIGFjbcv8z8l3mTZ5`) both showed phase-B. The model returned an empty completion before calling `task_complete`. Finalization-only Qwen3.5 9B and Gemma 4 E2B continuations each hit LM Studio HTTP 500. These persisted outputs verify rerun execution and matching evidence, but no post-edit task completion; keep this attempt as incomplete. Full record: `docs/verification-history.md`.

## Compaction mutable-state safeguard · 2026-10-29

A live CLI session showed a saved compaction could say `phase.txt contains phase-A` after a later authorized rerun had read `phase-B`. The resume wrapper now states its exact message cutoff and warns that mutable observations may be stale, and the AgentRunner instructs the model to refresh mutable state before treating it as current or using it for completion. The compactor also supplies message timestamps and requires temporal qualification of mutable observations. Focused tests passed 3/3 after correcting a stale header assertion; final root `npm test` passed runtime 266 (263 passed, 3 platform skips), CLI 31/31, renderer 4/4, release checks 5/5; root typecheck and diff check passed. No live-model acceptance of the updated compaction behavior is claimed. See `docs/verification-history.md`.

## Evidence reconciliation · rerun and owner-crash status · 2026-10-29

Later entries in docs/verification-history.md supersede the earlier “no live owner-crash acceptance” statements above:

- A real Windows CLI session completed an external-edit authorized rerun with task_complete verified:true (session 70836d19-1d55-4c36-b0fc-2328d2dbebf3, recorded around line 2327). A separate real CLI session completed owner termination, uncertain quarantine, explicit retry reconciliation, resumed execution, and verified completion (around line 2433). This does not erase the earlier selector rejection or the 30-second timeout-after-effect failure.
- Two bounded CDP-driven Windows Control Room renderer sessions completed owner-crash recovery and verified retry completion (around lines 2479 and 2494). The second observed the renderer unlock without reload. This does not establish native Computer Use, screen-reader/physical-keyboard acceptance, or sustained reliability.
- A later repeat harness attempt stopped before authorization and remains incomplete (around line 2501); native Computer Use composer attempts also remain incomplete (around line 2685 and the current-source attempt near the end of this file).
- The current-source native composer attempt did not submit a task. Do not count it as a desktop session acceptance. The traceability row’s bounded CDP renderer evidence and the separate native UI gap are both accurate.

PR #82 remains open/draft/unmerged. Its current head and checks must be revalidated before further integration. The unexplained original Windows timeout stall, historical CLI selector discrepancy, broad representative mission reliability, platform/accessibility coverage, and signed production delivery remain open.

## Continuation · current-source native input fallback · 2026-10-29

The isolated package built from `f288e73950bb0e7d64979ac45d75321876e0e3a2` rendered the Control Room with runtime ready and its composer focused. Two `type_text` attempts left the field empty; a clipboard-paste fallback also left it empty after an unknown paste/refresh outcome. The clipboard was restored and the isolated package was closed after fresh enumeration confirmed the field remained empty. No task/session/model/tool/process was started. This repeats the native input harness limitation; it does not demonstrate a product pass or failure and is not a model attempt. Full details are in `docs/verification-history.md`.

## Continuation · user-requested model resource policy · 2026-10-29

The user asked the agent to avoid unnecessary model loads after observing duplicate resident models. Automatic preparation now refreshes live inventory, coordinates loads across local Xyst CLI/desktop workers with a per-server lock, and reuses a compatible instance. It refuses to automatically add a duplicate merely for larger context; compaction or an explicit reload is required. Cross-process HTTP-fixture and agent regressions pass, as do full local tests, typecheck, desktop build, and diff check. Live LM Studio reuse preserved the existing instance IDs without a new load. Global idle unloading was enabled with a 600-second timeout. This source candidate still needs hosted verification and rebuilding of older packages. Keep the user's operating preference: inspect inventory before any acceptance-work model load, reuse adequate loaded models, and avoid speculative/extra test model instances. Do not unload another application's active model merely to simplify a test.

## Continuation · duplicate model inventory rows · 2026-10-29

A live inventory read returned two identical rows for qwen/qwen3.5-9b while no model was loaded. A later-row-only loaded-instance fixture reproduced an unnecessary duplicate load despite the existing cross-process lock (two loads rather than one). Model lookup now merges/deduplicates instance records from same-key/type rows in agent preparation, locked loading, and post-load verification. Focused tests passed 4/4; root tests passed runtime 268 (265 pass, 3 skips), CLI 31/31, renderer 4/4, release 5/5; typecheck/build/diff check passed. No real inference or model load occurred. Preserve the reproduced failure and do not infer it explains the historical freezes. Publish/recheck the candidate, rebuild the Windows package, then resume representative real workflows using only a needed model and releasing any owned test instance afterward.

## Continuation · real coding failure and shared-server constraint · 2026-10-29

The latest live CLI duration-parser run (session 69b512f4-e95b-408f-87d6-fc74ae50dc9a, isolated %TEMP%\xyst-live-duration-97c97f9) remains failed: model edits improved the fixed verifier from 4/25 to 23/25, but fractional/unsafe totals still passed incorrectly and history outgrew the one 14,848-token instance. The new policy refused a duplicate; no verified completion exists. The fixed tests were unchanged. Details and tool IDs are appended to verification history. Next, investigate compaction/continuation within the resident context rather than adding another instance or treating this as a pass.

The user clarified that another computer uses the same LM Studio server. Treat model instances as shared. Do not infer exclusive ownership from local process state or an initially empty local inventory. Automatic unloading on this PC is now globally disabled, superseding the earlier 600-second cleanup policy. A test-created instance had already been unloaded before this clarification; remote impact is unknown. No further model unload/reconfiguration should occur without coordination. The filesystem load lock covers only processes on this PC. Continue independent code/verification work while respecting this constraint.

## Continuation · automatic resident-context compaction candidate · 2026-10-29

The agent now tries a bounded persisted compaction on a resident local model before failing for context growth. Tool-only histories can end their compacted prefix after complete paired tool results; raw history, recent turns, images and incomplete pairs remain protected. The chosen instance's context bounds the full summary prompt. Agent context is rebuilt/re-estimated afterward, including saved-session resume. There are at most three automatic attempts per run and no model lifecycle calls on the compaction path. Final focused tests passed 8/8; full root tests before the final added test passed runtime 271 (268 pass, 3 skips), CLI 31/31, renderer 4/4, release 5/5; typecheck/build/diff check passed. Preserve the initial test-helper compile failure. Publish/recheck this candidate, then validate actual continuation without changing the shared server's instances. No new live-model acceptance is claimed. The previous 23/25 task and original Windows timeout/selector failures remain failures.

## Continuation · live compaction succeeded, coding continuation failed · 2026-10-29

Source 2d3f1fb2b468958f29d92b30e0cb52db90d82129 passed all eight hosted jobs (push CI 40965642956, PR CI 40965651250, push packages 40965642978, PR packages 40965651509). The local Windows package at %TEMP%\xyst-model-reuse-20261029 was rebuilt; both bundled runtime copies match final compiled hashes.

The saved duration-parser CLI session successfully compacted from estimated 15,176 to 11,046 tokens on one resident 14,848-token Qwen instance, then resumed tools. It remains a failed coding acceptance: tests reached 24/25, regressed to 17/25 after another model patch, and LM Studio's engine fetch error stopped inference. A separate normal retry also failed with HTTP 400 engine fetch failure during compaction; its guard stopped without a duplicate instance. Exact IDs/log paths are in verification history. No task_complete verified:true, no human fixture repair, verifier unchanged, independent final tests 17/25. Investigate the provider/engine connection failure before more blind retries; do not count bounded compaction as completed coding work.

The user clarified that the other computer need not be considered for this test. Fresh inventory was empty, one required Qwen instance was loaded, and it was released only after both CLI attempts ended. No loaded IDs remained afterward. This temporary coordination does not authorize permanent changes for other clients; global automatic unloading stays off. No active test inference handle remains. PR #82 stays draft/unmerged; retain broader native UI, platform, long-duration reliability and signed-release gaps.

## Current continuation · exact source and live limits · 2026-10-29

The previous log entries are historical. The active PC worktree is now on `codex/integrate-pr72-through-command-rerun` at `2d3f1fb2b468958f29d92b30e0cb52db90d82129`, with new local process-output, bounded compaction, and user-continuation changes. The ordinary runtime test command now includes their regression files. Current local Windows checks passed after the final assertion update: runtime 277 (274 pass, 3 skipped), CLI 31/31, renderer 4/4, release verification 5/5, root typecheck, desktop production build, and diff check. The exact candidate has no hosted checks yet. PR #82 is still OPEN/DRAFT at the previous published head and remains unmerged.

The new compaction continuation targets the earlier LM Studio missing-user-query template error, but is deterministic-only so far. Process-output handling has bounded capture (8 MiB per stream), a 2,000-character inline preview, redacted workspace artifacts for captured large outputs, and explicit loss reporting when capture itself exceeds its cap. Truncated stdout can no longer prove exact or substring output assertions. These changes still need hosted exact-head verification and representative live acceptance.

The user clarified that the other computer need not be considered now. No server model, auto-unload setting, server process, or scheduled task changed during this verification. The earlier live coding/provider attempts and original timeout stall remain failures; passing local checks do not resolve them. Do not claim overall completion or merge without fresh authorization.

## Exact-head result · bounded output and compaction continuation · 2026-10-29

The current source commit `2a755aacb1e910b36aa3fab0a10ad446a6604595` passed all eight exact-head hosted jobs: push CI `40968242850`, PR CI `40968247049`, push packages `40968242602`, PR packages `40968247044`. Windows and Ubuntu CI passed; Linux and Windows packaging passed, including the Windows clean-install renderer and live mission-service lifecycle smoke. Local Windows root tests/typecheck/desktop build/diff check passed at this source. PR #82 is OPEN/DRAFT/CLEAN/unmerged; its body identifies the exact source and checks. This handoff addendum is local until a docs-only follow-up is published and verified.

Current user clarification: the other computer need not be considered now. No LM Studio state or model policy changed. Latest read-only inventory showed one loaded Qwen3.5 9B instance at context 8192. The new compaction continuation has not had live-model validation. The previous live coding/provider errors and original Windows timeout stall remain failures; broader requirements and release prerequisites remain open.

## Continuation · full-history recovery, 250K context, and exact completion · 2026-10-30

Active PC worktree remains `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-pr81-review`, branch `codex/integrate-pr72-through-command-rerun`, base/head `fb8ed6ee537802e1e1bc12223ae6310bf4cfdadd` before the current local edits. Preserve its full diff. `pc` targets Xyst-Ai-Agent-PC; `origin` targets the other repository. PR #82 remains the existing draft integration PR; do not merge. The original workspace has only append-only checkpoint updates; PC Sync and its four untracked vision artifacts remain untouched.

Current local source candidate restores the complete persisted compaction summary, historical compacted tool-call journal, and every retained raw user message to inference context. It increases bounded automatic compaction from three to six passes, improves summary output budgeting/diagnostics and wrapped-JSON handling, and fixes an evidence-inference bug where `run exactly node --test ...` following a filename was treated as exact file contents. Regressions cover multi-pass fitting, full history preservation, and that live completion failure. Root `npm test` passed runtime 285 (282 passed, 3 skipped), CLI 31/31, renderer 4/4, release 5/5; typecheck, desktop production build, and diff check passed. Current changes are still local and are not covered by PR #82's prior checks.

The real isolated duration-parser CLI task `69b512f4-e95b-408f-87d6-fc74ae50dc9a` is now complete: exact `node --test acceptance.test.cjs` passed 25/25; fixed test SHA remains `b72175ae4771af93a0bd71c5274fc14facf096f4c53225f19538f12104a8d222`; final model-backed `task_complete` call `TWIoIvd4lC7pNtaUSTFC8Axl8EAZo3cx` returned `verified:true`. Preserve prior 22/25 and 24/25 test failures, file-tool mistakes, and two failed task-completion attempts as history; the final pass does not erase them.

Current LM Studio inventory contains one Qwen3.5 9B instance at context 250112, parallel 4, `offload_kv_cache_to_gpu=false`; no additional model was loaded. It uses system RAM for KV cache. Latest resource read: RTX 5080 11.03 GB used / 5.73 GB free, system RAM 53.74 GB available. Full-GPU estimate at 250K was 19.31 GiB, above VRAM. A real resumed request with a 40,367-token prompt completed in about 33 seconds; this single result is not sustained latency or freeze-resistance evidence. The user prefers maximum useful context without a severe speed penalty, durable context across sessions, and multiple models only when genuinely needed. Reassess only from measured throughput and live headroom; do not add duplicate models.

Next: review all source and doc diffs; commit and push only to `pc` on the existing integration branch under the existing PR-preparation authorization; poll all eight exact-head CI/package checks; update PR #82's draft description and append the exact outcomes to this handoff, traceability, verification history, and the original checkpoint. Do not merge. Then continue the requirement-by-requirement audit, representative end-to-end CLI/Desktop acceptance, the unexplained historical Windows timeout, native desktop/accessibility/platform gaps, and signed production release/update hosting. The full two-brief goal remains incomplete.

## Exact-head follow-up · `37bdc9b` · 2026-10-30

Correction to earlier continuation text: commit `37bdc9b2c6aaed9e445fae03a08b95052d4a8443` is now published on the existing `pc/codex/integrate-pr72-through-command-rerun` branch, not local-only. PR #82 body has been refreshed and remains OPEN/DRAFT/unmerged. Eight exact-head checks passed: PR CI `40977691239`, push CI `40977697395`, PR Desktop packages `40977691246`, push Desktop packages `40977697271` (Windows/Ubuntu tests and Linux/Windows packaging). The first push Windows package run failed when silent installer `spawnSync` hit its 180-second timeout. The same-head PR job passed clean install and lifecycle smoke; a targeted rerun of failed push job 123809696634 completed successfully under job 123813026900, including renderer and service smoke. Preserve the timeout; cause is unresolved. Exact outcomes are in `docs/verification-history.md` and PR #82 body. Docs-only follow-up from this commit remains to be published and checked.

Local source validation at `37bdc9b`: runtime 285 (282 passed, 3 skipped), CLI 31/31, renderer 4/4, release 5/5, typecheck, desktop production build and diff check passed. Live resumed duration task had verified completion for its fixed 25 tests, with earlier failed attempts retained. One 40,367-token prompt took about 33 seconds with 250112 context and RAM-backed KV; one sample only. Keep the user's preference for large persistent context and efficient model use; do not infer a sustained speed profile from it.

Next: commit/push this exact-head documentation addendum only to `pc`, collect exact-head checks for that docs commit, and refresh PR #82's body to the docs head. Then continue the two-brief acceptance matrix. Do not merge or claim completion; Windows timeout, sustained CLI/Desktop acceptance, native desktop/accessibility, broader platform coverage, and production signing/hosting gaps remain open.

## Documentation-head verification · `5b212b0` · 2026-10-30

The previous addendum's “docs-only follow-up ... remains to be published and checked” is superseded: docs commit `5b212b0328752105495f6eaefc8cc8f7e8b972fd` is published and passed all eight exact-head jobs: push CI `40979362369`, PR CI `40979365230`, push Desktop packages `40979362398`, and PR Desktop packages `40979365449`. Windows and Ubuntu CI, Linux/Windows packaging, installer/update metadata, clean-install renderer, and live service lifecycle passed. These checks cover the documentation head and its source ancestor `37bdc9b`. The first `37bdc9b` push Windows installer spawn timeout remains recorded despite the later successful isolated rerun. PR #82 remains OPEN/DRAFT/unmerged.

This note is an additional documentation-only follow-up; exact-head checks for its resulting commit must be recorded in the append-only original resume checkpoint. Continue the open acceptance matrix without merging or claiming overall completion.

## Current-build native desktop attempt · window handle unavailable · 2026-10-30

Tried a native UI acceptance using the source-current packaged executable and fresh isolated `--user-data-dir`/`XYST_ROOT` at temp root `C:\Users\HICKS~1.GAM\AppData\Local\Temp\xyst-desktop-cua-2b2f50bfd745`. Created disposable marker and one-test fixture with hashes recorded in verification history. The Computer Use app list returned stale current-package window id 1641128; `get_window` rejected it and reported id 1443588, but a fresh `list_windows()` did not include that window. Stopped per skill recovery rules. No UI task, model request, or test ran; fixture hashes stayed unchanged. Isolated PID 54884 and its children were stopped and follow-up showed no remaining matching process. This is a helper/discovery limitation, not product pass/failure. Desktop composer, accessibility and saved-session acceptance remain open.

## Integration head `a8290a8` verified · 2026-10-30

The active PC worktree is clean on `codex/integrate-pr72-through-command-rerun`, pushed only to remote `pc`, at `a8290a81a87ce756b93bb17852db681715a3ecfb`. PR #82 targets `main` and remains OPEN/DRAFT/unmerged. The integration includes PR #72's #58–#71 stack, unique #73–#76/#80 work and dependencies #77–#79, plus #81 command-rerun recovery. Exact-head CI and package checks passed on both Windows and Ubuntu (all eight jobs; IDs in `verification-history.md`).

The latest native Computer Use attempt could not target the isolated current-build window and ran no task. Do not count it as desktop acceptance or a product failure. Next, continue requirement-level review and representative workflows while preserving recorded model/timeout failures; pursue native UI/accessibility only with a working supported harness. Signed production release and update hosting still require owner credentials/hosting. No merge is authorized by this evidence; the two-brief objective remains incomplete.
## RAM-backed context admission follow-up · local candidate · 2026-10-30

Active worktree now has a local source/docs diff after `ac00b69`. Automatic local selection can use RAM-backed KV only when no sampled model fits free VRAM, LM Studio reports a total estimate within half of current free RAM / one quarter of installed RAM / 32 GiB, and automatic unloading is disabled. The applied load setting must be confirmed before inference. Mission-assigned automatic models run the same estimate guard. Existing adequate loaded instances and manual model assignments are not changed. This does not reconfigure LM Studio or guarantee a speed level; the estimate does not provide a precise CPU/GPU split.

Local Windows runtime suite: 286 tests (283 pass, 3 skips, 0 fail); root typecheck and desktop production build pass; `git diff --check` pass. No live model or server state was changed. This candidate has not yet received hosted exact-head checks or a live performance test. Continue by reviewing and publishing the precise diff to the existing draft PR #82 branch on remote `pc`, waiting for every exact-head check, then continue the two-brief acceptance gaps. Do not merge or claim completion.
## Exact-head verification · RAM-backed context candidate · 296347d · 2026-10-30

Commit `296347ddd83881c3aad9ba2b2a6a84ecc88c9ed5` passed all eight exact-head Windows/Ubuntu CI and Linux/Windows packaging checks. Both Windows CI/package paths passed, including Windows installer/update metadata, clean-install renderer, and mission-service lifecycle smoke. Exact IDs: push CI `40984404084`, PR CI `40984411524`, push packages `40984404100`, PR packages `40984411275`; job IDs are listed in `docs/verification-history.md`. PR #82 remains OPEN/DRAFT/unmerged. The RAM estimate remains only a guardrail, not proof of a specific speed or sustained freeze resistance.
## RAM-KV mission-path regression · 2026-10-30

The previously noted gap in dedicated coverage for mission-assigned RAM-backed KV is closed in the current local candidate. A test verifies the automatic mission-assignment estimate path requests RAM KV and fails closed before inference if LM Studio omits the applied setting. Runtime: 287 (284 pass, 3 skip), root typecheck passed. No live server/model mutation. This candidate is not yet published or hosted-checked.

## Exact-head mission guard and current CLI live acceptance · 2026-10-30

The preceding “not yet published” line is superseded: commit `228ce6a36cb9294f6a21f53bda8281ed728b8678` passed all eight exact-head CI/package jobs and PR #82 remains OPEN/DRAFT/unmerged. A fresh native UI targetability recheck again found Xyst IDs in `list_apps()` but no Xyst window in fresh `list_windows()`; no stale handle, live profile, or composer was touched. The attempt and precise limits are in the append-only original checkpoint.

Built runtime and CLI from `228ce6a`, then ran a fresh, read-only Windows CLI acceptance using the already-resident Qwen3.5 9B at 250112 context. Session `37411f83-d327-4f70-93dd-b45f0fa5c2d2` read the exact marker and persisted `task_complete verified:true`; it took about 16.5 seconds from run start with 5,728 prompt tokens on the first provider call. `lms ps` before/after showed the same four idle instances and no queued work; no instance or LM Studio setting changed. This is one short-task live sample, not sustained speed or freeze-resistance evidence. Preserve the prior wrong-CWD setup failure and malformed first completion call recorded in `verification-history.md`.

Next: continue the requirement-by-requirement audit and representative model/platform acceptance. Native desktop composer/accessibility remains open because the supported UI helper cannot target the current window. The original Windows timeout, sustained model reliability, broader bare-metal/platform coverage, signing and production update hosting remain open. No merge is authorized; the overall goal remains incomplete.

## Automatic model-choice failure and resumed 9B recovery · 2026-10-30

Follow-up in isolated profile `xyst-live-auto-reuse-228ce6a` disabled automatic loading while leaving selection on. Xyst chose the already-loaded Qwen3 4B for a 6,226-token task and added no model, but the real session ended `unverified`: after about 92 seconds it reported completion in plain text instead of calling `task_complete`. A contemporaneous resource sample showed 48.1 GiB free RAM, 14% GPU utilization, and 5,615 MiB free VRAM. Resuming that same saved session explicitly on the already-loaded Qwen3.5 9B completed `task_complete verified:true` after one malformed completion call was rejected and corrected. The four LM Studio instances and their contexts remained unchanged. Exact call IDs, prompt tokens, and limits are in `verification-history.md`; do not count the 4B run as a pass or treat this one 9B recovery as broad reliability evidence.

This adds current-source CLI evidence for cross-model saved-session continuation and no-extra-load behavior. It also shows the fresh-profile selector can choose a resident model whose observed tiny task did not finish correctly. Gather representative repeated task/model measurements before altering ranking policy. Native desktop/accessibility, historical timeout, sustained reliability, platform acceptance, and production signing/hosting are still open; PR #82 remains draft/unmerged.

## Documentation head `bbb440b` verified · 2026-10-30

The docs-only commit `bbb440b8a6fa470faf1f9b996fc30d6ddde902bc` passed all eight exact-head Windows/Ubuntu CI and Linux/Windows package jobs. Push CI `40987805070`, PR CI `40987810206`, push Desktop packages `40987805106`, and PR Desktop packages `40987810171`; exact job IDs and installer/renderer/service step outcomes are in `verification-history.md`. The branch still integrates the accepted PC PR stack and remains PR #82 OPEN/DRAFT/unmerged. The next local change is another documentation update only; its exact-head checks will be separate. No merge.

## Context-aware mission admission · local unpublished diff · 2026-10-29

The active worktree remains `C:\Users\hicks.GAMERX\AppData\Local\Temp\xyst-pr81-review`, at published base `e3670525ace2e574e23c7815e0620ddd34c17390`, branch `codex/integrate-pr72-through-command-rerun`, with `pc` targeting the PC repository. Ten files are locally modified: mission/resource admission, AgentRunner refusal handling, regression coverage, and LM Studio/mission/traceability/history/handoff docs. The full diff was inspected before editing. This is separate from command-rerun code already present in the published integration.

The current local change replaces the mission scheduler's fixed 8 GiB automatic RAM ceiling with a shared conservative budget (minimum of 50% free RAM, 25% installed RAM, and 32 GiB), supplies context-specific LM Studio total-memory estimates alongside GPU estimates, and uses the total estimate for cold-model admission. Automatic selection and mission assignment fail before load/inference when available estimates prove GPU and host budgets cannot fit. Resident workers remain heuristic; estimates do not reserve resources from other apps. A first focused refusal test found the refusal swallowed by a generic fallback; rethrowing it fixed that regression. Local Windows root checks pass: runtime 291 (288 pass, 3 skipped), CLI 31/31, renderer 4/4, release 5/5, root typecheck, desktop production build, and diff check. No LM Studio state changed. Hosted exact-head checks are pending.

Next: review all source/test/doc changes, confirm guard and scheduler fallback semantics, append this evidence to the original checkpoint, then commit/push on the existing `pc` integration branch and collect all eight exact-head CI/package jobs. PR #82 stays OPEN/DRAFT/unmerged. Continue live resumed CLI and desktop owner-crash flows, model/resource contention and representative workflow acceptance, the original Windows timeout investigation, and the two-brief requirement matrix. Preserve model failures; do not mark complete or merge.

## Exact-head outcome · context-aware resource admission · `d044146` · 2026-10-29

The prior pending state is superseded: `d044146d18c86bd6384ac122bb1d0d46ea9ec52b` passed all eight exact-head jobs. Push CI `40990934126` (Windows `123852896649`, Ubuntu `123852896417`); PR CI `40990938728` (Windows `123852910678`, Ubuntu `123852910233`); push Desktop packages `40990934141` (Windows `123852896209`, Ubuntu `123852896547`); PR Desktop packages `40990938714` (Windows `123852910197`, Ubuntu `123852910040`). Both CI matrices and Linux/Windows package jobs succeeded, including Windows installer/update verification, clean-install renderer, mission-service lifecycle, and artifact upload. Ubuntu package annotations were runner-image migration notices only. PR #82 body is updated and attached; it remains OPEN/DRAFT/unmerged. The candidate's local test counts and the live-performance limits remain as documented above. Continue the unmet acceptance matrix; no merge or overall completion claim.

## Completion evidence cross-check · local unpublished fix · 2026-10-29

A live CLI task that required Node stdout to match the current `phase.txt` exposed a false positive: stdout contained a trailing newline that the file did not, the model acknowledged the mismatch, but citing only the process row still persisted `verified:true`. This is a failed acceptance. The local candidate now requires both a successful process result and an exact-path readback, compares stdout with current file text, and rejects truncated stdout. The regression and full runtime suite pass (292 tests: 289 passed, 3 skipped); runtime typecheck passes. These changes are not hosted-checked or live-rerun yet. See `verification-history.md`.

The user's current context preference is a 250K starting ceiling where the model and conservative memory estimates support it, while favoring the largest context that stays responsive. A context ceiling is capacity, not a requirement to fill every request; RAM-backed KV can make large contexts possible beyond VRAM but may reduce speed, and this host has only one 40K-token latency sample. Do not claim a measured slowdown threshold or infer freeze resistance from that sample.

## Exact-head checks · `f2d5e8f` · all successful · 2026-10-29

Source/test head `f2d5e8f1a180fdb2f34e94504848f4ad9930640d` passed all eight exact-head hosted jobs: push CI `40993604452` (Windows job `123861503988`, Ubuntu `123861504355`); PR CI `40993608995` (Windows `123861520162`, Ubuntu `123861519892`); push Desktop packages `40993604185` (Windows `123861503345`, Ubuntu `123861503594`); PR Desktop packages `40993608993` (Windows `123861519581`, Ubuntu `123861520020`). Both OS test matrices and package paths passed, including Windows installer/update verification, clean-install renderer, mission-service lifecycle, and artifact upload. Ubuntu package annotations were informational runner-image migration notices. PR #82 remains OPEN/DRAFT/unmerged. Current-source live rerun remains outstanding; preserve the false-positive run as a failure.

## Next-step continuity · stdout/file mismatch and context sizing

The earlier live candidate run's `task_complete verified:true` was a false positive because the runtime accepted only process evidence after stdout differed from file text. The source guard now fails closed, but must be rerun in a fresh/resumed CLI case on the published candidate and should verify both mismatch rejection and matching-value success. User preference: context capacity around 250K where supported and safely estimated, use the largest context that remains responsive, avoid extra model instances, and don't assume all available tokens must be loaded into VRAM or filled in every request. The one ~40K / ~33-second sample cannot quantify the speed penalty; live speed comparison remains open.

## Newest live mismatch attempt and local prompt correction · 2026-10-29

Supersedes the pending “fresh/resumed CLI” item above only for the specific newline-mismatch case. Corrected isolated session `9b1e833e-a0e7-4dc9-a474-3faa0d66eb36` ran successfully and preserved `phase.txt` (`phase-A`, SHA-256 `9b7628efe5df0331cb4b13ebcb35150989d9fb6c1920b8d1bc75d78c288e841d`); stdout was `phase-A\n`. The model named the mismatch. Runtime rejected its first completion attempt because the `file_read` preceded `run_process`; the fresh read after process was saved, but the initial six-turn run ended `limit` before the exact comparison branch. Resuming on the locally rebuilt runtime with existing evidence only ended `unverified` and did not repeat side effects. Preserve this as a non-pass, not as live verification of the equality guard.

Local source now sends at most one conditional completion reminder: it explicitly says to call `task_complete` only if every criterion passes and to report a contradiction without calling it. Regression coverage asserts a contradicted stdout/file criterion ends unverified without a completion call. Local Windows runtime (293 tests: 290 passed, 3 skipped), root typecheck, desktop build and diff check pass after this edit. This fix is local on branch `codex/integrate-pr72-through-command-rerun`; it is not yet hosted-checked. PR #82 is the deliberate placement because it is the existing OPEN/DRAFT integration candidate containing the completion-evidence guard. Do not merge.

Next: collect exact-head push/PR CI and Windows/Linux package checks for this source fix; run a fresh exact-match live acceptance (and a carefully ordered post-process mismatch run if needed) without duplicating process side effects; continue owner-crash/resume CLI and desktop acceptance, representative workflows, historical Windows timeout diagnosis, and both original brief matrices. The positive live match, native desktop, sustained reliability, signed release and production hosting remain open.

## Positive exact-match CLI outcome and two failed setups · 2026-10-29

The positive case is now complete on the local candidate: fresh isolated session `9acafdb2-3b3c-4238-ab27-9ee9ca420a71` called `run_process` once (success, stdout exactly `phase-A` without LF), then read `phase.txt` from the workspace root (exact same text/hash), then persisted `task_complete verified:true` from both tool results. Four runtime checks passed; run duration was about 37 seconds. This closes the positive live equality case for CLI only.

Keep both failed setup attempts in [`verification-history.md`](verification-history.md#live-exact-match-cli-acceptance-and-failed-setup-attempts--2026-10-29-local): one created an unnecessary script and launched two Node processes while resolving a nested path incorrectly; the next sent malformed `node -e` code and used that wrong path. They were cancelled and are not passes. The first attempt's generated script remains in its isolated temp workspace because environment policy rejected the cleanup command; it is not in the repository or user's source workspace.

The negative live mismatch still ends unverified: the first read was stale before process execution; a fresh read was saved, but the initial run reached its limit before exact comparison. The model's resumed final text correctly names the trailing LF mismatch but referenced the earlier read ID. Keep this as a non-pass; deterministic runtime coverage rejects the mismatch. Current conditional one-reminder code/test and docs are still local on the existing PR #82 branch; final-source runtime (293 tests: 290 passed, 3 skipped), root typecheck, desktop build and diff check pass. Next: push to PR #82 and wait for all eight exact-head checks. Then continue live post-process mismatch/reconciliation, external-edit and owner-crash CLI/desktop cases, representative end-to-end work, and remaining brief requirements. Do not merge or declare completion.

## Exact-head checks and real CLI external-edit resume · 2026-10-30

PR #82 source head `1294e067408a16e69714bb05d47bab922055aa02` passed all eight exact-head Windows/Ubuntu CI and Windows/Linux package jobs. Run IDs: push CI `40997298387`, PR CI `40997302678`, push Desktop packages `40997298458`, PR Desktop packages `40997302815`. Package scope included Windows installer/update metadata, clean-install renderer, mission-service lifecycle, and Linux AppImage/update verification. PR #82 remains OPEN/DRAFT/unmerged.

An isolated real CLI session `3a464bb9-754d-4be3-b475-5615eb9b2d15` completed a baseline command/read match on `before`; after externally editing `phase.txt` to `after`, `agent authorize-rerun` granted one use. Resume executed the identical command as a fresh rerun (`is_rerun:1`, stdout `after`), read the post-process file (`after`), rejected the model's stale pre-edit evidence, then persisted `task_complete verified:true` using only the fresh process/read calls. This is one bounded CLI external-edit/resume pass; retain the rejected call as a failure within that session. No model/server settings changed.

During the resumed invocation, a read-only session-list snapshot still showed the prior `completed` status while the CLI process was running; the status changed when the run finished. Check status semantics and any desktop consumer before deciding whether this is a product defect. Next: validate owner-crash recovery through CLI and native Desktop (prior Desktop crash cases are bounded; transient renderer unlock delay and harness limits remain), investigate this status observation, and continue representative workflows and both original briefs. The live mismatch case is still unverified, sustained reliability and the unexplained Windows timeout remain open, and signed production release/update hosting still needs external credentials and hosting. Keep PR #82 draft; do not merge or claim overall completion.

### CLI owner-crash follow-up · 2026-10-30 UTC

The current live attempt is a non-pass. See `verification-history.md` section “Live CLI rerun owner-crash attempt · 2026-10-30 UTC · non-pass” for the full evidence. The first process rerun reached `uncertain` only after the 30-second process timeout; the recovery command correctly refused the no-longer-running head. After explicit retry reconciliation, a 10-second rerun completed normally before interruption; completion then failed on stale evidence, and the resumed CLI exited because estimated conversation context (8,256) exceeded the selected 8,192 context. No owner-crash acceptance was achieved. Retry only with a new isolated profile/session, enough context for saved history, a fixture shorter than the runtime command timeout, and a watcher started before resume. Keep PR #82 open/draft; preserve the separate 8K context-limit failure for requirement follow-up.

## Documentation-head verification · `af9bee7` · 2026-10-30 UTC

Commit `af9bee748bd8a94cbee10ae8b26f95a2854b8bbc` passed all eight exact-head hosted jobs. Push CI `40998309597` (Windows `123876527307`, Ubuntu `123876527555`); PR CI `40998316200` (Windows `123876548480`, Ubuntu `123876548787`); push Desktop packages `40998309873` (Windows `123876527668`, Ubuntu `123876527982`); PR Desktop packages `40998316299` (Windows `123876548510`, Ubuntu `123876548858`). Both CI matrices passed. Both package matrices passed, including Windows installer/update metadata, clean-install renderer, mission-service lifecycle, Linux AppImage/update-manifest checks, and artifact upload. PR #82 remains OPEN/DRAFT/unmerged. The newest local owner-crash attempt is documented above and is not an acceptance pass.

### Exact-fit loaded context candidate · 2026-10-30 UTC

A live saved CLI session needed estimated context 8,256 but the selected instance exposed 8,192, then exited before further inference. The current local source change removes the context tolerance from loaded-instance adequacy selection, avoiding selection of a configured instance that cannot fit the estimate; a test verifies an existing 250,112 instance is selected over an 8,192 instance for an 8,256-token request with no load. Local Windows runtime (294: 291 pass, 3 skip), root typecheck, desktop production build, and diff check pass. Hosted checks for this candidate are pending. Keep the failed live run as failure evidence. Next establish how the real model inventory maps the two Qwen3.5 contexts, verify a resumed session with sufficient context, then repeat owner-crash recovery with a watcher started before the CLI rerun.

### Exact-head verification · docs predecessor `96ddeec` · 2026-10-30 UTC

The previous docs-only head `96ddeec0a99a21332eb67479fce771de817cae01` passed all eight exact-head Windows/Ubuntu CI and Windows/Linux package jobs. Exact run/job IDs are in `verification-history.md`. Windows installer/update metadata, clean-install renderer, mission-service lifecycle, Linux AppImage/update manifest, and artifact upload passed. This does not verify the new uncommitted exact-fit context source candidate added afterward.

### Duplicate live LM Studio context rows · 2026-10-30 UTC

The first local exact-fit fix passed its eight hosted jobs, but a follow-up live `lm models` inventory showed two Qwen3.5 9B rows with the same instance ID and contradictory contexts 250,112 and 8,192. Existing dedupe kept the lower row. Current local follow-up preserves the max reported context for that duplicate ID and has a regression using the observed shape; runtime 294 (291 pass, 3 skip), typecheck, desktop build and diff check pass. This follow-up is uncommitted/unhosted until recorded below.

After rebuilding, an isolated resumed session passed the former 8K context guard and made four model requests at ~10.3K–11.2K reported prompt tokens, but failed verification because the model twice cited stale `probe.cjs` evidence even after a fresh read. The session reached its turn limit; no command rerun/file edit occurred. This is bounded context/inference evidence and a failed task, not owner-crash acceptance or speed validation. Full details and exact IDs are in `verification-history.md`. Next publish this local fix to PR #82, collect exact-head CI, then retry owner-crash only with a fresh enough-context session and pre-started watcher. Keep the previous failed model attempts.

The `ea1f7b9` predecessor passed all eight exact-head checks; exact job IDs are in `verification-history.md`. They do not cover the duplicate-row merge change.

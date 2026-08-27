---
name: run-loop
description: Execute a loop-shaped plan as a ticket-driven autonomous loop. Use when running or resuming a plan produced by /plan-loop, when a queue of tickets has to be worked to completion, or when a build needs dispatch-verify-adjudicate rather than a single pass. The driver dispatches one fresh executor per ticket and closes a ticket only on the deterministic verdict of scripts/loop_check.py.
---

# Run Loop

> Execute a loop-shaped plan as a ticket-driven autonomous loop. A driver (this session) works a per-plan queue, dispatching one fresh executor per ticket under the bundled standards, closing executed tickets only on the deterministic verdict of `scripts/loop_check.py`, and folding discovered work back into the queue until it runs dry. A ticket whose acceptance is already satisfied may use the evidence-backed pre-dispatch close in Phase 1.
>
> **The one contract everything else serves: the checker is the only path to `done`.** An executor's claim of "verified" is never the gate. The driver re-runs the ticket's verify, re-checks attribution, territory, blast radius and evidence through `scripts/loop_check.py`, and reads the outcome from the verdict file it writes. Nothing else closes a ticket.

## Variables

`plan_path` — the argument this skill is invoked with: the path to the plan, either the flat `plans/<name>.md` or, once a loop has run, `plans/<name>/PLAN.md`. Both resolve to the same plan. Run by hand in a harness that passes no arguments, read it as "the plan the operator named".

Optional flags, given alongside the plan path:
- `--budget=N` — max executor dispatches this run (default 15). On exhaustion the driver pauses and asks whether to continue.
- `--dry-run` — Phase 0 only: materialize or derive the queue, print it, dispatch nothing.

## Where things live

Two roots, and the difference matters in every command below.

- **`<engine>`** — wherever this repo sits on the machine: the clone root under the plain-clone install, or the installed plugin directory under a plugin install. It holds `scripts/loop_check.py`, `scripts/loop_voice.py`, `tools/verify_seam.sh`, `config/blast-globs.txt`, `config/live-credentials.txt`, and `skills/run-loop/standards.md`.
- **`<project>`** — the repo the work lands in and the directory that holds `plans/`. Every plan, queue and ticket path below is relative to it.

The checker resolves `--plan-dir` from the process working directory and compares it against paths returned by `git diff`, so **the checker is always invoked with the working directory set to `<project>` and a `--plan-dir` relative to it**, even when the ticket's own `repo` is somewhere else. Its default blast globs resolve from `<engine>/config/blast-globs.txt` regardless of where it is called from.

Nothing here needs a virtualenv. Every script is Python 3 standard library only.

---

## Roles

- **The driver — this session.** Orchestrates only: claims tickets, assembles executor prompts, dispatches executors, adjudicates returns via the checker script, flips queue states, emits findings as tickets, writes the report. The driver NEVER writes feature code. If the driver catches itself editing a source file, that work belongs in a ticket.
- **Executors — fresh agents, one per ticket.** Run them on the strongest practical coding model your harness offers; the driver's own model does not have to match, and a cheap fast tier is a false economy on work that has to pass a real gate. Executors write the code and the evidence. An executor never touches `queue.jsonl`, and its claim of "verified" is never the gate — the driver re-checks everything.
- **The human.** Four touchpoints, and only these: manifest approval (Phase 0), `gate_policy: block` tickets (decided asynchronously while the loop continues), `blocked_on: scope` decisions, and the final report. Receipt and TTL clears do not add a human touchpoint. The human is never in the per-ticket path.

---

## Artifacts

A loop keeps the plan and its queue together: Phase 0 folds the flat `plans/<name>.md` into `plans/<name>/PLAN.md`, and everything else lives beside it in `plans/<name>/tickets/`:

- `queue.jsonl` — one line per ticket. **Driver-owned. Executors never write it.**
- `T-###.md` — the ticket file: trace, acceptance criteria, implementation plan, evidence, findings, history.
- `T-###.verify.log` — verify output captured by the checker, one header block per attempt.
- `T-###.verdict.json` — the checker's adjudication record (full verdict + `exit_code`), rewritten each run, with a `T-###.verdict-attempt-N.json` kept per adjudication so attempt history survives the rewrite. **The driver reads the verdict file, never a shell capture of the checker's exit status.**
- `RESIDUAL-NOTES.md` — velocity-mode P3 and hygiene findings retained beside the queue instead of emitted as tickets.

### queue.jsonl line schema

```json
{"id":"T-001","title":"...","status":"active","priority":"P1","deps":[],"repo":".","territory":["src/thing.py","tests/test_thing.py"],"verify":"bash ~/agent-loop/tools/verify_seam.sh --repo . --path src/thing.py --run 'python3 -m pytest tests/test_thing.py -q'","verify_expect":"\\d+ passed","claimed_by":null,"claimed_at":null,"base_sha":null,"attempts":0,"origin":"manifest","created":"2026-02-10T14:00:00Z","closed":null}
```

- `priority`: P1-P3. P0 is never assigned — it's the escalation class an executor reports.
- `deps`: ticket ids that must be `done` before this one is ready.
- `blocked_on`: temporary condition labels that must be explicitly cleared by the driver before selection, separate from ticket-ID dependencies. Absent means `[]` for older queues. `blocked_on: scope` is the reserved convention for a should-we-build question or product fork. It always blocks selection and is outside checker gate policy: no receipt, TTL, sweep, or driver inference may clear it. The driver may remove `scope` only after a human explicitly authorizes the ticket as written, and must first append the non-empty answer, actor, and timestamp to the ticket's History. A rejection or deferral leaves the label in place and the ticket non-runnable.
- `gate_class`, `gate_policy`, `gated_at`: checker-authored gate metadata copied from an exit-2 verdict. Missing `gate_policy` means `block`, so older rows stay fail-closed. `gated_at` is stamped when a `ttl` verdict first enters `gated` and is never refreshed by selection.
- `repo`: the git repo this ticket's work lands in, as a **path** (absolute, or relative to `<project>`). `"."` or absent is `<project>` itself, which is every single-repo plan. It drives two things: the claim step's `rev-parse` and the checker's `--repo`. The checker resolves no labels, so a manifest shorthand like `client` is expanded to the real path at materialization, exactly like a verify string.
- `territory`: globs the executor may modify, **relative to that repo** (that's what `git diff --name-only` returns). The ticket's own `T-###.md` is always implicitly writable.
- `verify`: a runnable command through a throwaway seam. A ticket without one is not a ticket.
- `verify_expect`: the ticket's **receipt** — an optional regex that the verify's own output must match (`re.M`) for a pass to count, so a verify exiting 0 having asserted nothing can't close the ticket. Absent means older behavior: exit 0 alone closes it. An invalid regex fails loudly by name rather than quietly widening into no receipt at all (`check_receipt`, `scripts/loop_check.py`). Phase 0 step 5c stamps one on every ticket it materializes.
- `claimed_by`: an identifier for whoever holds the claim. Use whatever the machine already knows — `git config user.name` is the sane default — or any string you choose. It is claim bookkeeping only: attribution of the *work* is by commit prefix, never by this field.
- `model`: the executor model actually dispatched, stamped by the driver before dispatch. An unstamped row reports `unspecified` in the verdict telemetry.
- `origin`: `manifest`, or `finding:T-###` for emitted tickets.

### T-###.md sections

```markdown
# T-001: <title>

## Trace
Vector: <entry point → path → fix site>    Signal: <what's wrong and how you know>    Fix site: <where the change lands>

## Acceptance criteria
<1-3 lines, checkable>

## Territory
<globs>

## Verify command
<command>

## Implementation plan
<EXECUTOR writes this at claim time, BEFORE coding — against the current codebase, not the plan doc's guess>

## Evidence
<EXECUTOR: what changed and why, per file; reference the verify log>

## Findings
<EXECUTOR: three tables — P0 discoveries / New work discovered / Refactor candidates. Write "None discovered." where a table is empty. Absent tables fail the evidence lint.>

## History
<DRIVER appends: attempt log, checker verdicts, gate decisions>
```

---

## The state machine

Only the driver writes `queue.jsonl`. Human decisions are recorded by the driver into History.

| From | To | Flipped by | When |
|---|---|---|---|
| `proposed` | `active` | driver (in-scope finding) or human | promotion into the runnable queue |
| `active` | `claimed` | driver | selected; sets `claimed_by`, `claimed_at`, `base_sha` (current HEAD **in the ticket's own repo**) |
| `claimed` | `in_review` | driver | executor reported back |
| `in_review` | `done` | driver | `loop_check.py` exit 0 — and only then; `gate_policy: receipt` is valid only on this exit-0 path and is recorded when present; findings emitted; `closed` stamped |
| `in_review` | `active` | driver | exit 1; `attempts` +1; checker verdict appended to History |
| `in_review` | `gated` | driver | exit 2 with `gate_policy: ttl` or `block`; class, policy, and `gated_at` are recorded |
| `active` | `parked` | driver | `attempts` ≥ 3; failure summary in History |
| `gated` | `active` or `done` | checker sweep or human decision, recorded by driver | elapsed `ttl` policy with receipt, or human reviewed a `block` diff |

---

## Phase 0 — Materialize

1. Read the plan completely. Confirm the header says `**Execution:** loop`; a plan not stamped that way is not a loop plan, so say so and stop rather than improvising a queue from it. Read `**Thoroughness:** velocity | rigorous` and keep that mode for every emission decision. A plan without the header is treated as `rigorous`.
2. **Fold the plan into its folder.** If the plan is still the flat `plans/<name>.md`: `mkdir -p plans/<name>` then `git mv plans/<name>.md plans/<name>/PLAN.md` (history follows the move). Every reference from here on — executor prompts, the Loop Report, the status update — uses `plans/<name>/PLAN.md`. Idempotent: on resume the plan is already inside.
3. **Resume path:** if `plans/<name>/tickets/queue.jsonl` exists, this is a resume. Reset any `claimed` or `in_review` ticket from a dead session back to `active` with a History note ("reset on resume"), keep everything else as-is, and go to Phase 1. The loop is idempotent from state on disk.
4. **Manifest path:** if the plan has a `## Ticket Manifest` section, materialize it: create `tickets/`, write one queue line and one `T-###.md` per manifest row (Trace, Acceptance criteria, Territory, Verify command filled from the manifest; the other sections as empty headings). The manifest was approved with the plan — no second approval.
5. **Derivation path (adoption):** if the plan has no manifest, derive one from its step-by-step tasks — small, independently verifiable tickets, each with a runnable verify command and territory globs; a task needing more than one sitting becomes two tickets. Present the derived manifest as a table plus the plan-scope globs, and **STOP for the human's approval** before writing anything.
5b. **Materialize a runnable verify string — no placeholders, and the dep-chain built in.** The verify goes into `queue.jsonl` fully expanded: a manifest shorthand (`<client>`, `<T003 file>`, `<chain…>`) and the `<engine>` root itself are written out to real paths here, because `loop_check.py` runs the string verbatim through `shell=True` and a leftover `<...>` is a shell redirect that dies before the seam runs (`validate_verify` fails such a string on attempt 1, but Phase 0 is where it should never exist). **Deps-aware chaining is this step's job, not the checker's.** When a ticket's own migration only applies after a dependency's migration (its function/table is a precondition), materialize the verify as the ordered chain the seam already accepts (`verify_seam.sh <dep-migration> … <this-migration> <assert>`), listing the contributing deps explicitly. The checker stays verbatim by design — ordering is the caller's problem — and building the chain at materialization is what satisfies "no human hand-edits the verify string" without the checker silently inferring an order.
5c. **Stamp a receipt on every ticket — `verify_expect`.** Exit codes measure "no command errored", not "the assertion happened": a script without `set -e`, an assert returning zero rows, a grep guarded by `|| true` all exit 0 having checked nothing. So every materialized queue line also gets a receipt regex. Take it from the manifest's **Receipt** column when the plan ships one; otherwise derive it from the verify string's runner — `pytest` → `\d+ passed`, a shell verify script → the explicit PASS marker it prints, a bare assert script → an explicit token the script prints on success, added here if it prints nothing today (a script with no success output has no receipt to match). **A receipt asserts that work happened, not that the command exited.** `\d+ passed` is a receipt; `0 failed`, an empty match, or any pattern a skipped or collected-nothing run also prints is not. Derive it at materialization, with the verify string, because changing what a receipt asserts once work has landed is the gate-weakening direction the Rules already send to a human.
6. If `--dry-run`: print the queue that was (or would be) materialized and stop.
7. **Mirror the queue into the harness's task tracker, if it has one:** one entry per ticket (`T-### — <title>`). The tracker is the UI progress view for whoever's watching; `queue.jsonl` stays the only source of truth and is never read back from the tracker. Task tools are harness- and model-dependent — when there is no tracker, skip the mirror entirely and continue. No protocol step may depend on the tracker existing.

---

## Phase 1 — The loop

Repeat until the ready set is empty or the budget is exhausted:

1. **Select.** Before computing the ready set, run `python3 <engine>/scripts/loop_check.py --sweep-gates --plan-dir plans/<name>` from `<project>`. Read its JSON result. For every entry in `swept`, use the checker's already-written `approved-by-ttl` verdict and History receipt, narrate one line (`T-004 auto-cleared — blast_other by TTL; veto: git revert --no-commit <sha>`), and add the receipt to the run's auto-cleared log. A sweep error stops selection and is handled as a shared-harness failure; the driver never silently skips a failed sweep. Then compute the ready set = `active` tickets whose `deps` are all `done` and whose `blocked_on` condition labels are all explicitly cleared by the driver, ordered P1 → P2 → P3, then by id. An unmet `blocked_on` excludes a ticket even when every dependency is done; record the condition and its clearance in History.
   A ticket with `blocked_on: scope` belongs to the scope lane, not the gate lane. Record the exact should-we-build question or product fork in History when applying the label. Only a positive, explicit human answer recorded with actor and timestamp clears it; negative, empty, inferred, receipt, and TTL outcomes do not. Scope-blocked tickets never appear in `gated` lists or gate counts.
   **Active batching at select.** The driver may take 2-3 ready, small tickets with identical territory and the same `repo` plus compatible `verify` commands, and send them to one chained executor in ready-set order. A P1 or P2 ticket may batch only with tickets at its own priority; never batch a P1 or P2 ticket with a lower-priority ticket. Run pre-dispatch re-validation and claim state transitions separately for every candidate. If re-validation closes one as already satisfied, remove it from the batch before dispatch.

   Batching changes prompt setup only. The executor completes the full guarded chain for one ticket before starting the next, makes no shared cross-ticket commit, and uses one plan-scoped commit prefix per ticket. On return, move each claimed ticket to `in_review` and adjudicate each ticket independently with its own `loop_check.py` invocation and verdict file. A sibling ticket's verdict never closes, retries, parks, or gates another ticket.

   **Parallel dispatch.** Serial remains the default. The driver may dispatch N concurrent executors only when all selected tickets share the same `repo`, their exact-filename territory entries are pairwise disjoint, and `python3 <engine>/scripts/loop_check.py --lint-queue --plan-dir plans/<name>` exits green immediately before dispatch, including no directory glob shared by two tickets. If any condition fails, use serial dispatch. Parallel dispatch is not batching: claim and prompt one fresh executor per ticket, keep attribution and adjudication per ticket, and close each return with only `queue.jsonl` plus that ticket's exact filenames. Never use a directory pathspec for a parallel close.

   **Pre-dispatch re-validation.** Before claiming the selected ticket, run its `verify` command through `<engine>/tools/verify_seam.sh` with the ticket repo and each territory glob, then confirm every acceptance criterion against the current tree. A green generic suite alone is not acceptance evidence. If both checks pass, fill Evidence with the command, observed result, current HEAD, and acceptance evidence; append the evidence and an `already-satisfied` receipt to History; set status → `done` and stamp `closed`. Do not claim or dispatch it. This path does not consume an executor dispatch or increment `attempts`; commit the ticket and queue receipt by exact filenames (`{id}.md` and `queue.jsonl`, never the `tickets/` directory), narrate the close, and select again. A failed pre-dispatch verify is only a selection signal: write no verdict, verify-log block, History receipt, or attempt, then proceed to claim. The owner-controlled core branch still requires its `--satisfied-by` receipt and does not use this shortcut.
   Claim the first ticket that still needs execution: status → `claimed`, set `claimed_by`, `claimed_at`, `base_sha` = `git -C <the ticket's repo> rev-parse HEAD`. Read HEAD in the ticket's `repo` (default `.`), never in the driver's own: a SHA from the wrong repo is not a commit in the one the work lands in, and the checker refuses it rather than diffing an empty set.
   **Owner-controlled core branch:** a ticket whose manifest mode is `core` is never dispatched, because it edits the adjudicator itself — `scripts/loop_check.py`, `tools/verify_seam.sh`, or the gate config. The repo owner lands the declared change with the full battery, moves the ticket to `in_review`, then runs `loop_check.py ... --satisfied-by <landed-sha>`. That mode verifies the commit exists, is an ancestor of HEAD, and touched no path outside the ticket's declared territory; it reruns the ticket verify and writes both `verdict.json` and an idempotent History receipt. The landed work commit therefore names only the ticket's declared territory plus its own ticket file: **`queue.jsonl` is bookkeeping and is committed separately**, because a work commit carrying it touches a path outside the declared territory and fails the satisfied-by check — which is exactly how a real core close has failed before. A direct or bulk queue close is invalid without that receipt.
2. **Assemble the executor prompt** in this exact block order. The standards file is READ AT DISPATCH TIME from `<engine>/skills/run-loop/standards.md` and referenced by path, so an edit to it reaches every future loop. For a batch, include identity and the shared context once, then repeat the ticket, chain, territory, escalation, and completion blocks for each ticket in chain order:
   1. **Identity:** "You are the loop executor for ticket {id} in plan {plan}. Working dir: {the ticket's repo}, branch `main`." When that repo isn't `<project>`, say so: the code lands there, the ticket file `{id}.md` stays in `<project>`, and each is committed in its own repo.
   2. **Context reading list**, in order: `<engine>/skills/run-loop/standards.md` (required — the executor obligations and coding standards); `plans/<name>/tickets/{id}.md` (your ticket); the plan doc's relevant section(s); plus any project doc covering a system this ticket touches.
   3. **The ticket:** title, the Trace block, acceptance criteria, verify command.
   4. **Chain execution protocol** (inline):
      ```
      1. Write your Implementation plan into {id}.md FIRST (## Implementation plan) — grounded in the current codebase, not the plan doc's guess
      2. TRACE the vector to root cause
      3. DIAGNOSE: actual root cause, or a symptom? If deeper, extend the trace
      4. FIX: the smallest correct change at the fix site. No refactors, no drive-by cleanup — a discovered refactor goes in Findings, not in the diff
      5. PREPARE THE SEAM: commit the implementation, tests, verify artifacts, and Implementation plan by explicit pathspec before verification. Every repair is committed before its retry. The guard refuses uncommitted claimed-territory inputs so another session's autostash cannot remove the code being verified.
      6. VERIFY: run the ticket's verify command through tools/verify_seam.sh until green. Pass the ticket repo with --repo, each territory glob with a separate --path, and the shell-quoted verify string as the single --run value. The guard refuses known live credential variables before the command starts. In a parallel dispatch, send the driver `ready-to-verify` and wait for its release before running the first verify.
      7. DOCUMENT: fill ## Evidence (what changed and why, per file) and ## Findings (all three tables; "None discovered." where empty), then commit those ticket-file updates with the same required prefix. Evidence MUST contain one informational `Read set:` line listing every out-of-territory module imported or asserted against by this ticket's tests; `Read set: none` is valid. The read-set disclosure grants no write permission and does not expand territory.
      One chain, fully, in this order. Blocked is different from hard: hard means continue; blocked means document the blocker in Findings and report back.
      ```
   5. **Territory** (inline):
      ```
      CAN modify: {territory globs} + plans/<name>/tickets/{id}.md
      CANNOT modify: everything else. queue.jsonl is NEVER yours. Read anything you need.
      A change needed outside your territory goes in Findings → New work discovered. Do not make it.
      ```
   6. **Optimal Path + intensity** (inline):
      ```
      OPTIMAL PATH: full semantic analysis before coding; match naming and architectural patterns EXACTLY; handle every error path; no unnecessary complexity; test failure points as you build; verify integration after.
      This must be production-ready on first delivery. No placeholders. No TODOs. Complete, working, verified code or a clear blockers report. Execute now.
      ```
   7. **Critical escalation** (inline): "If you discover data corruption, a security hole, or a race condition losing data — in or out of territory — STOP the chain, document it under Findings → P0 discoveries, commit what you have, report back."
   8. **Completion requirements** (inline; the driver substitutes the LITERAL plan-directory basename for `<plan-slug>` before sending, never a template or a shortened form — a shortened slug attributes nothing and costs a retry):
      ```
      1. Commit by explicit pathspec before the first guarded verify, and commit the Evidence/Findings follow-up before handoff: git add -N <new files>; git commit -m "loop(<plan-slug>/{id}): <summary>" -- <files>. `<plan-slug>` is the exact plan-directory name from plans/<plan-slug>/PLAN.md. Never git add -A. Never git commit -a.
      2. The prefix is load-bearing, not a style: the checker attributes your work only by the exact literal `loop(<plan-slug>/{id}):` inside the claim window. A same-numbered ticket from another plan is foreign work. A missing or shortened prefix is invisible to adjudication, and zero attributed commits fails the ticket.
      3. Report back: chain state, files touched, verify result as observed, findings counts.
      Do NOT claim the ticket done — the driver re-runs every check itself. Do NOT edit queue.jsonl.
      ```
3. **Dispatch.** Before dispatching, stamp the queue row's `model` field with the executor actually being used — model name, prefixed with the harness when it isn't the driver's own (`sonnet`, `opus`, `codex/<model>`). The verdict's telemetry reads that field, and an unstamped row reports `unspecified`, blinding the per-model outcomes table.
   **Every executor is a fresh agent with an empty context window.** Never fork, clone, resume or otherwise hand an executor the driver's conversation: a forked context carries the adjudication reasoning, sibling tickets and gate decisions the executor must not see, and the separation between driver and executor is the only reason the checker's verdict means anything. (In Claude Code that means dispatching a general-purpose or custom executor subagent and never `subagent_type: fork`. In a plain terminal it means a new session started from the assembled prompt and nothing else. In any harness the test is the same: could the executor read the driver's reasoning? Then it isn't fresh.)
   Serial dispatch is the default. A compatible batch still uses one chained executor. Parallel dispatch uses one fresh executor per ticket only under step 1's disjoint-territory, lint-green, same-repo preconditions. The driver releases parallel executors to VERIFY one at a time in ready-set order; before each release it drains sibling closes and relays any finding that names that in-flight ticket. Record the relay in the closing source ticket's History, and require the receiving executor to record the same relay in its own ticket History before VERIFY. Adjudicate every returned ticket separately and close it with exact-file pathspecs even while its siblings remain in flight. Parallel dispatch here uses the shared checkout under the declared territory rails; per-executor worktrees are not part of this engine.
4. **Adjudicate.** On return: status → `in_review` for each returned ticket. Run separately per ticket:
   ```
   bash <engine>/tools/verify_seam.sh --repo <the ticket's repo> \
     --path <territory-glob-1> [--path <territory-glob-N>] \
     --run '<shell-quoted loop_check.py command>'
   ```
   The guarded command is `cd <project> && python3 <engine>/scripts/loop_check.py --plan-dir plans/<name> --ticket {id} --repo <the ticket's repo> [--blast-globs-file plans/<name>/blast-globs.txt]`. The explicit `cd` invokes the checker from the directory that holds `plans/`, preserving its project-relative plan-path contract, while the guard and `--repo` still inspect and adjudicate the ticket repository. This is the plain checker invocation with the environment and clean-input refusal placed in front of it.
   For the owner-controlled core branch, append `--satisfied-by <landed-sha>` to this command. Normal executor adjudication and satisfied-by adjudication produce the same durable verdict contract.
   `--repo` is always the ticket's `repo` field (default `.`): the log walk, the status scan and territory matching all run there, so a ticket worked in another repository is checked where the work actually happened. `--blast-globs-file` amends the default globs for the whole plan when the plan ships one, and is never set per ticket. The file is a diff, not a replacement: a bare line adds a glob, a leading `-` drops a default, and a first non-comment line of `!` replaces the list wholesale — so a default the file doesn't name stays gated, and an omission can't silently ungate a class. The checker attributes work by the exact plan-scoped commit prefix inside the claim window (never by a bare time window or ticket number — other plans also start at T-001). A history rewrite under a live claim (a `pull --rebase` on the shared checkout orphaning the recorded base) is detected by ancestry and self-heals: the checker re-anchors the window to the parent of the ticket's earliest plan-scoped commit and records it in the verdict's `window` field. **Read the outcome from `tickets/{id}.verdict.json` (`exit_code` field), never from a shell capture** — a piped `$?` has already lied once.
   - **Exit 0** → `done`, `closed` stamped. When the verdict carries `gate_policy: receipt`, append one idempotent History line containing `gate_class`, the receipt's `kind` and summary, and its `veto_command`; add the same record to the run's auto-cleared log; narrate exactly one line (`T-004 auto-cleared — <gate_class>: <receipt summary>; veto: <command>`). A receipt clear pages nobody and does not count as a human intervention. **Commit that close by exact filenames — `queue.jsonl` plus the closing ticket's own files (`T-###.md`, `T-###.verify.log`, `T-###.verdict.json`), nothing else.** A directory pathspec over `plans/<name>/tickets/` sweeps whatever a sibling executor has in flight, and has done exactly that in a real run: one ticket's close commit carried another executor's half-written ticket file with it. While any ticket is `claimed` or `in_review`, a directory pathspec is forbidden for every close and bookkeeping commit the driver makes. Parse the Findings tables, assign each actionable finding P1, P2, P3, or hygiene, then apply the plan's thoroughness policy. If a finding names a currently in-flight ticket, relay it before that executor verifies: append the relay to this closing ticket's History, send it to the named executor, and require the matching receipt in the named ticket's History before releasing VERIFY. **Velocity emits P1 and P2 findings as tickets** and appends **every P3 or hygiene finding** to **`tickets/RESIDUAL-NOTES.md` beside `queue.jsonl`**, including its source ticket, category, title, evidence, and disposition. **Rigorous emits P1, P2, and P3 findings as tickets**, treating actionable hygiene or refactor findings as P3. P0 discoveries retain the critical-escalation path and never become ordinary queue entries. `scripts/loop_check.py` remains the verdict gate and does not interpret thoroughness.
     For each finding emitted as a ticket, normalize the title (lowercase, alphanumerics and single spaces only) and compare against every existing queue line's normalized title — a match is skipped as a duplicate. New tickets whose territory falls inside the plan's declared scope globs are born `active`; scope-expanding ones are born `proposed`. `origin` = `finding:{id}`. Log every emission or residual decision (created / skipped-duplicate / proposed / retained-residual) for the report.
     **Emission parity with Phase 0.** Every ticket born here, `active` or `proposed`, gets the same materialization a manifest row gets in Phase 0: its own `T-###.md` (Trace, Acceptance criteria, Territory, Verify command filled; other sections as empty headings), a verify authored against THIS ticket's own acceptance criteria under step 5b's rules (runnable, no placeholders, never copied from the finding's source ticket), and a `verify_expect` receipt under step 5c's rules. The measurement that forced this rule: in one audited run, every emitted ticket shipped without a file of its own and nine of ten carried a verify copied from the ticket that discovered them — each one ran, passed, and closed green having asserted nothing about its own work. **The copy check** is a string check against the queue line's verify field, not an execution: it refuses emission when that string names no path or glob fragment from the new ticket's own territory. The driver can still emit it by recording a one-line exception in the ticket's History naming why (a gitignore check probing ignored paths is a legitimate example); the escape is recorded, never silent.
     **Emission governor.** Maintain run counters named `emitted_tickets` and `closed_tickets`. Count each newly created `active` or `proposed` queue ticket as emitted, but do not count duplicates or residual notes; count every ticket that reaches `done` as closed. After every close, record `tickets-emitted-per-closed` as `emitted_tickets / closed_tickets` in the emission log and console. If the ratio exceeds `1.0`, flag the run as `non-converging` in the console, Loop Report, and retrospective calibration. This is a durable warning, not an automatic pause.
     Before per-finding emission, group related scope-expanding findings. Two or more findings that share the same out-of-scope objective or territory are an **off-campaign cluster**. File one new-plan seed at `plans/seeds/<date>-<slug>.md` with the source tickets, finding titles, evidence, proposed scope, and the reason it sits outside this campaign. Do not emit that cluster into the current queue. Log the seed path and each absorbed finding in the emission log so the Loop Report accounts for the routing.
     A migration set may additionally report `apply.action: auto_apply`. This is available only when the plan contains `apply-policy.json` with `enabled: true`, runnable `drift_check` and `apply` commands, the checker is otherwise green, registerability passes, drift passes, and no path matches the effective blast globs. Run the recorded `apply` command only in that case, then rerun `drift_check` and record both results in History. The word "green" always means this checker verdict, never the executor's report. Without explicit opt-in, every migration remains human-gated. To permit additive migrations while retaining danger gates, the plan's own blast-globs file must drop the generic migration globs and add its reviewed data-loss, mass-merge, auth, and secrets globs; drift still runs before both the auto-apply and blast-gated decisions.
   - **Exit 1** → the checker atomically returns the ticket to `active`, increments `attempts`, writes exactly one verify-log block for that attempt, and appends one marker-keyed History receipt. The driver must not repeat those writes. At `attempts` ≥ 3 → `parked` with a failure summary. The next executor's prompt includes the History, so it starts from what went wrong.
   - **Exit 2, `correlated_failure` non-empty** → shared-harness pause. Three consecutive byte-identical verify failures are one environment incident, not three ticket misses: do not increment attempts, do not park the current ticket, and stop further dispatch until the harness is repaired or the human clears the pause. Report the fingerprint and affected tickets.
   - **Exit 2, otherwise** → branch mechanically on the verdict's `gate_policy`; never infer policy from paths or prose. Missing or unknown policy is `block`.
     - `ttl` → set `status: gated`, copy `gate_class` and `gate_policy` to the queue row, and stamp `gated_at` once. Tell nobody. The select-time sweep is the only automatic path from this state to `done`; until it writes an `approved-by-ttl` verdict and receipt, the ticket remains gated.
     - `block` → set `status: gated`, copy `gate_class` and `gate_policy`, and tell the human in one line (ticket, class, offending SHAs/paths). The loop continues with other tickets; the gate blocks only this ticket.
     - `receipt` → fail closed as a checker contract error: an authorized receipt clear must have returned exit 0 with a structured receipt. Leave the ticket gated and report the invalid verdict; do not manufacture a receipt or clear the row.
5. **Narrate one line per transition** to the console (`T-004 claimed → executor dispatched`, `T-004 done — 2 findings emitted, 1 duplicate skipped`), and mirror it to the task tracker when the harness has one: in-progress on claim, completed on done; a parked or gated ticket keeps its entry with `(parked)` / `(gated)` suffixed to the title; an emitted ticket gets a new entry.
6. **No per-ticket bookkeeping anywhere else.** `queue.jsonl` plus the ticket files are the fine-grained record of the run, and nothing outside `plans/<name>/` needs updating per ticket.

---

## Status report

When a driver is asked for status, by a human or another session, answer with facts first, prose after. Lead with queue counts (done/total/claimed), then a separate `Scope-blocked` section listing each unresolved `blocked_on: scope` ticket and its exact question. Follow with one line each for every gate and park, excluding scope-blocked tickets from the gate list and count, then any attempts anomaly, then money receipts when the plan carries a cost dimension (spent, cap, projection). Add a product-terms lane label for what's running now: describe the work, not the ticket id ("transcoding the A-roll", not "T-012"). Close the facts with the next milestone. Prose comes last, and only once the facts are on the record, so anyone reading the answer can distill it well regardless of the driver's mood.

---

## Phase 2 — Terminate

1. When the ready set is empty: run one **quiescence pass** — re-scan the queue; if the final iteration emitted any new `active` ticket, return to Phase 1. Verify phases are ticket sources; a queue can look briefly empty. Audit every `done` row before reporting: an executed ticket must have a committed verdict with `exit_code: 0` and non-empty History; an already-satisfied close must have committed, non-empty Evidence and its `already-satisfied` History receipt. A core row must additionally name `satisfied_by`; flag any manual or bulk close that lacks the required receipt and return it to `active`. The same pass also audits every `active` and `proposed` row: its `T-###.md` exists, its `verify_expect` receipt is present, its verify names at least one own-territory path or carries the recorded copy-check exception, and (unless the row is `proposed`) its territory sits inside the plan's declared scope globs. A failing row is repaired or returned to the emitting context, never dispatched as-is. A repair that corrects a row's territory re-checks it against the scope globs too: a corrected ticket landing outside scope flips to `proposed` or is surfaced, not left `active` outside the plan it claims to serve.
1b. **Terminal battery pass, optional.** A plan may declare one plan-level `battery` command — a `**Battery:**` line beside the Ticket Manifest, the same pattern as `**Scope globs:**` — for a check broader than any single ticket's verify: the kind that catches tickets correct alone but wrong together, composition failures no per-ticket verify can see. A plan that declares none skips this step entirely. When a plan declares one, run it exactly once through `<engine>/tools/verify_seam.sh`, after the quiescence pass and before the Loop Report is written. Green → continue to step 2. Red → emit the failures as one or more tickets, same duplicate-detection and thoroughness rules as any other finding, and return to Phase 1 to work them; the Loop Report waits until they're resolved.
2. Append a `## Loop Report` section to the plan doc:
   - Tickets table: id, title, final status, attempts, evidence link (`tickets/T-###.md`).
   - **Scope-blocked:** its own always-blocking section listing every unresolved `blocked_on: scope` ticket, the exact should-we-build question or product fork from History, and when it was raised. Include zero explicitly. Scope-blocked tickets are absent from the `gated` list and gate counts.
   - `gated` list (policy and class; identify only `block` rows as awaiting the human), `parked` list (with failure summaries), `proposed` backlog. Exclude `blocked_on: scope` rows from the gate list.
   - **Auto-cleared table:** one row per receipt or TTL clear, with columns `Gate class | Ticket | Receipt summary | Veto command`. Include zero as an explicit empty count, not an omitted table.
   - The emission log (every created / skipped / proposed decision).
   - Telemetry: dispatches used vs budget, attempts distribution, runtime, plus these cross-run fields:
     - **Freshness table:** group every queue ticket by final status and age bucket (`0-1d`, `2-7d`, `8-30d`, and `31d+`). Age runs from the ticket's `created` timestamp through run termination. Report a count and percentage for each bucket, plus the percentage emitted after the manifest versus present at manifest creation.
     - **Emissions-per-close:** report `emitted_tickets / closed_tickets` with both counts, the ratio, and its converging or `non-converging` classification from the emission governor.
     - **Per-`(model, prompt_version)` outcomes table:** group dispatched-ticket verdicts by the exact queue `model` and verdict `prompt_version`; report dispatches, exit-0, exit-1, exit-2, and pass rate. Use the checker's `unspecified` for a missing stamp so older data stays visible instead of changing a denominator.
     - **Batching table:** every batch formed this run, listing its ticket IDs in dispatch order; batched vs solo dispatch counts; and outcomes (exit codes, attempts) split batched vs solo — the data a cross-run audit needs to detect over- or under-batching and tune the select rule.
     - **Cycle-time table:** one row per ticket that closed `done` this run — id, `created`-to-`closed` duration, `claimed_at`-to-`closed` duration, batched-or-solo — computed from `queue.jsonl`'s own `created`/`claimed_at`/`closed` timestamps only, no new capture. Roll up both durations as median and p90, overall and split batched vs solo, so the Batching table gains a time axis: whether batching a ticket costs or saves wall-clock time, not just whether it passes. A ticket closed by Phase 1's pre-dispatch re-validation path (`already-satisfied`, no `claimed_at`) reports created-to-closed only; claimed-to-closed stays blank rather than guessed.
   - **Auto-close remains rejected.** Open proposed tickets are the duplicate-detection substrate, and stale-ticket auto-close has been measured elsewhere to reduce merged work and contributors rather than to clean anything up. Keep proposed tickets open; freshness measurement and dispatch-time re-validation are the hygiene mechanism.
2c. **Voice run-end note.** When the notifier is configured (see **Notifications** below), close the run with:
   ```
   python3 <engine>/scripts/loop_voice.py \
     --message "<plan-slug> run-end: <done> done, <parked> parked, <gated> gated, <proposed> proposed. <what-was-built>" \
     --run-context "<plan-slug>/<run-id>/<machine>"
   ```
   `<what-was-built>` is not new writing — it's the same one-or-two-sentence plain-language summary of what the run built that the driver already composes for the Loop Report, passed through verbatim alongside the counts, never counts alone: a rewrite has nothing to recover if the driver never hands it the content. Fires whether or not anyone was watching — this is the one note that always goes out when the channel is configured; per-ticket notes stay opt-in. Skipped silently, no refusal, when the pointer files are absent.
   **Keep `<what-was-built>` short — roughly two sentences: the counts plus one plain-language line on what got built, not a recap of the whole run.** The rewrite's latency scales with the message it's given: a real ~65-word run-end message hit the rewrite timeout (`loop_voice: briefing rewrite timed out after 40.0s`), while the same content trimmed to ~45 words succeeded on the very next attempt, and an earlier ~45-word measurement came back in 18.7s. The shorter the note, the more reliably it comes back rewritten instead of falling back to verbatim.
3. **Write the run's self-retrospective** to `plans/<name>/RETRO.md` — the Loop Report is what happened, this is what it *means for the engine*. Every run files one, proportionate to what happened (a clean run gets a short retro that's still useful as baseline data). Use these exact headings so retros aggregate across runs:
   - **Health verdict** (one line): trustworthy / trustworthy-with-caveats / not — did the checker stay the only path to done, did attribution hold, did the seam stay honest.
   - **Interventions** (one count line, then one line per touch): how many times a human had to touch this run, and what each touch was — a `block` gate cleared, a park unblocked, a budget refreshed, a harness repaired. Receipt and TTL clears are excluded. `Interventions: 0` is the graduation signal: a plan class is trusted to run with nobody watching only when zero holds across consecutive runs.
   - **Auto-cleared** (one count line, then one line per receipt): write `Auto-cleared: N`, then class, ticket, receipt summary, and veto command for every `receipt` or `approved-by-ttl` clear. This is telemetry, never a human touch.
   - **What this run taught us about the engine**: engine defects a real ticket surfaced (a checker false-pass or false-gate, an attribution incident, a seam failure, a version-string collision, a protocol gap the driver had to improvise around), each with the ticket or commit that exposed it.
   - **Calibration**: where the manifest or estimates were off, the attempt distribution (did anything actually need a retry), and the emissions-per-close ratio (emitted ÷ done) with a one-line read on whether the queue converged or ran away.
   - **What worked, keep it**: the patterns that held, so the next engine change doesn't regress them.
   - **Feeds the engine backlog**: concrete engine changes this run argues for, each pointing at an existing engine ticket or naming a new one.
   Write "None this run." under any genuinely empty heading. An all-"None" retro on a run that parked or gated anything is itself a finding — the driver wasn't reflecting.
4. Update plan status: `Implemented` if everything is `done`; `Implemented (residual)` if parked, gated, or proposed tickets remain.
5. **Close the run's own commits by explicit pathspec:** the plan doc, `RETRO.md`, `queue.jsonl`, and the ticket files, each named. Push if the project's workflow pushes. Report anything left uncommitted rather than sweeping it in.

---

## Notifications

`scripts/loop_voice.py` is this engine's notifier: it turns a driver milestone into a real Telegram voice note — text-to-speech → OGG/Opus → `sendVoice` — with a plain text message as its own fallback when speech or the container conversion fails.

- **Credentials come from pointer files, read at call time, never from the environment:** `~/.config/fish-audio/speak.json` (`apiKey`, `voiceId`, `model`) and `~/.config/loop-voice/telegram.json` (`bot_token`, `chat_id`), both mode 600. This is not a style preference. `tools/verify_seam.sh` refuses credential-shaped environment names by design, and every one of those names is listed in `config/live-credentials.txt`, so a channel that read its keys from the environment could never be invoked from inside a verify at all. Both paths are overridable with `--fish-config` and `--telegram-config`, which is how the tests run without touching a real config.
- **Exit contract, shared with any other channel you add:** `0` delivered (by voice or by the text fallback), `2` not landed anywhere, `3` misconfigured. Never best-effort, never silent.
- **What speaks.** The Phase 2 run-end note always, when the channel is configured. Gate notes, so a `block` gate reaches you while the loop carries on with other tickets. Per-ticket completion notes do NOT go out by default: an eight-hour run closing dozens of tickets would turn every close into a voice note, which is noise, not signal — they fire only when the run explicitly opts in.
- **Absent config is not an error.** With no pointer files the engine behaves exactly as it did before the channel existed: no note, no refusal, no message.
- **`--prove` is the arm-time check** when you want one: it sends a fixed proof sentence naming the run context, so "the channel worked last night" is never mistaken for "the channel works now."
- **A note that fails to land** (exit 2 or 3) is recorded in the ticket's History with its exit code and changes nothing else: the park stays parked, the gate stays gated, and the run continues to its termination. A notification is never the state; History is.
- The rewrite step that turns a terse status line into a spoken briefing shells out to an optional local model binary, and the OGG rewrap shells out to `ffmpeg`. Both are **optional**: missing, each degrades to a documented fallback with a diagnostic on stderr, and the note still goes out.

---

## Stop conditions

Everything that would otherwise halt a run, and what the driver does instead. Nothing here self-clears by driver discretion: a gate stays gated and a park stays parked unless the checker authorizes the named reversible class.

| Condition | What the driver does |
|---|---|
| Phase 0 derivation approval (plan has no `## Ticket Manifest`) | STOP for the human's approval before writing anything |
| receipt clear (`exit_code: 0`, `gate_policy: receipt`) | append History, narrate one line, continue — not a human touch |
| `gated` ticket, `gate_policy: ttl` | stays gated until a select-time sweep writes its `approved-by-ttl` verdict; tell nobody |
| `gated` ticket, `gate_policy: block` or absent | one line naming ticket, class and offending SHAs/paths; the loop continues with the other tickets |
| `blocked_on: scope` ticket | keep it in the separate scope lane until a human explicitly authorizes the ticket as written; never sweep or auto-clear it |
| `parked` ticket (`attempts` ≥ 3) | failure summary in History; the loop continues with the other tickets |
| correlated-failure pause (exit 2, `correlated_failure` non-empty) | stop dispatching until the harness is repaired or the human clears the pause; report the fingerprint and affected tickets |
| budget exhaustion | report state (done / remaining / parked / gated) and ask: fresh budget, or stop. A run does not grant itself more budget |
| terminal battery pass returns red | emit the failures as tickets and return to Phase 1 |

---

## Rules (non-negotiable)

- **The checker is the only path to `done`.** Not the executor's report, not the driver's reading of a diff, not a green-looking terminal. Exit 0 from `scripts/loop_check.py`, read out of the verdict file, or the ticket is not done.
- **Verify only through the loop guard and a throwaway target seam.** `tools/verify_seam.sh` refuses known live credentials and any uncommitted file in the ticket's claimed territory. The command it runs must still target throwaway state: a temp directory, a scratch database, a random free port, fixtures. Never a live port, a live database, or a real API that bills or writes. If the target environment has no throwaway seam, building one is ticket zero.
- **All commits by explicit pathspec.** For the driver's close and bookkeeping commits that means exact filenames — `queue.jsonl` and the closing ticket's own `T-###.md`, `T-###.verify.log`, `T-###.verdict.json` — and never a directory pathspec over `plans/<name>/tickets/` while any ticket is `claimed` or `in_review`: that pathspec is literally explicit and behaves like `git add -A` over the one directory every concurrent ticket writes into.
- **The driver never writes feature code.** Discovered driver-side work becomes a ticket like everything else.
- **Claimed territory is frozen to everyone but its executor — the driver included.** While a ticket is `claimed` or `in_review`, the driver and any cooperating session treat its territory globs and ticket file as read-only. A foreign commit sweeping an executor's in-flight edits corrupts attribution, and it has happened. What the driver does instead: a driver-side edit becomes a ticket like any other discovered work, or waits for the close. If it genuinely can't wait (a live engine fix mid-run), the driver lands it knowing the checker will gate the claimed ticket as unattributed, and records that deliberate decision in the ticket's History — a gate cleared by a human who knew, never a silent sweep.
- **One driver per plan queue at a time.** Two concurrent drivers on one queue is unsupported — the resume path (Phase 0.3) is how a second session picks up a dead loop, not a live one.
- **Budget pause:** on exhaustion, report state (done / remaining / parked / gated) and ask the human: continue with a fresh budget, or stop and land what's done.
- **Blast-radius gates fire on the diff, not the description.** The checker's default globs live in `config/blast-globs.txt` (migrations, auth/token/secrets modules, `.env*`, Dockerfiles, git hooks, credentials, and the judge files themselves) and are amendable per plan via `--blast-globs-file` (diff format: bare lines add, `-` drops a named default, `!` on line 1 replaces wholesale), **never removable from inside a ticket.** A ticket cannot widen its own gate.
- **Repairing a verify: split by what the repair touches, because that decides whether it can weaken the gate.** An *unrunnable* verify (a `<...>` placeholder, a `cd` to a missing path, a chain missing a dependency's migration) may be repaired at any point, before or after work lands, **provided the assertion file is byte-identical and the change only adds chain links** — that can widen coverage, never shrink it. A change to *what is asserted* — the assertion text, the count, the exit condition — is the dangerous direction and **always gates to the human**, even when the original verify was self-contradictory (an acceptance criterion the verify couldn't satisfy). Either way, record the change in the ticket's History under a heading naming which case it was. The distinction is not stylistic: the engine's integrity rests on the checker being the gate, so a driver editing the assertion the checker runs is a hole unless a human closes it.

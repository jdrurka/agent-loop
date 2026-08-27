---
name: plan-loop
description: Write a loop-shaped plan whose ticket manifest the driver can materialize into a queue. Use when scoping a build where verifying one thing is expected to reveal the next, when a request has to become tickets with runnable verify commands and receipts, or when an existing document needs reshaping into a manifest /run-loop can execute.
---

# Plan Loop

> Turn a request into a loop-shaped plan: the context and decisions a stranger needs, plus a `## Ticket Manifest` that `/run-loop` Phase 0 materializes into `tickets/queue.jsonl` and one `T-###.md` per row, with no second approval.
>
> **The one contract everything else serves: the manifest is an interface, not a summary.** Every column lands in a named field of the queue line the driver actually runs. A row that cannot state a runnable verify command, and the receipt that command prints when it really asserts something, is not a ticket yet. A manifest of rows like that materializes into a queue that closes tickets green having checked nothing, which is worse than no queue at all.

## Variables

`request` — what to plan. A harness that supports slash commands passes it as the argument; run by hand, read it as the thing the operator described. Ask for the missing half before writing: what already exists, what "done" looks like, and which repo the work lands in.

## Where the plan goes

`plans/YYYY-MM-DD-<kebab-name>.md` inside `<project>`, the repo that holds the work. One flat file is right; Phase 0 folds it into `plans/<name>/PLAN.md` and keeps `tickets/` beside it on the first run, so don't pre-build the folder.

Every path in the plan — territory globs, verify commands, traces — is relative to `<project>` unless the plan says otherwise. The engine's own root (wherever this repo sits: `~/agent-loop`, a plugin directory) is a different place, and verify strings spell it out in full.

## Is this the right shape?

Loop shape is for work where finishing one thing is expected to reveal the next: a repair campaign, a migration, hardening something you didn't write, an extraction whose acceptance test is a stranger trying it. Across the runs this engine has been measured on (16 runs, 341 tickets), a plan named about 54% of the work it turned out to cover. The queue is where the other 46% goes.

Work you can enumerate now, where finishing the list finishes the job, doesn't need any of this — write the list and do it. When genuinely unsure, plan it as a loop: a queue that dries up right after the manifest cost nothing, while enumerated work that keeps growing becomes scope managed by hand.

## Two header lines the driver reads

```markdown
**Execution:** loop
**Thoroughness:** velocity | rigorous
```

Phase 0 reads both before it touches anything. Without `**Execution:** loop` the driver stops rather than improvising a queue out of prose. `**Thoroughness:**` decides what happens to the work the loop discovers: `rigorous` emits P1, P2 and P3 findings as tickets, `velocity` emits P1 and P2 and parks P3 and hygiene in `tickets/RESIDUAL-NOTES.md`. A plan with no thoroughness line is treated as `rigorous`.

## Research before you write a single ticket

Read the code the plan touches. Not the directory listing, the files. A trace is worth writing only when it names a real path and, where it helps, a real line number; a plan's guess about the codebase is not the codebase, and an executor arriving at a fictional fix site burns an attempt discovering that.

Three things to come back with: the files that will change, the command that already proves something in this repo (a test runner, a lint, a script with a success token), and the boundary between the pieces, because that boundary is where tickets split.

## Plan format

```markdown
# Plan: <descriptive title>

**Created:** <YYYY-MM-DD>
**Status:** Draft
**Execution:** loop
**Thoroughness:** <velocity | rigorous>
**Request:** <one line, what was asked for>
**Purpose:** <what outcome this enables>

---

## Overview

<2-3 sentences on the end result, then why it's worth doing now>

## Current state

<What exists today, with real paths and sizes. Then the gap: what's missing, broken, or
unusable, stated concretely enough that someone could disagree with it.>

## Design decisions

<Numbered. Each one a decision plus its reasoning, including the ones you'd defend in a
review. Then alternatives considered and why they lost, and any open question that needs a
human answer before the run starts.>

## Ticket Manifest

**Scope globs:** <the outer bound of everything the tickets may touch>

<the table — see below>

## Validation checklist

<What a person checks by hand after the queue runs dry. Not a restatement of the verifies.>

## Success criteria

<Numbered, measurable, and written so someone other than you can call it.>

## Notes

<Anything that shapes execution but isn't a ticket: a pre-step the owner has to do, work
deliberately left out, a risk you're accepting.>
```

Sections the loop makes redundant, and which this format deliberately leaves out: a step-by-step task list (the tickets are the steps), a file-by-file change table (territory columns carry it), and any per-command routing.

## The ticket manifest

```markdown
| ID | Title | Priority | Deps | Trace | Acceptance criteria | Territory | Verify command | Receipt |
|----|-------|----------|------|-------|---------------------|-----------|----------------|---------|
| T-001 | <short title> | P1 | — | <entry point → path → fix site> | <1-3 checkable lines> | <exact globs, relative to the repo> | <runnable command through the verify seam> | <regex the verify's own output must match> |
```

Nine columns is wide and it stays wide. Every one of them is a field the driver needs at dispatch time, and a column dropped here becomes something a human improvises later, at the point where improvising weakens the gate.

### What each column becomes

Phase 0 writes one `queue.jsonl` line and one `T-###.md` per row. The mapping is exact:

| Manifest column | Where it lands | Notes |
|---|---|---|
| ID | `id`, and the ticket filename `T-###.md` | Sequential from T-001, no gaps |
| Title | `title`, and the ticket's `# T-###: <title>` heading | Short enough to read in a status line |
| Priority | `priority` | P1, P2 or P3. Selection orders P1 → P2 → P3, then by id |
| Deps | `deps` | Ticket ids from this same manifest. A ticket is selectable only when all of its deps are `done` |
| Trace | ticket body `## Trace` | Copied into the ticket file, not into the queue line |
| Acceptance criteria | ticket body `## Acceptance criteria` | Same: the executor reads it, the checker doesn't |
| Territory | `territory`, and ticket body `## Territory` | Globs relative to the ticket's repo, because that's what `git diff --name-only` returns |
| Verify command | `verify`, and ticket body `## Verify command` | Run verbatim through a shell. It is the gate |
| Receipt | `verify_expect` | A regex matched against the verify's output with `re.M` |
| *(optional)* Repo | `repo` | Only for a plan spanning repos. A path, absolute or relative to `<project>`. Absent means `<project>` itself |

The fields you never plan, because the driver owns them: `status`, `claimed_by`, `claimed_at`, `base_sha`, `attempts`, `origin`, `created`, `closed`, `model`, and every gate field. `blocked_on` is the driver's too, with one exception worth knowing about: a ticket carrying a genuine should-we-build question is better written as an open question in Design decisions than smuggled into the queue as a ticket nobody can run.

P0 is never a planned priority. It's the escalation class an executor reports when it finds data corruption, a security hole, or a race that loses data.

## The verify command

A ticket without a runnable verify is not a ticket. That's the line the whole engine rests on, because the checker re-runs this exact string and reads the result; nothing else moves a ticket to `done`.

Shape it like this, with each territory glob passed as its own `--path`:

```
bash ~/agent-loop/tools/verify_seam.sh --repo . --path src/thing.py --path tests/test_thing.py --run 'python3 -m pytest tests/test_thing.py -q'
```

Four properties, and a row is not finished until it has all four.

**Fully materialized, no placeholders.** The command runs verbatim through a shell, so a leftover `<engine>` or `<the auth file>` is a redirect that dies before the seam starts and produces a failure that looks nothing like the ticket. The checker refuses such a string on attempt 1 by design, and the manifest is where it should never have existed. Write real paths, including the engine root — where that root actually sits depends on how the engine was installed, and the README's Install section is where you get its value for this machine.

**It names a path from its own row's territory.** A verify copied from a sibling ticket and left un-retargeted runs, passes, and closes the ticket green having asserted nothing whatsoever about this ticket's work. Read every verify string against the territory column beside it, on the same row, and confirm the assertion actually touches that file.

**It runs against a throwaway seam.** A temp directory, a scratch database, a random free port, fixtures. Never a live port, a live database, or an API that bills or writes. The guard refuses known live credential names and any uncommitted file in the claimed territory, but it can't tell a scratch database from production. If the target has no throwaway seam today, building one is ticket zero and it comes first in the manifest.

**It's honest about ordering.** When a ticket's assertion only holds after a dependency's change lands (its table exists, its function is importable, its file was written), materialize the verify as the ordered chain the seam already accepts, listing the contributing steps explicitly. The checker infers no ordering and never will — ordering is the plan's job.

## The receipt

This is the column people skip, and skipping it is how a queue quietly stops meaning anything.

An exit code measures "no command errored", not "the assertion happened". A shell script without `set -e` exits 0 after every line in it failed. An assert that returns zero rows exits 0. A grep whose failure is swallowed by an `or true` guard exits 0 having matched nothing. Each of those closes a ticket on a green checker.

So every ticket carries a regex its verify's own output must match. `\d+ passed` is a receipt: only a run that collected and passed tests can print it. `0 failed` is not, because a run that collected nothing prints it too. Neither is an empty pattern, `.*`, the ticket's own id, or anything a skipped run also emits.

| Verify runner | Receipt | Why the obvious alternative fails |
|---|---|---|
| `pytest -q` | `\d+ passed` | `0 failed` and `no errors` both print on a run that collected nothing |
| a shell script | the explicit token it echoes on success (`MIGRATION_APPLIED`) | without `set -e`, exit 0 says nothing about the lines above |
| a chain of greps | a marker echoed after the last one (`CONFIG_CLEAN`) | a swallowed grep failure still exits 0 |
| a SQL assert | the count or token the assert prints | zero rows returned is a successful query |
| a build or a compile | the artifact's own success line | a cached no-op build exits 0 too |

If the command prints nothing worth matching, add a success token to it as part of the ticket. A script with no success output has no receipt available, and that's a defect in the script rather than a reason to leave the column empty.

## Territory

Globs, relative to the ticket's repo, naming exactly what this executor may modify. The ticket's own `T-###.md` is always implicitly writable, so don't list it.

Prefer exact files (`src/auth/session.py`) over directories. Prefer a directory over a recursive glob. And **two tickets must never share a recursive directory glob** — `**`, or anything ending `/**`. The checker's queue lint reports that as a shared directory glob and it's right to: the checker decides attribution by matching every changed path against the claiming ticket's territory, so two tickets owning one subtree can each sweep the other's in-flight files, and the verdicts stop describing reality.

Two rows that genuinely need the same file are one ticket, or one of them depends on the other and edits it afterwards. Splitting territory is what makes parallel work safe, so split the work along the territory boundary rather than the other way round.

## Acceptance criteria

One to three lines, checkable by someone who wasn't in the room. "Handles errors properly" is aspirational. "Every non-200 response returns the wrapped error type and logs once" is checkable.

**Assert the behaviour you want, not a cheaper proxy for it.** A real ticket from this engine's own history asked for a manifest file that was "valid JSON naming two skills". The delivered file was valid JSON and it named two skills. It still didn't load, because the loader wanted paths and the file carried names. The criterion measured a property adjacent to the one that mattered, the verify agreed, and the ticket closed on a broken artifact. When what you want is "the loader loads it", both the criterion and the verify say exactly that.

**A check checks what it names and nothing else.** Another ticket's verify grepped for three forbidden strings, passed, and the file still carried three other private names nobody had listed. Enumerate the entire set, or assert the general property (a pattern class, a schema, a loader's own verdict) rather than a hand-picked sample of it. A sampled check is fine when you say so in the criteria; it's dangerous when you and the reader both think it's exhaustive.

## Deps

`deps` are ticket ids, and they gate selection: a ticket becomes runnable only when every dep is `done`. Use them for two things and nothing else.

Real precondition: T-004 cannot be written until T-002's function exists. And verify chaining: T-006's assertion only holds once T-003's change has been applied, so the verify runs both in order and the Deps column is what says so.

Don't add deps to express a preferred running order or your own comfort. Every dep narrows the ready set, and a chain of ten tickets each depending on the last is a queue that can never work on two things at once.

## Before you hand it over

Walk the manifest once, per row, and answer all six. A row that fails any of them isn't a ticket yet.

1. Does the verify string run as written from `<project>`, with nothing left inside angle brackets?
2. Does the verify name at least one path from this row's own Territory column?
3. Would the receipt appear only when the assertion actually ran, and not on a skipped or collected-nothing run?
4. Are the territory globs exact, and does no recursive directory glob appear on two rows?
5. Do the acceptance criteria and the verify describe the same thing?
6. Is every entry in Deps an id that exists in this manifest?

Then read the plan the way a person who has never seen this codebase would. The context sections exist for the executor arriving with an empty context window, and vague ones cost attempts.

## Rules (non-negotiable)

- **A ticket without a runnable verify is not a ticket.** Not a smaller ticket, not a ticket with a note attached. It doesn't go in the manifest.
- **Every ticket carries a receipt.** The column is not optional, and the driver derives one at materialization if the plan ships without it — badly, because it can only guess from the runner.
- **The manifest is approved once, with the plan.** Phase 0 materializes it with no second approval, so anything you'd want a human to look at goes in Design decisions as an open question, before the run.
- **No placeholder survives into a verify string.** Angle brackets in a shell command are redirects.
- **One recursive directory glob, one owner.**
- **The plan doesn't write the code.** It names the fix site and what "done" means. The executor writes its own implementation plan against the codebase as it actually is at claim time, which is usually not how the plan imagined it.

## Report

After writing the plan:

1. The file path.
2. Two sentences on what it does.
3. The ticket count and the priority split, plus any row you're least confident about.
4. `Run /run-loop plans/<name>.md when ready.`

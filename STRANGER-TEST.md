# Stranger test

A run of this repo as someone who has never seen the machine it was written on: cloned from the
public remote into a throwaway directory, both install paths exercised, one full loop driven end to
end against the bundled demo.

Everything below was executed. Nothing is projected. Where a path is machine-specific it is written
as `<clone>` (the throwaway clone of this repo) or `<demo>` (the copy of `examples/demo-project`
that the loop was run against). Both were disposable directories outside any real project.

Date of run: 2026-08-27. Clone at `8e7b2fc`. Host: macOS 24.1, git 2.x, Python 3.14.7 on PATH,
`ffmpeg` and `claude` both present.

---

## Summary

The engine works. A fresh clone drove three tickets to `done` on checker verdicts, the demo's
median ticket was refused by the checker on a partial fix and closed only after a real one, and the
plugin path resolves both skills. The claim this repo makes is true as written.

What a stranger also hits: **three blocking friction points and three high-severity gaps between
what the README says and what the code does.** None of them are hard to fix and all of them are the
kind that lose a reader in the first ten minutes.

| Severity | Count |
|---|---|
| Blocking | 3 |
| High | 3 |
| Medium | 5 |
| Low | 5 |

---

## What was run

### 1. Clone from the public remote

```bash
git clone https://github.com/jdrurka/agent-loop.git <clone>
```

Worked. 34 files, HEAD `8e7b2fc`. Nothing needed was left untracked: every file the README, the
skills and the demo reference is in the clone. `tools/verify_seam.sh` and
`tools/tests/test_verify_seam_credentials.sh` carry their executable bits;
`scripts/loop_check.py` and `scripts/loop_voice.py` do not, which is correct because every
documented invocation goes through `python3`.

### 2. The engine's own test suites

```bash
cd <clone>
python3 -m pytest scripts/tests/test_loop_voice.py -q     # 98 passed in 0.16s
python3 -m pytest -q                                       # 98 passed in 0.21s
bash tools/tests/test_verify_seam_credentials.sh           # PASS, exit 0
```

All three green. The README's "98 tests, about a tenth of a second, no network" is accurate to the
test. Bare `pytest` at the repo root also passes, so `examples/conftest.py` does keep the demo's
deliberately-red spec out of collection, exactly as `examples/README.md` claims.

### 3. Guard behaviour, against the README's exit-code table

```bash
bash <clone>/tools/verify_seam.sh --repo /tmp --path x --run 'echo hi'
# verify_seam.sh: refusing non-git repo: /tmp                              exit 65

env OPENAI_API_KEY=sk-not-real bash <clone>/tools/verify_seam.sh --repo . \
  --path demo/stats.py --run 'echo hi'
# verify_seam.sh: refusing live credential environment: OPENAI_API_KEY is set   exit 66

# with an uncommitted edit in claimed territory:
bash <clone>/tools/verify_seam.sh --repo . --path demo/stats.py --run 'echo hi'
# verify_seam.sh: refusing uncommitted verify inputs in claimed territory:
#  M demo/stats.py                                                          exit 67
```

Every code matched the README's table, and every message names the specific offender. This is the
best-documented part of the repo.

### 4. Notifier, unconfigured

```bash
env HOME=<scratch> python3 <clone>/scripts/loop_voice.py --prove --run-context "setup/first-check"
# loop_voice: unreadable or invalid Fish config at <scratch>/.config/fish-audio/speak.json
# exit 3
```

Exactly as documented: exit 3, and it names the path it could not read. No network call was made
and no real config on the machine was touched.

### 5. Plain-clone path: one full loop, end to end

Set up per `examples/README.md`:

```bash
cp -R <clone>/examples/demo-project <demo>
cd <demo>
git init && git add . && git commit -m "starting state"

python3 -m demo.cli    # IndexError, on the blank line in data/timings.csv
python3 -m pytest -q   # 6 failed, 11 passed
```

Both starting-state outputs matched `examples/README.md` exactly.

**Phase 0.** The demo ships its plan at the project root as `PLAN.md`, so
`skills/run-loop/SKILL.md` Phase 0 step 2 (which folds `plans/<name>.md`) does not literally apply.
Following `examples/README.md` instead, the plan was folded to `plans/PLAN/PLAN.md`, which makes
the plan slug `PLAN` and every executor commit prefix `loop(PLAN/T-00N):`. `queue.jsonl` and three
ticket files were materialized from the manifest, with the `<engine>` root written out to the real
clone path per step 5b and the manifest's `\d+ passed` receipt stamped per step 5c.

```bash
python3 <clone>/scripts/loop_check.py --lint-queue   --plan-dir plans/PLAN   # {"ok": true}   exit 0
python3 <clone>/scripts/loop_check.py --sweep-gates  --plan-dir plans/PLAN   # {"swept": []}  exit 0
```

**Phase 1.** Each ticket: pre-dispatch re-validation through the guard, claim with `base_sha`, work
the ticket, commit by explicit pathspec under the plan-scoped prefix, guarded verify, then driver
adjudication in the shape `skills/run-loop/SKILL.md` Phase 1 step 4 specifies:

```bash
bash <clone>/tools/verify_seam.sh --repo . --path demo/stats.py \
  --run "cd <demo> && python3 <clone>/scripts/loop_check.py \
         --plan-dir plans/PLAN --ticket T-002 --repo ."
```

**T-002 is the one that matters, and it behaved.** Attempt 1 was a deliberately partial fix — the
even-count branch only, which is what an executor writes if it reads the loudest failure and stops.
The checker refused it:

```
exit_code = 1   verify = fail   ("1 failed, 6 passed")
queue after adjudication: T-002 active attempts=1
History: <!-- loop-attempt:T-002:1 -->
         - Attempt 1 checker failure: verify exit 1; verdict exit 1.
```

The checker did all of it atomically and without being asked: returned the ticket to `active`,
incremented `attempts`, wrote the failing block into `T-002.verify.log`, and appended a
marker-keyed History receipt. Note the receipt regex `\d+ passed` *did* match that output
("6 passed") — the refusal came from the verify's exit code, which is the AND the checker
documents. Attempt 2 added the empty-input guard and closed on `exit_code = 0`, `7 passed`.

Final state, and the plan's own validation checklist:

```
T-001 done attempts=0      T-002 done attempts=1      T-003 done attempts=0

python3 -m demo.cli    -> 6 steps, 17.0s total, 2.8s mean, 2.5s median   (exact match)
python3 -m pytest -q   -> 17 passed                                       (exact match)
git diff --stat <start> HEAD -- tests/  -> no output; tests/ byte-identical
```

Every item on the checklist held, including the `2.5s median` that only appears if T-002 really
landed. `tests/` was never in any ticket's territory and no ticket touched it.

### 6. Two extra gate probes

Neither is part of the demo. Both were run afterwards, in the throwaway copy, to test the repo's
headline safety claim rather than take it on trust.

**T-004** — a ticket whose declared territory is `tests/test_stats.py`, committing a change to that
file. Expected an `assertion_change` gate. Got:

```
exit_code = 0   blast_radius = []   gate_class = None
```

**T-005** — a ticket whose declared territory is `demo/auth_helper.py`, matching `[blast] **/*auth*`.
Got:

```
exit_code = 2   blast_radius = ['demo/auth_helper.py']
gate_class = blast_auth_surface   gate_policy = ttl
```

So the gate does fire, and it fires on the diff rather than the description. But it fires only for
paths in the `[blast]` section. See findings H2 and H3.

### 7. Plugin path

```bash
claude plugin validate <clone>
# ⚠ plugins[0] plugin.json → author: No author information provided
# ✔ Validation passed with warnings                                        exit 0

claude --plugin-dir <clone> plugin details agent-loop
#   Source: agent-loop@inline
#   Skills (2)  plan-loop, run-loop
#   plan-loop  ~130 always-on  ~5.5k on-invoke
#   run-loop   ~140 always-on  ~18.6k on-invoke

claude --plugin-dir <clone> --model haiku -p '...list the skills the agent-loop plugin provides...'
# /agent-loop:plan-loop
# /agent-loop:run-loop

claude --plugin-dir <clone> --model haiku --permission-mode plan -p '/run-loop no-such-plan.md'
# "The plan file no-such-plan.md doesn't exist — I can't run the loop skill against a plan
#  that isn't there."
```

Both skills resolve, by the namespaced form and by the bare short name, and the driver skill
correctly refuses a plan that isn't there. The README's claim about namespacing is accurate.

**No residue.** `~/.claude/plugins/installed_plugins.json` and `known_marketplaces.json` contain no
`agent-loop` entry, no marketplace directory was created, and `settings.json` is byte-identical
before and after (sha256 unchanged). The only trace is a usage counter (`agent-loop@inline`,
`agent-loop:run-loop`) that the CLI writes for any skill invocation. Nothing was installed and
nothing was configured.

---

## Findings

Severity is about what it costs a stranger, not how hard it is to fix.

- **Blocking** — a first-time reader stops here until they debug the repo themselves.
- **High** — a documented guarantee that does not hold, or a silent wrong answer.
- **Medium** — real friction, worked around by rereading or guessing.
- **Low** — inaccuracy or rough edge.

### Blocking

**B1. On a stock Mac, `python3` is 3.9 and `loop_check.py` dies with an unreadable traceback.**
macOS ships `/usr/bin/python3` at 3.9.6 and does not ship 3.10+. The README's Requirements section
does say "Python 3.10 or newer" and even explains why, but nothing in the code says so when it
matters:

```
$ /usr/bin/python3 <clone>/scripts/loop_check.py --lint-queue --plan-dir plans/PLAN
  File ".../scripts/loop_check.py", line 171, in <module>
    def validate_verify(cmd: str | None) -> list[str]:
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
exit 1
```

There is no `sys.version_info` guard anywhere in the file. That traceback tells a newcomer nothing.
It arrives at the first adjudication, after they have already set up a plan and a queue, and every
documented invocation is bare `python3`, so it hits whatever the shell resolves. `loop_voice.py`
runs fine under 3.9, which makes it look like a checker bug rather than an interpreter version.
Fix: a version guard at the top of `loop_check.py` printing the requirement and the version found.

**B2. The documented close pathspec cannot be executed against the shipped `.gitignore`.**
`skills/run-loop/SKILL.md` Phase 1 step 4 says: "Commit that close by exact filenames —
`queue.jsonl` plus the closing ticket's own files (`T-###.md`, `T-###.verify.log`,
`T-###.verdict.json`), nothing else." Both `.gitignore` in this repo and
`examples/demo-project/.gitignore` contain `*.verify.log`. The first ticket close of the first
loop:

```
$ git add -N plans/PLAN/tickets/T-001.verify.log
The following paths are ignored by one of your .gitignore files:
plans/PLAN/tickets/T-001.verify.log

$ git commit -m "..." -- ... plans/PLAN/tickets/T-001.verify.log ...
error: pathspec 'plans/PLAN/tickets/T-001.verify.log' did not match any file(s) known to git
exit 1
```

The whole close commit fails, not just that path. Either the skill should stop naming the verify
log, or the demo's gitignore should stop ignoring it. Right now the repo contradicts itself on the
first close a stranger performs.

**B3. Under a plugin install there is no documented way to learn `<engine>`.**
The README leads with the plugin install. Every verify string in every plan must contain the
absolute path to `<engine>/tools/verify_seam.sh`, and `skills/plan-loop/SKILL.md` is emphatic that
it must be fully materialized: "a leftover `<engine>` … is a redirect that dies before the seam
starts". `skills/run-loop/SKILL.md` defines `<engine>` as "the installed plugin directory under a
plugin install" — and then nothing, anywhere in the repo, says how to find that directory.
`CLAUDE_PLUGIN_ROOT` does not appear in any file. So the install path the README recommends first
is the one that cannot produce a runnable verify string. The plain clone works because the reader
chose the path themselves.

### High

**H1. A checker that crashes leaves the previous verdict file in place, and the protocol says to
trust it.** `skills/run-loop/SKILL.md` Phase 1 step 4: "**Read the outcome from
`tickets/{id}.verdict.json` (`exit_code` field), never from a shell capture** — a piped `$?` has
already lied once." Measured, on a ticket whose previous verdict was a legitimate `exit_code: 0`:

```
verdict before crash:  exit_code= 0   head= 53c4a23...
<checker invoked under Python 3.9; crashes at import>
seam-wrapped exit code = 1
verdict after crash:   exit_code= 0   head= 53c4a23...     (mtime unchanged)
```

The checker died before writing anything. A driver obeying its own rule reads `exit_code: 0` from a
run that never happened and closes the ticket. Any crash does this — a bad interpreter, a syntax
error introduced into the checker, a permissions problem on the plan directory. The data needed to
catch it is already in the file (`material_digest.head` still names the old HEAD), but nothing in
the protocol tells the driver to compare it. Fix: have the driver require the verdict's HEAD to
match the current one, or have the checker write a failure verdict from a top-level handler, or
both.

**H2. The `[assertion]` section of `config/blast-globs.txt` never gates anything, and the shipped
config says the opposite in its own comments.** `blast_radius` is computed only against the
`[blast]` section (`loop_check.py` lines 1191-1192); `[judge]`, `[auth]` and `[assertion]` are
classifiers applied *to paths already in that set* (`derive_gate_class`). Since no `tests/**` glob
appears under `[blast]`, the `assertion_change` class — the only one besides shipped-migration
edits and correlated failures with `policy: block` — is unreachable under the shipped defaults.
Proven by probe T-004 above: a commit whose only path was `tests/test_stats.py` closed at exit 0
with an empty blast radius.

This appears to be intentional; the `[judge]` comment says "test DIRS stay open … gating them is a
tax the mission filter rejects". The problem is that two other places say the reverse. The
`[assertion]` comment, eight lines further down the same file: "Editing what a test asserts, while
that test is the gate, is the gate-weakening direction; **it blocks** rather than clearing on a
receipt." And the README: "It answers a single question: which changes are unrecoverable enough
that a ticket touching them stops for a human. Four sections, `[blast]`, `[judge]`, `[auth]` and
`[assertion]`, one glob per line." A reader tuning this file for their own repo will put globs in
`[assertion]` believing they gate. They do not.

**H3. Auth, token and secrets paths gate on a 24-hour TTL that clears itself and tells nobody.**
Probe T-005 returned `gate_class: blast_auth_surface`, `gate_policy: ttl`. The policy table gives
that class `ttl_hours: 24`, and `skills/run-loop/SKILL.md` says a `ttl` gate is swept to `done`
automatically at select time and "Tell nobody." The README describes the same globs under "which
changes are unrecoverable enough that a ticket touching them stops for a human", and lists
"anything matching `*auth*` or `*token*` or `*secrets*`". It stops for a human for 24 hours and
then approves itself. Only `blast_shipped_migration_edit`, `assertion_change` and
`correlated_failure` are `block`. The behaviour may be the right default; the description of it is
not.

### Medium

**M1. The README never mentions `examples/`.** Not once — no "demo", no "example", no link. The
demo is the single most persuasive thing in this repo: a project that ships red on purpose, with a
median that is wrong the way medians are actually wrong, and a checker that visibly refuses to
close it. `examples/README.md` is excellent and nobody arriving at the front page will ever see it.
For a repo whose job is to convince a stranger in two minutes, this is the largest single loss in
the whole test.

**M2. Requirements understates pytest.** "**pytest**, only if you want to run the notifier's test
suite." All three of the demo's verify commands are `python3 -m pytest`, and `examples/README.md`
says "`python3` and `pytest` are the whole toolchain". Anyone who reads Requirements and skips
pytest cannot run the demo.

**M3. The "What's in here" map omits two files that matter.** `skills/run-loop/standards.md` is
required reading for every executor (`skills/run-loop/SKILL.md` step 2.2 calls it "required — the
executor obligations and coding standards"), and it is not in the map. That matters most for the
use the README explicitly invites — "hand your agent `~/agent-loop/skills/run-loop/SKILL.md`" on a
non-Claude-Code harness — where a reader porting the driver would not know `standards.md` has to
travel with it. `tools/tests/test_verify_seam_credentials.sh` is also missing, even though
`verify_seam.sh`'s own comment says "Run it after touching the list."

**M4. The demo's plan is not where Phase 0 expects a plan.** It ships at the project root as
`PLAN.md`. Phase 0 step 2 folds `plans/<name>.md` into `plans/<name>/PLAN.md`; a root-level
`PLAN.md` matches neither the flat nor the folded form, so the driver has to improvise.
`examples/README.md` concedes this ("folds the plan into `plans/PLAN/` — or wherever you put it"),
but "wherever you put it" decides the plan slug, and the plan slug is the commit prefix the checker
attributes by. Landing on `plans/PLAN/` gives every executor the prefix `loop(PLAN/T-00N):`, which
is not obviously a plan name. Shipping the demo plan at `plans/build-timings/PLAN.md` would remove
the improvisation and give the demo a slug worth seeing.

**M5. In the two-root layout, the judge-file globs cannot match.** `[judge]` lists
`tools/verify_seam.sh` and `scripts/loop_check.py` as literal repo-relative paths. The checker
computes the blast radius from `git diff` inside `--repo`, which is `<project>`. Under the
documented plain-clone install those two files live in `<engine>`, a different repository, so those
globs match nothing and an edit to them is invisible to adjudication entirely. Territory rules
already forbid an executor from going there, but the README's "the judge files themselves … because
a ticket that can edit its own gate can close green having weakened it" is only enforced when
engine and project are the same repo. Worth stating in the README, since the two-root split is the
documented default.

### Low

**L1. `T-###.verdict-attempt-N.json` is never committed.** The checker writes one per adjudication
and `skills/run-loop/SKILL.md` Artifacts describes it as the reason attempt history survives the
rewrite — but the close pathspec names only `T-###.md`, `T-###.verify.log` and
`T-###.verdict.json`. Following the skill literally leaves every attempt file untracked forever.
Four of them were sitting untracked at the end of this run.

**L2. Nothing tells the driver to write History on a clean close.** Phase 1's exit-0 branch mentions
History only for `gate_policy: receipt`. Phase 2's quiescence audit then requires that "an executed
ticket must have a committed verdict with `exit_code: 0` and **non-empty History**". A driver
following Phase 1 to the letter fails its own Phase 2 audit on every ticket that closed first try.

**L3. `plugin.json` has no `author`.** `claude plugin validate` warns about it, and the plugin
browser shows no attribution for a repo whose README ends with a byline.

**L4. The demo's validation checklist has an off-by-one check.** `PLAN.md` says "`git log
--oneline` shows one commit prefix per ticket and no commit touching two tickets' files", and
`examples/README.md` says "Check it afterwards: `git log -- tests/` should be empty." It is not
empty — it lists the `starting state` commit that created `tests/` in the first place. The check
that means what it intends is `git log --oneline <starting-sha>..HEAD -- tests/`.

**L5. Three hardcoded `~/agent-loop` paths before the first run.** The demo's manifest spells the
engine root as `~/agent-loop` in all three verify commands, and `PLAN.md`'s Notes flag it honestly.
It is still three edits before anything runs for a reader who cloned elsewhere, and the failure if
they forget one arrives from inside a verify as `bash: <home>/agent-loop/tools/verify_seam.sh: No
such file or directory`, which looks like a broken engine rather than a path they were told to
change.

---

## What held up

Worth recording as much as the failures, because these are the claims the repo is actually staking
itself on and they were all true.

- **The checker is the only path to `done`.** T-002 attempt 1 was a real, plausible, partial fix. It
  was refused: exit 1, `attempts` incremented, verify log written, History receipt appended, ticket
  back to `active`, all atomically and without a driver decision. Nothing about the executor's own
  account of the work entered the outcome.
- **A ticket cannot edit the spec it is judged by.** T-002's territory was `demo/stats.py` alone.
  `tests/test_stats.py` was byte-identical at the end of the run.
- **The guard's five exit codes are exactly what the README says**, and each message names the
  offender rather than the rule.
- **Attribution by exact plan-scoped prefix worked** across nine commits in one repository,
  including a two-attempt ticket and interleaved driver bookkeeping commits.
- **The receipt did its job.** `\d+ passed` was stamped on every ticket at materialization and
  matched real output on every close.
- **The demo's starting state and finishing state are byte-accurate to their documentation** —
  `6 failed, 11 passed` before, `17 passed` and `6 steps, 17.0s total, 2.8s mean, 2.5s median`
  after.
- **Zero install, zero dependencies, zero network.** Nothing was built, no virtualenv, no package
  manager, and the only optional binaries the run touched were the ones the README calls optional.
- **The plugin path leaves nothing behind.** Session-scoped `--plugin-dir` created no marketplace
  entry, no installed-plugin entry, and no settings change.

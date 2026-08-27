# Plan: Finish the build-timings summariser

**Created:** 2026-08-27
**Status:** Draft
**Execution:** loop
**Thoroughness:** velocity
**Request:** Make `python3 -m demo.cli` produce a correct one-line summary of `data/timings.csv`.
**Purpose:** A three-ticket loop small enough to watch end to end, where one ticket really does fail its gate.

---

## Overview

This project reads a CSV of build-step timings and prints one line about them. It ships as a
spec-first tree: `tests/` is complete and describes exactly what the three modules owe, and the
modules themselves are unfinished. Three tickets finish them.

It is worth running because of what happens in the middle of it. `demo/stats.py` ships with a
median that is wrong in the ordinary way medians are wrong, the spec catches it, and the executor
that fixes it cannot touch the spec. Watching a gate refuse work is more instructive than watching
three tickets close green.

## Current state

The tree, apart from this plan:

```
data/timings.csv       8 rows, two of them deliberately messy
demo/parse.py          parse_timings(text) -> list[float]
demo/stats.py          total, mean, median
demo/report.py         summarise(text) -> str
demo/cli.py            python3 -m demo.cli [path]
tests/test_parse.py    5 assertions, the spec for parse
tests/test_stats.py    7 assertions, the spec for stats
tests/test_report.py   5 assertions, the spec for report
```

`python3 -m demo.cli` does not run today. `parse_timings` indexes `row.split(",")[1]` on every line
after the header, so the blank line in `data/timings.csv` raises `IndexError` and the `package,n/a`
row would raise `ValueError` right behind it. The three gaps, one per module:

1. **`parse_timings` crashes on anything but a clean file.** Blank lines, rows with no seconds
   field, and rows whose seconds are not a number all abort the parse instead of being skipped.
2. **`median` is wrong for an even number of values, and wrong about empty input.**
   `sorted(values)[len(values) // 2]` returns 3.0 for `[1, 2, 3, 4]` where the middle pair averages
   to 2.5, and it raises `IndexError` on `[]` where `mean` — sitting directly above it in the same
   file — raises `ValueError`.
3. **`summarise` never mentions the median.** It reports count, total and mean, and stops.

## Design decisions

1. **The tests ship, the implementations do not.** Spec-first, so every ticket's verify is a gate
   written before the work rather than alongside it. It also means the whole suite is red at the
   starting state, which is normal for this project and not a sign anything is broken.

2. **One source file per ticket, and no ticket owns its own spec.** T-002's territory is
   `demo/stats.py` alone. The executor can read `tests/test_stats.py` and cannot write it. If a
   ticket could edit the test it is judged by, a green verify would mean nothing, and the median
   ticket is exactly where that would show.

3. **The naive median is a real first attempt, not a trap.** `sorted(values)[len(values) // 2]` is
   what people write when they write a median from memory. It is correct for odd counts and for a
   single value, so it passes casual inspection and 5 of the 7 assertions in its spec. The two it
   fails are the two it should. The ticket's acceptance criteria state both plainly — nothing is
   hidden from whoever works it.

4. **T-003 depends on T-002 for a real reason.** The four steps in `tests/test_report.py` have a
   mean of 3.0 and a median of 2.5. The assertion that the summary contains `2.5s median` cannot
   pass while `median` still returns the upper middle value, so the dependency is a precondition
   rather than a preference.

5. **`total` and `mean` ship correct and tested.** They are the in-file precedent for how this
   project handles empty input, and they make the median's missing guard an inconsistency an
   attentive executor can see without being told twice.

6. **Standard library only, no install step, no network.** `python3` and `pytest` are the whole
   toolchain, so the demo runs the same on a stranger's machine as it does on the author's.

*Alternative considered:* letting each ticket write its own tests alongside its module, which is
the usual shape. It was dropped because the demo's whole point is a gate the worker cannot move.

*Open question, answered before the run:* none. Every ticket below is buildable as written.

## Ticket Manifest

**Scope globs:** `demo/**`, `tests/**`

| ID | Title | Priority | Deps | Trace | Acceptance criteria | Territory | Verify command | Receipt |
|----|-------|----------|------|-------|---------------------|-----------|----------------|---------|
| T-001 | parse_timings survives a messy CSV | P1 | — | `python3 -m demo.cli` → `demo/report.py:summarise` → `demo/parse.py:parse_timings`, the list comprehension that indexes `row.split(",")[1]` unguarded | A blank line is skipped rather than raising. A row with no seconds field is skipped. A row whose seconds field is not a number is skipped. Every well-formed row is still returned, in file order, as a float. | `demo/parse.py` | `bash ~/agent-loop/tools/verify_seam.sh --repo . --path demo/parse.py --run 'python3 -m pytest tests/test_parse.py -q'` | `\d+ passed` |
| T-002 | median is correct for even counts and for empty input | P1 | — | `tests/test_stats.py` → `demo/stats.py:median`, `sorted(values)[len(values) // 2]` | `median([1, 2, 3, 4])` is 2.5, the average of the middle pair. `median([7])` is 7.0 and odd counts keep returning the middle value. `median([])` raises `ValueError`, matching `mean` directly above it. | `demo/stats.py` | `bash ~/agent-loop/tools/verify_seam.sh --repo . --path demo/stats.py --run 'python3 -m pytest tests/test_stats.py -q'` | `\d+ passed` |
| T-003 | the summary line reports the median | P2 | T-002 | `tests/test_report.py` → `demo/report.py:summarise`, the f-string that stops after the mean | The summary ends with the median, formatted to one decimal place like the total and the mean, as `<n>s median`. Count, total and mean keep their current wording and order. A file with no timing rows still returns `no timings`. | `demo/report.py` | `bash ~/agent-loop/tools/verify_seam.sh --repo . --path demo/report.py --run 'python3 -m pytest tests/test_report.py -q'` | `\d+ passed` |

## Validation checklist

- `python3 -m demo.cli` runs to completion and prints
  `6 steps, 17.0s total, 2.8s mean, 2.5s median`. Two of the eight rows in the CSV are skipped, and
  a median of 2.5 rather than 3.0 is what says T-002 actually landed.
- `python3 -m pytest -q` is green across all three files, 17 assertions.
- `git log --oneline` shows one commit prefix per ticket and no commit touching two tickets' files.
- `tests/` is byte-identical to its starting state. No ticket had it in territory, so no ticket
  should have changed it.

## Success criteria

1. All three tickets reach `done` on a checker verdict, not on an executor's report.
2. T-002's verify fails before its fix lands and passes after, and the failure names the even-count
   and empty-input assertions rather than anything else.
3. The CLI prints the exact line in the validation checklist.
4. No file outside `demo/` changed.

## Notes

- **Run this as its own git repo.** The loop attributes work by commit, so copy the project out of
  the agent-loop clone, `git init`, and commit the starting state before starting the driver.
  `examples/README.md` has the four commands.
- The verify strings above assume the engine is cloned at `~/agent-loop`, which is where the repo's
  install instructions put it. If yours is somewhere else, that path is the one thing to adjust, in
  all three rows.
- Deliberately out of scope: reading the CSV with the `csv` module, which is the right call in a
  real project and would replace the parsing this demo is about; and any output format beyond the
  single summary line.

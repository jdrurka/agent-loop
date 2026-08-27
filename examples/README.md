# examples

One example, and it exists to show you the part of this engine that is hard to believe from a
README: a ticket failing its gate, and the checker refusing to close it.

```
demo-project/    a build-timings summariser with three tickets, one of which is wrong on purpose
```

## What the demo is

`demo-project/` reads a CSV of build-step timings and prints one line about them. Ten files, under
200 lines of Python, standard library only, no install step and no network. It ships **spec-first**:
`tests/` is complete and describes exactly what the three modules owe, and the modules are
unfinished. Its `PLAN.md` carries a three-row ticket manifest that finishes them.

Everything in it is red at the starting state. That is not a broken checkout, it is the point.

## Run it

The loop attributes work by commit, so the demo needs to be its own git repo. Copy it out of this
clone rather than working inside it:

```bash
cp -R ~/agent-loop/examples/demo-project ~/demo-project
cd ~/demo-project
git init && git add . && git commit -m "starting state"
```

Look at the starting state before you start the driver. Both of these are worth seeing:

```bash
python3 -m demo.cli      # IndexError, on the blank line in data/timings.csv
python3 -m pytest -q     # 6 failed, 11 passed
```

Then hand the driver the plan:

```
/run-loop PLAN.md
```

Phase 0 folds the plan into `plans/PLAN/` — or wherever you put it — materializes
`tickets/queue.jsonl` and three `T-00N.md` files, and starts working. Three tickets at a default
budget of 15 dispatches; it finishes in a few minutes.

When it runs dry:

```bash
python3 -m pytest -q     # 17 passed
python3 -m demo.cli      # 6 steps, 17.0s total, 2.8s mean, 2.5s median
```

## What to watch for

**T-002.** The other two tickets are ordinary work. T-002 is the one to keep your eye on.

`demo/stats.py` ships with this:

```python
def median(values):
    """Return the middle value of `values`."""
    return sorted(values)[len(values) // 2]
```

That is what a median looks like when it is written from memory, and it is wrong twice. For an even
number of values there is no single middle value, so `[1, 2, 3, 4]` has a median of 2.5 and this
returns 3.0. And on an empty list it raises `IndexError`, where `mean` — six lines above it in the
same file — raises `ValueError`. It is right for odd counts and right for a single value, so it
survives a glance, and it passes 5 of the 7 assertions in its spec.

Nothing about it was written to fail. It is the near-miss that happens in real work: correct in the
cases you thought of, wrong in the one you didn't.

**The gate is not editable by the work.** T-002's territory is `demo/stats.py` and nothing else.
`tests/test_stats.py` is outside it, so the executor working that ticket can read the spec and
cannot change it. That is the reason a green verify on T-002 means anything at all. Check it
afterwards: `git log -- tests/` should be empty.

**This is the failure, verbatim.** It is what the guarded verify prints against the starting state:

```
$ bash ~/agent-loop/tools/verify_seam.sh --repo . --path demo/stats.py \
    --run 'python3 -m pytest tests/test_stats.py -q'
....F.F                                                                  [100%]
=================================== FAILURES ===================================
____________ test_median_of_an_even_count_averages_the_middle_pair _____________

    def test_median_of_an_even_count_averages_the_middle_pair():
>       assert median([1.0, 2.0, 3.0, 4.0]) == 2.5
E       assert 3.0 == 2.5
E        +  where 3.0 = median([1.0, 2.0, 3.0, 4.0])
...
E       IndexError: list index out of range

demo/stats.py:22: IndexError
=========================== short test summary info ============================
FAILED tests/test_stats.py::test_median_of_an_even_count_averages_the_middle_pair
FAILED tests/test_stats.py::test_the_median_of_nothing_is_an_error - IndexErr...
2 failed, 5 passed in 0.03s
```

Exit 1. `loop_check.py` returns the ticket to `active`, increments `attempts`, writes the block
above into `tickets/T-002.verify.log`, and records the miss in the ticket's History. It does not
close. Nothing an executor says about its own work changes that, which is the whole claim this repo
makes and the reason the demo is worth two minutes.

The fix is a guard and three lines. After it, the same command:

```
.......                                                                  [100%]
7 passed in 0.01s
```

Exit 0, and the `\d+ passed` receipt in the manifest matches real output rather than a run that
collected nothing.

**One honest caveat.** What the demo guarantees is the red above: the starting state genuinely fails
that gate, every time, for the reason printed. Whether the executor you point at T-002 clears it on
the first attempt or the second is up to the executor, and forcing that would mean staging a failure
— which is exactly the thing this engine exists to stop anyone doing. Run the verify yourself before
you start the driver and you have seen the gate refuse the work either way.

## Reset it

```bash
cd ~ && rm -rf demo-project && cp -R ~/agent-loop/examples/demo-project ~/demo-project
```

The copy in this repo is never modified by a run, so it stays the starting state.

## A note on the tests here

`examples/conftest.py` keeps `demo-project/tests/` out of this repo's own pytest collection. Those
tests are the demo's spec, they are red on purpose, and they only import when pytest runs from
inside the demo project. Without the ignore, `pytest` at the repo root reports the demo's unfinished
work as an engine failure.

"""Keep the demo project's spec out of this repo's own test collection.

`examples/demo-project/` ships in its starting state, so `tests/` there is red
by design and its modules only import when pytest runs from inside that project.
Collecting them from the repo root would report the demo's unfinished work as a
failure of the engine. Run the demo's tests from the demo's own directory.
"""

collect_ignore_glob = ["demo-project/tests/*"]

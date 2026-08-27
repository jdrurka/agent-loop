"""Print a summary of a timings CSV.

    python3 -m demo.cli [path]

With no argument it reads `data/timings.csv` from this project.
"""

import pathlib
import sys

from demo.report import summarise

DEFAULT_TIMINGS = pathlib.Path(__file__).resolve().parent.parent / "data" / "timings.csv"


def main(argv):
    """Print the summary for the named CSV. Returns a process exit code."""
    path = pathlib.Path(argv[1]) if len(argv) > 1 else DEFAULT_TIMINGS
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as err:
        print(f"cannot read {path}: {err}", file=sys.stderr)
        return 1
    print(summarise(text))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

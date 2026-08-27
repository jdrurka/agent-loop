"""Render a one-line summary of a timings CSV."""

from demo.parse import parse_timings
from demo.stats import mean, total


def summarise(text):
    """Return a one-line summary of the timings in `text`.

    A file with no timing rows has nothing to average, so it gets a fixed
    line instead of a summary.
    """
    values = parse_timings(text)
    if not values:
        return "no timings"
    return f"{len(values)} steps, {total(values):.1f}s total, {mean(values):.1f}s mean"

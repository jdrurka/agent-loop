"""Read build-step timings out of CSV text."""


def parse_timings(text):
    """Return the seconds column of `text` as a list of floats.

    `text` is the contents of a timings CSV: a `step,seconds` header line
    followed by one row per build step.
    """
    rows = text.splitlines()[1:]
    return [float(row.split(",")[1]) for row in rows]

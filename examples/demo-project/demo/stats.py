"""Summary statistics over a list of build-step timings."""


def total(values):
    """Return the sum of `values` as a float. The sum of nothing is 0.0."""
    return float(sum(values))


def mean(values):
    """Return the arithmetic mean of `values`.

    Raises ValueError on an empty sequence, because the average of nothing is
    not a number and returning 0.0 would quietly claim it is.
    """
    if not values:
        raise ValueError("mean of an empty sequence")
    return sum(values) / len(values)


def median(values):
    """Return the middle value of `values`."""
    return sorted(values)[len(values) // 2]

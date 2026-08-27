"""The spec for demo/stats.py. Ships complete; the implementation does not.

T-002's territory is `demo/stats.py` and nothing else, so the executor working
that ticket cannot edit this file. That is the point: the gate it is judged by
is not one it can reach.
"""

import pytest

from demo.stats import mean, median, total


def test_total_sums_the_timings():
    assert total([1.5, 2.5, 4.0]) == 8.0


def test_mean_averages_the_timings():
    assert mean([1.0, 2.0, 3.0]) == 2.0


def test_the_mean_of_nothing_is_an_error():
    with pytest.raises(ValueError):
        mean([])


def test_median_of_an_odd_count_is_the_middle_value():
    assert median([3.0, 1.0, 2.0]) == 2.0


def test_median_of_an_even_count_averages_the_middle_pair():
    assert median([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_median_of_a_single_value_is_that_value():
    assert median([7.0]) == 7.0


def test_the_median_of_nothing_is_an_error():
    with pytest.raises(ValueError):
        median([])

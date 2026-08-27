"""The spec for demo/report.py. Ships complete; the implementation does not.

The median of these four steps is 2.5 and their mean is 3.0, so the median
assertion below can only pass once T-002 has corrected `demo.stats.median`.
That is what T-003's dependency on T-002 is for.
"""

from demo.report import summarise

FOUR_STEPS = "step,seconds\ncheckout,1.0\nbuild,2.0\ntest,3.0\ndeploy,6.0\n"


def test_the_summary_counts_the_steps():
    assert "4 steps" in summarise(FOUR_STEPS)


def test_the_summary_reports_the_total():
    assert "12.0s total" in summarise(FOUR_STEPS)


def test_the_summary_reports_the_mean():
    assert "3.0s mean" in summarise(FOUR_STEPS)


def test_the_summary_reports_the_median():
    assert "2.5s median" in summarise(FOUR_STEPS)


def test_a_file_with_no_timings_has_no_summary_to_give():
    assert summarise("step,seconds\n") == "no timings"

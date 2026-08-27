"""The spec for demo/parse.py. Ships complete; the implementation does not."""

from demo.parse import parse_timings

CLEAN = "step,seconds\ncheckout,1.5\nbuild,12.0\n"


def test_reads_the_seconds_column():
    assert parse_timings(CLEAN) == [1.5, 12.0]


def test_a_header_only_file_has_no_timings():
    assert parse_timings("step,seconds\n") == []


def test_blank_lines_are_skipped():
    assert parse_timings("step,seconds\n\ncheckout,1.5\n\n") == [1.5]


def test_rows_whose_seconds_are_not_a_number_are_skipped():
    messy = "step,seconds\ncheckout,1.5\npackage,n/a\ntest,2.0\n"
    assert parse_timings(messy) == [1.5, 2.0]


def test_rows_missing_the_seconds_field_are_skipped():
    assert parse_timings("step,seconds\ncheckout\nbuild,12.0\n") == [12.0]

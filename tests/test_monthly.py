"""Monthly reports: every department builds, the index is bounded, empty months have no index."""
import pytest

import database as db
import monthly
import report


@pytest.mark.parametrize("dept", ["M", "E", "I", "REF", "OM", "PLN", "INSP"])
def test_report_builds_for_each_department(dept, today):
    rep = monthly.report(dept, today.year, today.month)
    assert rep["sections"] and "index" in rep
    v = rep["index"]["value"]
    assert v is None or 0 <= v <= 100
    assert len(report.build_monthly_xlsx(rep, monthly.index_series(dept, today.year, today.month))) > 1000


def test_empty_month_has_no_index():
    rep = monthly.report("M", 2020, 1)
    assert rep["index"]["value"] is None


def test_maintenance_counts_job_cards_as_done(today):
    jc = db.create_job("JC", today, "I", "calibration", requesting_dept="REF", actor="REF", tag_no="T-1")
    rep = monthly.report("I", today.year, today.month)
    assert jc and rep["index"]["value"] is not None

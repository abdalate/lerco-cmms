"""Daily contractor-hour capacity validation."""
from datetime import timedelta

import pytest
import database as db


@pytest.fixture(autouse=True)
def fixed_shift_hours(monkeypatch):
    monkeypatch.setattr(db, "_shift_hours_per_day", lambda: 5.0)


def make_work_order(day, tag):
    wr = db.create_wr(day, "REF", "M", f"Contractor work {tag}", tag_no=tag, actor="REF")
    return db.create_job(
        "WO", day, "M", f"Contractor work {tag}", wr_no=wr, actor="PLN",
        requesting_dept="REF", tag_no=tag,
    )


def test_contractor_hours_cannot_exceed_daily_headcount(today):
    day = today + timedelta(days=20)
    db.save_manpower(day, "M", technicians=1, contract=2)
    first = make_work_order(day, "CT-101")
    second = make_work_order(day, "CT-102")

    db.add_labor(first, day, cont_hours=10)

    with pytest.raises(ValueError, match="Contractor hours exceed daily capacity"):
        db.add_labor(second, day, cont_hours=1)


def test_manpower_cannot_be_reduced_below_recorded_contractor_hours(today):
    day = today + timedelta(days=21)
    db.save_manpower(day, "M", technicians=1, contract=2)
    job = make_work_order(day, "CT-103")
    db.add_labor(job, day, cont_hours=8)

    with pytest.raises(ValueError, match="contractor hours are already recorded"):
        db.save_manpower(day, "M", technicians=1, contract=1)

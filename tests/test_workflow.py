"""Core document cycle: WR -> WO -> work -> acceptance, and the Job Card rules."""
from datetime import datetime

import pytest

import database as db


def test_wo_requires_work_request(today):
    with pytest.raises(ValueError):
        db.create_job("WO", today, "M", "no request", actor="PLN")


def test_wr_to_wo_to_acceptance(today):
    wr = db.create_wr(today, "REF", "M", "Repair pump", tag_no="P-101", actor="REF")
    wo = db.create_job("WO", today, "M", "Repair pump", wr_no=wr, actor="PLN", requesting_dept="REF",
                       tag_no="P-101", est_hours=8.0)
    assert wo.startswith("WO-M-")
    with pytest.raises(ValueError):                      # stock check is required before execution
        db.set_job_status(wo, "O", 0, actor="M")
    db.set_stock_checked(wo, True, "M")
    db.set_job_status(wo, "O", 20, actor="M")
    db.add_labor(wo, today, 3, 8, 16, 2, 0)
    db.set_job_status(wo, "C", 100, actor="M")
    db.accept_job(wo, "REF", True)
    with pytest.raises(ValueError):                      # closed after acceptance
        db.set_job_status(wo, "O", 10, actor="M")


def test_service_request_only_from_planning(today):
    wr = db.create_wr(today, "REF", "M", "Need scaffolding", tag_no="P-102", actor="REF")
    with pytest.raises(ValueError):
        db.create_sr(today, "SCAFFOLD", "x", "REF", wr_no=wr, actor="REF", executor="A")
    assert db.create_sr(today, "SCAFFOLD", "x", "REF", wr_no=wr, actor="PLN", executor="A",
                        tag_no="P-102").startswith("SR-PLN-")


def test_job_card_closed_for_planning_then_wr(today):
    jc = db.create_job("JC", today, "M", "needs welding", requesting_dept="REF", actor="REF", tag_no="P-103")
    with pytest.raises(ValueError):                      # notes are mandatory
        db.close_job_planning(jc, "WELD", "", "M")
    db.close_job_planning(jc, "WELD", "cracked flange", "M")
    assert db.get_job(jc)["close_reason"] == "WELD"
    with pytest.raises(ValueError):                      # cannot close twice
        db.close_job_planning(jc, "WELD", "again", "M")
    wr = db.create_wr(today, "REF", "M", "Weld flange", tag_no="P-103", actor="REF",
                      cost_center="71/100", materials="flange", special_equip="crane")
    db.link_wr_to_jc(wr, jc, "REF")
    assert "WR_FROM_JC" in [e["event"] for e in db.timeline(jc)] or \
        db.query("SELECT src_jc FROM work_requests WHERE doc_no=?", (wr,))[0]["src_jc"] == jc


def test_job_card_auto_close_at_cutoff(today):
    jc = db.create_job("JC", today, "E", "motor", requesting_dept="OM", actor="OM", tag_no="M-1")
    before = datetime(today.year, today.month, today.day, 10, 0)
    db.jc_auto_close(before)
    assert db.get_job(jc)["close_reason"] is None        # not yet end of day
    db.jc_auto_close(datetime(today.year, today.month, today.day, 23, 59))
    assert db.get_job(jc)["close_reason"] == "AUTO"


def test_settings_roundtrip():
    old = db.get_setting("shift_hours")
    db.set_setting("shift_hours", 6, "PLN")
    assert db.get_setting("shift_hours") == 6
    db.set_setting("shift_hours", old, "PLN")

"""General inspection cycle: request -> study -> Planning documents -> report -> single send."""
import pytest

import database as db
import tir_form as tf


def _new_tir(today, types=("VISUAL", "SHUTDOWN")):
    wr = db.create_wr(today, "REF", "M", "Inspect vessel", tag_no="V-1", actor="REF")
    return wr, db.create_tir(today, wr, "", actor="PLN", types=list(types), reason="SHUTDOWN")


def test_stages_and_requirements(today):
    wr, t = _new_tir(today)
    assert db.get_tir(t)["stage"] == "STUDY"
    db.tir_study(t, [{"kind": "SCAFFOLD", "detail": "shell"}, {"kind": "OPEN", "detail": "manway"}], "INSP")
    assert db.get_tir(t)["stage"] == "ISSUE"             # waiting for Planning documents
    sr = db.create_sr(today, "SCAFFOLD", "Scaffold", "REF", wr_no=wr, actor="PLN", executor="A", tag_no="V-1")
    wo = db.create_job("WO", today, "M", "Open manway", wr_no=wr, actor="PLN", requesting_dept="REF", tag_no="V-1")
    db.tir_link_req(t, "R1", sr, "PLN")
    db.tir_link_req(t, "R2", wo, "PLN")
    assert db.get_tir(t)["stage"] == "PREP"
    with pytest.raises(ValueError):                      # a linked requirement is locked
        db.tir_study(t, [{"id": "R1", "kind": "OTHER", "detail": "x"}], "INSP")
    db.set_sr_status(sr, "C", actor="PLN")
    db.set_stock_checked(wo, True, "M")
    db.set_job_status(wo, "C", 100, actor="M")
    assert db.get_tir(t)["stage"] == "READY"


def test_report_and_single_send(today):
    wr, t = _new_tir(today)
    db.tir_study(t, [], "INSP")
    db.set_tir_status(t, "O", actor="INSP")
    form = {"summary": "No corrosion", "verdict": "SATISFACTORY", "action_required": "BOXUP",
            "recommendations": "Box-up the vessel", "prepared_by": "F. Alguriti", "paper_ref": "57/26"}
    errs, _ = tf.validate(form)
    assert not errs
    s = tf.summarize(form)
    db.submit_tir(t, s["findings"], s["recommendations"], actor="INSP", form_data=form)
    assert db.get_tir(t)["stage"] == "SEND"
    assert db.tir_view("SEND", "PLN") == "ACT" and db.tir_view("SEND", "INSP") == "WAIT"
    with pytest.raises(ValueError):                      # only Planning sends
        db.tir_send(t, "INSP", "", True)
    db.tir_send(t, "PLN", "see report", tf.has_recommendations(form))
    assert db.get_tir(t)["stage"] == "SENT"
    assert [x["doc_no"] for x in db.tir_sent_for_wr(wr)] == [t]
    wr2 = db.create_wr(today, "REF", "M", "Per report", tag_no="V-1", actor="REF")
    db.link_wr_to_tir(wr2, t, "REF")


def test_validation_requires_verdict_and_inspector():
    errs, _ = tf.validate({"summary": "x"})
    assert any("verdict" in e.lower() for e in errs) and any("inspector" in e.lower() for e in errs)

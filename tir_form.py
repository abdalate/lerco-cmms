# -*- coding: utf-8 -*-
"""
LERCO CMMS - Technical Inspection Report (TIR): simple one-page report
=====================================================================
The signed PAPER report remains the official document. The system records its summary
and tracks its route (Inspection -> Planning -> requesting department):

  paper report no. · inspection date · final verdict · findings summary ·
  recommendations · action required from Planning · next inspection · inspector

Stored as JSON in inspection_reports.form_json, so fields can be added later without any
database migration. No attachments and no export (decision: keep the system light).
"""
import json
from datetime import date

import streamlit as st

FORM_VERSION = 2

VERDICTS = {"SATISFACTORY": "Satisfactory — inspection activities met",
            "MONITOR": "Fit for service — monitor / re-inspect",
            "REPAIR": "Repair required before return to service",
            "UNFIT": "Unfit for service"}
ACTIONS = {"NONE": "No action required", "BOXUP": "Box-up the vessel",
           "REPAIR": "Repair per recommendations", "REINSPECT": "Re-inspect",
           "OTHER": "Other (see recommendations)"}


def load(tir):
    """Parse the stored form (dict) from an inspection_reports row; {} when none."""
    try:
        d = json.loads((tir or {}).get("form_json") or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def has_recommendations(f):
    """True when the report asks the requester / Planning for follow-up work."""
    f = f or {}
    return bool((f.get("recommendations") or "").strip()
                or (f.get("action_required") or "NONE") not in ("NONE", ""))


def validate(form):
    """(errors, warnings). Errors block submission."""
    f, errs, warns = form or {}, [], []
    if not (f.get("summary") or "").strip():
        errs.append("Findings summary is required")
    if not f.get("verdict"):
        errs.append("Select the final verdict")
    if f.get("verdict") in ("REPAIR", "UNFIT") and (f.get("action_required") or "NONE") == "NONE":
        errs.append("The verdict requires an action from Planning")
    if (f.get("action_required") or "NONE") != "NONE" and not (f.get("recommendations") or "").strip():
        warns.append("An action is requested but no recommendations are written")
    if not (f.get("prepared_by") or "").strip():
        errs.append("Inspector name is required")
    return errs, warns


def summarize(form):
    """Text for the classic TIR columns (findings / recommendations)."""
    f = form or {}
    fl = [(f.get("summary") or "").strip()]
    if f.get("verdict"):
        fl.append("Verdict: " + VERDICTS.get(f["verdict"], f["verdict"]))
    rc = []
    if (f.get("action_required") or "NONE") != "NONE":
        rc.append("Action: " + ACTIONS.get(f["action_required"], f["action_required"]))
    if (f.get("recommendations") or "").strip():
        rc.append(f["recommendations"].strip())
    return {"findings": "\n".join(x for x in fl if x) or "See inspection report.",
            "recommendations": "\n".join(rc)}


def _s(v):
    return "" if v is None else str(v)


def _kv(pairs):
    return "  \n".join(f"**{k}:** {_s(v) or '—'}" for k, v in pairs)


def _day(v):
    try:
        return date.fromisoformat(v) if v else None
    except (TypeError, ValueError):
        return None


def edit_form(tir, saved):
    """Render the one-page form (call inside a dialog) and return the form dict."""
    saved, p = saved or {}, f"tf_{tir['doc_no']}_"
    out = {}
    a, b, c = st.columns(3)
    a.text_input("Report No.", value=tir["doc_no"], disabled=True)
    b.text_input("Equipment", value=tir.get("equip") or "", disabled=True)
    c.text_input("Work Request", value=tir.get("wr_no") or "", disabled=True)
    st.caption("Inspection types requested: " + (tir.get("types_ar") or "—"))
    a, b = st.columns(2)
    out["paper_ref"] = a.text_input("Paper report reference (e.g. 57/26)",
                                    value=saved.get("paper_ref") or "", key=p + "paper_ref")
    out["inspection_date"] = b.date_input("Inspection date", value=_day(saved.get("inspection_date")) or date.today(),
                                          key=p + "inspection_date").isoformat()
    verdicts = [""] + list(VERDICTS)
    out["verdict"] = st.selectbox("Final verdict *", verdicts,
                                  index=verdicts.index(saved["verdict"]) if saved.get("verdict") in VERDICTS else 0,
                                  format_func=lambda v: "—" if not v else VERDICTS[v], key=p + "verdict")
    out["summary"] = st.text_area("Findings summary * (what was inspected and found)", height=110,
                                  value=saved.get("summary") or "", key=p + "summary")
    out["recommendations"] = st.text_area("Recommendations", height=90,
                                          value=saved.get("recommendations") or "", key=p + "recommendations")
    a, b = st.columns(2)
    acts = list(ACTIONS)
    out["action_required"] = a.selectbox("Action required from Planning", acts,
                                         index=acts.index(saved["action_required"])
                                         if saved.get("action_required") in acts else 0,
                                         format_func=ACTIONS.get, key=p + "action_required")
    nxt = b.date_input("Next inspection date (optional)", value=_day(saved.get("next_inspection")),
                       key=p + "next_inspection")
    out["next_inspection"] = nxt.isoformat() if nxt else None
    a, b = st.columns(2)
    out["prepared_by"] = a.text_input("Inspector name *", value=saved.get("prepared_by") or "",
                                      key=p + "prepared_by")
    out["badge_no"] = b.text_input("Badge number", value=saved.get("badge_no") or "", key=p + "badge_no")
    out["version"] = FORM_VERSION
    return out


def requirements_card(f):
    """Verdict, action and recommendations (requester view and Work Order screens)."""
    if f.get("verdict"):
        st.markdown(f"**Verdict:** {VERDICTS.get(f['verdict'], f['verdict'])}  \n"
                    f"**Action required:** {ACTIONS.get(f.get('action_required') or 'NONE', '—')}")
    if (f.get("recommendations") or "").strip():
        st.info(f["recommendations"].strip())


def view_ui(f):
    """Read-only view of a saved report."""
    st.markdown(_kv([("Paper report ref.", f.get("paper_ref")), ("Inspection date", f.get("inspection_date")),
                     ("Verdict", VERDICTS.get(f.get("verdict"), "")),
                     ("Findings", f.get("summary") or f.get("visual_external") or f.get("sd_notes")),
                     ("Recommendations", f.get("recommendations")),
                     ("Action required", ACTIONS.get(f.get("action_required") or "NONE", "")),
                     ("Next inspection", f.get("next_inspection")),
                     ("Inspector", f"{f.get('prepared_by') or ''} (badge {f.get('badge_no') or '—'})")]))

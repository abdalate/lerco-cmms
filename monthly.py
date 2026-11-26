# -*- coding: utf-8 -*-
"""
LERCO CMMS - Monthly department reports + internal improvement index
====================================================================
Every figure is derived from data the system already records (no new data entry).
The improvement index (0-100) is an INTERNAL tool: each department compares itself with its
own previous months. It is not used to rank departments.

report(dept, year, month) -> {
    "dept", "year", "month", "label",
    "sections": [{"title", "metrics": [(label, value)], "table": {"cols", "rows"} | None, "note"}],
    "index": {"value", "components": [{"key","label","raw","score","weight"}], "weak": [...], "missing": [...]},
    "notes": [data-completeness warnings]}

Weights are constants below (documented in the report footer); targets come from the
settings table (database.SETTINGS_DEFAULT).
"""
import calendar
import json
from datetime import date, datetime, timedelta

import database as db

# index weights (% of the index) per department kind
WEIGHTS = {
    "maint": {"completion": 30, "timeliness": 20, "utilization": 15, "quality": 15,
              "estimate": 10, "planned": 10},
    "prod": {"acceptance": 50, "plan": 50},
    "plan": {"response": 30, "plan": 30, "backlog": 25, "dispatch": 15},
    "insp": {"study": 35, "cycle": 35, "overdue": 30},
}
BANDS = ((85, "Excellent"), (70, "Good"), (50, "Needs improvement"), (0, "Focus area"))


def band(v):
    return None if v is None else next(lab for lim, lab in BANDS if v >= lim)


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _bounds(y, m):
    s = date(y, m, 1)
    e = date(y, m, calendar.monthrange(y, m)[1])
    return s.isoformat(), e.isoformat(), e


def _d(x):
    try:
        return date.fromisoformat((x or "")[:10])
    except ValueError:
        return None


def _days(a, b):
    a, b = _d(a), _d(b)
    return None if not a or not b else (b - a).days


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _pct(a, b):
    return None if not b else round(100.0 * a / b, 1)


def _hours(a, b):
    try:
        return (datetime.strptime(b, "%Y-%m-%d %H:%M") - datetime.strptime(a, "%Y-%m-%d %H:%M")
                ).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def _cap(x):
    return None if x is None else round(max(0.0, min(100.0, x)), 1)


def _inv_days(avg, target):
    """100 when average days <= target, falling proportionally above it."""
    if avg is None:
        return None
    return 100.0 if avg <= target else _cap(100.0 * target / avg)


def _comp(kind, key, label, raw, score):
    return {"key": key, "label": label, "raw": raw, "score": _cap(score),
            "weight": WEIGHTS[kind][key]}


def _index(components):
    avail = [c for c in components if c["score"] is not None]
    wsum = sum(c["weight"] for c in avail)
    value = round(sum(c["weight"] * c["score"] for c in avail) / wsum, 1) if wsum else None
    weak = sorted([c for c in avail if c["score"] < 100], key=lambda c: c["score"])[:2]
    return {"value": value, "components": components, "weak": [c["label"] for c in weak],
            "missing": [c["label"] for c in components if c["score"] is None],
            "band": band(value)}


def _sec(title, metrics=None, cols=None, rows=None, note=None):
    return {"title": title, "metrics": metrics or [], "note": note,
            "table": {"cols": cols, "rows": rows} if cols and rows else None}


def _in(col):
    return f"{col} BETWEEN ? AND ?"


# ----------------------------------------------------------------------------
# maintenance (M / E / I)
# ----------------------------------------------------------------------------
def _maint(D, y, m):
    s, e, end = _bounds(y, m)
    asof = min(end, date.today()).isoformat()
    age_lim = db.get_setting("backlog_age_days")
    shift = db.get_setting("shift_hours")
    util_t, plan_t = db.get_setting("util_target"), db.get_setting("planned_target")
    notes = []

    rec = {r["doc_type"]: r["n"] for r in db.query(
        "SELECT doc_type, COUNT(*) n FROM jobs WHERE exec_dept=? AND " + _in("doc_date")
        + " GROUP BY doc_type", (D, s, e))}
    carried = db.query("SELECT COUNT(*) n FROM jobs WHERE exec_dept=? AND doc_date<?"
                       " AND (status!='C' OR end_date>=?)", (D, s, s))[0]["n"]
    done = {r["doc_type"]: r["n"] for r in db.query(
        "SELECT doc_type, COUNT(*) n FROM jobs WHERE exec_dept=? AND status='C' AND "
        + _in("end_date") + " GROUP BY doc_type", (D, s, e))}
    n_rec, n_done = sum(rec.values()), sum(done.values())
    rate = _pct(n_done, n_rec + carried)
    wo_done = db.query("SELECT doc_date, end_date FROM jobs WHERE exec_dept=? AND doc_type='WO'"
                       " AND status='C' AND " + _in("end_date"), (D, s, e))
    avg_days = _avg([_days(r["doc_date"], r["end_date"]) for r in wo_done])

    # backlog (Work Orders only; JC never stays open)
    bl = db.query("SELECT doc_date, est_hours FROM jobs WHERE exec_dept=? AND doc_type='WO'"
                  " AND doc_date<=? AND (status!='C' OR end_date>?)", (D, asof, asof))
    aged = sum(1 for r in bl if (_days(r["doc_date"], asof) or 0) > age_lim)
    bl_hours = sum(r["est_hours"] or 0 for r in bl)

    # hours
    hrs = db.query(
        "SELECT j.doc_type, j.category, j.cat_set, SUM(l.spv_hours) spv, SUM(l.tech_hours) tech,"
        " SUM(l.ot_hours) ot, SUM(l.cont_hours) cont FROM labor_log l JOIN jobs j ON j.doc_no=l.job_no"
        " WHERE j.exec_dept=? AND l.work_date BETWEEN ? AND ? GROUP BY j.doc_type, j.category, j.cat_set",
        (D, s, e))
    tech = sum(r["tech"] or 0 for r in hrs)
    spv = sum(r["spv"] or 0 for r in hrs)
    reg = tech  # استغلال الطاقة الإنتاجية يُحسب للفنيين فقط
    ot = sum(r["ot"] or 0 for r in hrs)
    cont = sum(r["cont"] or 0 for r in hrs)
    wo_h = [r for r in hrs if r["doc_type"] == "WO" and r["cat_set"]]
    wo_total = sum((r["spv"] or 0) + (r["tech"] or 0) + (r["ot"] or 0) + (r["cont"] or 0) for r in wo_h)
    pl_h = sum((r["spv"] or 0) + (r["tech"] or 0) + (r["ot"] or 0) + (r["cont"] or 0)
               for r in wo_h if r["category"] in ("PM", "PD"))
    planned = _pct(pl_h, wo_total)
    mp = db.query("SELECT COUNT(*) days, SUM(technicians) heads FROM manpower_daily"
                  " WHERE dept=? AND report_date BETWEEN ? AND ? AND technicians>0", (D, s, e))[0]
    avail = (mp["heads"] or 0) * shift
    util = _pct(tech, avail)
    lab_days = db.query("SELECT COUNT(DISTINCT l.work_date) n FROM labor_log l JOIN jobs j ON j.doc_no=l.job_no"
                        " WHERE j.exec_dept=? AND l.work_date BETWEEN ? AND ?", (D, s, e))[0]["n"]
    if lab_days and mp["days"] < lab_days:
        notes.append(f"Technician attendance was entered for {mp['days']} of {lab_days} working days — "
                     "technician utilization may be understated" if mp["days"] else
                     "Technician attendance was not entered — utilization cannot be calculated")
    # estimate accuracy
    est = db.query(
        "SELECT j.est_hours est, COALESCE((SELECT SUM(spv_hours+tech_hours+ot_hours+cont_hours)"
        " FROM labor_log WHERE job_no=j.doc_no),0) act FROM jobs j WHERE j.exec_dept=? AND j.doc_type='WO'"
        " AND j.status='C' AND j.end_date BETWEEN ? AND ? AND j.est_hours>0", (D, s, e))
    est_t, act_t = sum(r["est"] for r in est), sum(r["act"] for r in est)
    acc = None
    if est and act_t > 0:
        acc = round(100 - abs(act_t / est_t - 1) * 100, 1)
    elif est:
        notes.append("Completed Work Orders have estimates but no recorded hours — estimate accuracy not calculated")
    # rework + repeats + JC closures + spares
    rw = db.query("SELECT COUNT(*) n FROM events e JOIN jobs j ON j.doc_no=e.doc_no WHERE e.event='JOB_RETURNED'"
                  " AND j.exec_dept=? AND substr(e.ts,1,10) BETWEEN ? AND ?", (D, s, e))[0]["n"]
    rw_rate = _pct(rw, n_done)
    rep = db.query(
        "SELECT COALESCE(NULLIF(tag_no,''), equipment_name) eq, COUNT(*) n FROM jobs WHERE exec_dept=? AND "
        + _in("doc_date") + " AND (doc_type='JC' OR category='CM') GROUP BY eq HAVING n>=3"
        " ORDER BY n DESC LIMIT 10", (D, s, e))
    jc_close = db.query("SELECT close_reason, COUNT(*) n FROM jobs WHERE exec_dept=? AND doc_type='JC' AND "
                        + _in("doc_date") + " AND close_reason IS NOT NULL GROUP BY close_reason", (D, s, e))
    sp = db.query("SELECT SUM(sp.qty) q, SUM(sp.returned) r FROM spare_parts sp JOIN jobs j ON j.doc_no=sp.job_no"
                  " WHERE j.exec_dept=? AND j.status='C' AND j.end_date BETWEEN ? AND ?", (D, s, e))[0]

    sections = [
        _sec("Work handled", [
            ("Received — Work Orders / Job Cards", f"{rec.get('WO', 0)} / {rec.get('JC', 0)}"),
            ("Carried in from previous months", carried),
            ("Completed — Work Orders / Job Cards", f"{done.get('WO', 0)} / {done.get('JC', 0)}"),
            ("Completion rate (%)", rate if rate is not None else "—"),
            ("Average days to complete a Work Order", avg_days if avg_days is not None else "—")],
            note="All Job Cards count as completed work (a Job Card lives one day)."),
        _sec("Work Order backlog at end of month", [
            ("Open Work Orders", len(bl)), ("Estimated hours", round(bl_hours, 1)),
            (f"Older than {age_lim:g} days", aged)]),
        _sec("Manpower & hours", [
            ("Regular technician hours (TECH)", round(tech, 1)),
            ("Supervisor hours (SPV)", round(spv, 1)), ("Overtime hours", round(ot, 1)),
            ("Contractor hours", round(cont, 1)),
            ("Available technician hours", round(avail, 1)),
            ("Utilization (%)", util if util is not None else "—"),
            ("Overtime share (%)", _pct(ot, reg + ot) if reg + ot else "—")]),
        _sec("Quality & planning", [
            ("Planned work — PM+PD share of Work Order hours (%)", planned if planned is not None else "—"),
            ("Estimate accuracy (%)", acc if acc is not None else "—"),
            ("Jobs returned by requester (rework)", rw),
            ("Spare parts issued / returned", f"{sp['q'] or 0:g} / {sp['r'] or 0:g}")]),
    ]
    if rep:
        sections.append(_sec("Repeated failures (3 or more corrective jobs in the month)",
                             cols=["Equipment", "Jobs"], rows=[[r["eq"], r["n"]] for r in rep]))
    if jc_close:
        sections.append(_sec("Job Cards closed without completion", cols=["Reason", "Job Cards"],
                             rows=[[db.JC_CLOSE_REASONS.get(r["close_reason"], r["close_reason"]), r["n"]]
                                   for r in jc_close]))
    comps = [
        _comp("maint", "completion", "Completion", rate, rate),
        _comp("maint", "timeliness", "Timeliness (open Work Orders within age limit)",
              f"{aged} overdue of {len(bl)}", 100.0 if not bl else 100 - 100.0 * aged / len(bl)),
        _comp("maint", "utilization", "Manpower utilization", util,
              None if util is None else 100.0 * util / util_t),
        _comp("maint", "quality", "Quality (no rework)", rw_rate,
              None if not n_done else 100 - (rw_rate or 0)),
        _comp("maint", "estimate", "Estimate accuracy", acc, acc),
        _comp("maint", "planned", "Planned work share", planned,
              None if planned is None else 100.0 * planned / plan_t),
    ]
    return sections, comps, notes


# ----------------------------------------------------------------------------
# production (REF / OM)
# ----------------------------------------------------------------------------
def _prod(P, y, m):
    s, e, end = _bounds(y, m)
    asof = min(end, date.today()).isoformat()
    acc_t = db.get_setting("accept_target_days")
    wrs = db.query("SELECT * FROM work_requests WHERE requesting_dept=? AND " + _in("doc_date"), (P, s, e))
    jcs = db.query("SELECT doc_no FROM jobs WHERE doc_type='JC' AND requesting_dept=? AND " + _in("doc_date"),
                   (P, s, e))
    prio = {}
    for w in wrs:
        prio[w["priority"] or "Normal"] = prio.get(w["priority"] or "Normal", 0) + 1
    conv = sum(1 for w in wrs if w["status"] in ("CONVERTED", "SERVICE"))
    rej = [w for w in wrs if w["status"] == "REJECTED"]
    pend = sum(1 for w in wrs if w["status"] in ("NEW", "STUDY"))
    resp = _avg([_hours(w["submitted_at"], w["reviewed_at"]) for w in wrs if w["reviewed_at"]])
    wo_cycle = _avg([_days(r["doc_date"], r["end_date"]) for r in db.query(
        "SELECT doc_date, end_date FROM jobs WHERE doc_type='WO' AND requesting_dept=? AND status='C' AND "
        + _in("end_date"), (P, s, e))])
    acc = _avg([_days(r["end_date"], r["accepted_date"]) for r in db.query(
        "SELECT end_date, accepted_date FROM jobs WHERE requesting_dept=? AND status='C' AND close_reason IS NULL"
        " AND accepted_date IS NOT NULL AND " + _in("accepted_date"), (P, s, e))])
    waiting = db.query("SELECT end_date FROM jobs WHERE requesting_dept=? AND status='C' AND accepted_date IS NULL"
                       " AND end_date<=?", (P, asof))
    oldest = max([_days(r["end_date"], asof) or 0 for r in waiting], default=0)
    items = [i for i in db.plan_due_items(P, on_date=end) if i["year"] == y and i["month"] == m]
    sent = sum(1 for i in items if i["state"] == "SENT")
    plan_done = sum(1 for i in items if i["status"] == "C")
    top = {}
    for r in db.query("SELECT tag_no, equipment_name, location FROM work_requests WHERE requesting_dept=? AND "
                      + _in("doc_date") + " UNION ALL SELECT tag_no, equipment_name, location FROM jobs"
                      " WHERE doc_type='JC' AND requesting_dept=? AND " + _in("doc_date"), (P, s, e, P, s, e)):
        k = db.equipment_label(r["tag_no"], r["equipment_name"], r["location"])
        top[k] = top.get(k, 0) + 1
    top = sorted(top.items(), key=lambda kv: -kv[1])[:5]
    sections = [
        _sec("Requests", [("Work Requests", len(wrs)), ("Job Cards", len(jcs)),
                          ("Urgent / Shutdown / Emergency", sum(n for k, n in prio.items() if k != "Normal")),
                          ("Converted to Work Order / Service", conv), ("Rejected", len(rej)),
                          ("Still pending with Planning", pend)]),
        _sec("Cycle times", [("Planning response (hours)", resp if resp is not None else "—"),
                             ("Work Order issue → completion (days)", wo_cycle if wo_cycle is not None else "—"),
                             ("Completion → my acceptance (days)", acc if acc is not None else "—"),
                             ("Completed work awaiting my acceptance now", len(waiting)),
                             ("Oldest waiting (days)", oldest)]),
        _sec("Annual plan items due this month", [("Due", len(items)), ("Work Request sent", sent),
                                                  ("Completed", plan_done)]),
    ]
    if rej:
        sections.append(_sec("Rejected requests", cols=["Request", "Reason"],
                             rows=[[w["doc_no"], w["reject_reason"] or "—"] for w in rej]))
    if top:
        sections.append(_sec("Most requested equipment", cols=["Equipment", "Requests"],
                             rows=[[k, n] for k, n in top]))
    comps = [
        _comp("prod", "acceptance", "Acceptance speed", acc, _inv_days(acc, acc_t)),
        _comp("prod", "plan", "Annual plan requests sent", f"{sent} of {len(items)}",
              _pct(sent, len(items))),
    ]
    return sections, comps, []


# ----------------------------------------------------------------------------
# planning & programming
# ----------------------------------------------------------------------------
def _plan(y, m):
    s, e, end = _bounds(y, m)
    asof = min(end, date.today()).isoformat()
    age_lim, resp_t = db.get_setting("backlog_age_days"), db.get_setting("response_target_h")
    disp_t = db.get_setting("dispatch_target_days")
    wrs = db.query("SELECT * FROM work_requests WHERE " + _in("doc_date"), (s, e))
    hours = [_hours(w["submitted_at"], w["reviewed_at"]) for w in wrs if w["reviewed_at"]]
    hours = [h for h in hours if h is not None]
    in_t = sum(1 for h in hours if h <= resp_t)
    conv = sum(1 for w in wrs if w["status"] in ("CONVERTED", "SERVICE"))
    rej = sum(1 for w in wrs if w["status"] == "REJECTED")
    pend = sum(1 for w in wrs if w["status"] in ("NEW", "STUDY"))
    wo_out = db.query("SELECT exec_dept, COUNT(*) n FROM jobs WHERE doc_type='WO' AND " + _in("doc_date")
                      + " GROUP BY exec_dept", (s, e))
    n_sr = db.query("SELECT COUNT(*) n FROM service_requests WHERE " + _in("doc_date"), (s, e))[0]["n"]
    n_tir = db.query("SELECT COUNT(*) n FROM inspection_reports WHERE " + _in("doc_date"), (s, e))[0]["n"]
    bl = db.query("SELECT doc_date, est_hours FROM jobs WHERE doc_type='WO' AND doc_date<=?"
                  " AND (status!='C' OR end_date>?)", (asof, asof))
    aged = sum(1 for r in bl if (_days(r["doc_date"], asof) or 0) > age_lim)
    wo_h = db.query(
        "SELECT j.category, SUM(l.spv_hours+l.tech_hours+l.ot_hours+l.cont_hours) h FROM labor_log l"
        " JOIN jobs j ON j.doc_no=l.job_no WHERE j.doc_type='WO' AND j.cat_set=1 AND l.work_date BETWEEN ? AND ?"
        " GROUP BY j.category", (s, e))
    tot_h = sum(r["h"] or 0 for r in wo_h)
    planned = _pct(sum(r["h"] or 0 for r in wo_h if r["category"] in ("PM", "PD")), tot_h)
    jc = db.query(
        "SELECT j.exec_dept d, COUNT(*) n, SUM(j.close_reason IS NOT NULL AND j.close_reason!='AUTO') plan_n,"
        " SUM(j.close_reason='AUTO') auto_n, COALESCE(SUM((SELECT SUM(spv_hours+tech_hours+ot_hours+cont_hours)"
        " FROM labor_log WHERE job_no=j.doc_no)),0) h FROM jobs j WHERE j.doc_type='JC' AND "
        + _in("j.doc_date") + " GROUP BY j.exec_dept", (s, e))
    plan_rows, due_ytd, done_ytd = [], 0, 0
    for P in db.dept_codes("PRODUCTION"):
        its = [i for i in db.plan_due_items(P, on_date=end) if i["year"] == y]
        cur = [i for i in its if i["month"] == m]
        ytd_done = sum(1 for i in its if i["status"] == "C")
        due_ytd += len(its)
        done_ytd += ytd_done
        plan_rows.append([db.dept_en(P), len(cur), sum(1 for i in cur if i["state"] == "SENT"),
                          sum(1 for i in cur if i["status"] == "C"), len(its), ytd_done,
                          sum(1 for i in its if i["unexec"])])
    srs = db.query(
        "SELECT COALESCE(executor,'—') ex, SUM(doc_date BETWEEN ? AND ?) n, SUM(status!='C' AND doc_date<=?) open_n,"
        " COALESCE(SUM((SELECT SUM(hours) FROM sr_labor_log WHERE sr_no=s.doc_no AND work_date BETWEEN ? AND ?)),0) h"
        " FROM service_requests s GROUP BY ex HAVING n>0 OR open_n>0", (s, e, asof, s, e))
    sent = db.query("SELECT submitted_at, sent_at FROM inspection_reports WHERE sent_at IS NOT NULL AND "
                    "substr(sent_at,1,10) BETWEEN ? AND ?", (s, e))
    disp = _avg([_days(r["submitted_at"], r["sent_at"]) for r in sent])
    waiting = db.query("SELECT COUNT(*) n FROM inspection_reports WHERE status='C' AND sent_at IS NULL")[0]["n"]
    sections = [
        _sec("Incoming Work Requests", [
            ("Received", len(wrs)), ("Converted to Work Order / Service", conv),
            ("Rejected", rej), ("Rejection rate (%)", _pct(rej, len(wrs)) if wrs else "—"),
            ("Pending", pend), ("Average response (hours)", _avg(hours) if hours else "—"),
            (f"Answered within {resp_t:g} h (%)", _pct(in_t, len(hours)) if hours else "—")]),
        _sec("Issued by Planning", [
            ("Work Orders", sum(r["n"] for r in wo_out)), ("Service Requests", n_sr),
            ("Inspection Requests", n_tir)] + [(f"Work Orders → {db.dept_en(r['exec_dept'])}", r["n"])
                                                for r in wo_out]),
        _sec("Work Order backlog at end of month", [
            ("Open Work Orders", len(bl)), ("Estimated hours", round(sum(r["est_hours"] or 0 for r in bl), 1)),
            (f"Older than {age_lim:g} days", aged),
            ("Planned work — PM+PD share of Work Order hours (%)", planned if planned is not None else "—")]),
    ]
    if jc:
        sections.append(_sec(
            "Job Cards (direct from Production — did not pass through Planning)",
            cols=["Executing dept", "Job Cards", "Hours", "Closed — needs Planning", "Closed automatically"],
            rows=[[db.dept_en(r["d"]), r["n"], round(r["h"] or 0, 1), r["plan_n"] or 0, r["auto_n"] or 0]
                  for r in jc],
            note="Job Cards are counted as completed work and do not affect response-time or backlog figures."))
    sections.append(_sec("Annual plan", cols=["Production dept", "Due this month", "Request sent",
                                              "Completed", "Due year-to-date", "Completed YTD", "Overdue"],
                         rows=plan_rows))
    if srs:
        sections.append(_sec("External services (Service Requests)",
                             cols=["Executing party", "Issued", "Open at end", "Hours"],
                             rows=[[r["ex"], r["n"] or 0, r["open_n"] or 0, round(r["h"] or 0, 1)] for r in srs]))
    sections.append(_sec("Inspection reports", [
        ("Sent to requesters this month", len(sent)),
        ("Average days from report to sending", disp if disp is not None else "—"),
        ("Reports waiting to be sent now", waiting)]))
    comps = [
        _comp("plan", "response", f"Response within {resp_t:g} h", f"{in_t} of {len(hours)}",
              _pct(in_t, len(hours))),
        _comp("plan", "plan", "Annual plan completion (year-to-date)", f"{done_ytd} of {due_ytd}",
              _pct(done_ytd, due_ytd)),
        _comp("plan", "backlog", "Backlog aging", f"{aged} overdue of {len(bl)}",
              100.0 if not bl else 100 - 100.0 * aged / len(bl)),
        _comp("plan", "dispatch", "Inspection report dispatch speed", disp, _inv_days(disp, disp_t)),
    ]
    return sections, comps, []


# ----------------------------------------------------------------------------
# technical inspection
# ----------------------------------------------------------------------------
def _insp(y, m):
    s, e, end = _bounds(y, m)
    asof = min(end, date.today())
    study_t, cyc_t = db.get_setting("study_target_days"), db.get_setting("insp_target_days")
    rows = db.query("SELECT * FROM inspection_reports")
    rcv = [r for r in rows if s <= r["doc_date"] <= e]
    sub = [r for r in rows if r["submitted_at"] and s <= r["submitted_at"][:10] <= e]
    open_end = [r for r in rows if r["doc_date"] <= e and not (r["submitted_at"] and r["submitted_at"][:10] <= e)]
    study = _avg([_days(r["doc_date"], r["studied_at"]) for r in rows
                  if r["studied_at"] and s <= r["studied_at"][:10] <= e])
    cycle = _avg([_days(r["doc_date"], r["submitted_at"]) for r in sub])
    starts = db.query("SELECT e.doc_no, MIN(e.ts) ts FROM events e WHERE e.event='TIR_STATUS' AND e.note=?"
                      " GROUP BY e.doc_no", (db.TIR_STATUS["O"],))
    studied = {r["doc_no"]: r["studied_at"] for r in rows if r["studied_at"]}
    prep = _avg([_days(studied[x["doc_no"]], x["ts"]) for x in starts
                 if x["doc_no"] in studied and s <= x["ts"][:10] <= e])
    verd, kinds = {}, {}
    for r in sub:
        try:
            v = json.loads(r["form_json"] or "{}").get("verdict")
        except ValueError:
            v = None
        verd[v or "—"] = verd.get(v or "—", 0) + 1
    for r in rcv:
        for t in (r["insp_types"] or r["scope"] or "").split(","):
            if t:
                kinds[t] = kinds.get(t, 0) + 1
    # latest submitted report per equipment -> next inspection
    latest = {}
    for r in sorted((x for x in rows if x["status"] == "C"), key=lambda x: x["submitted_at"] or ""):
        latest[db.equipment_label(r["tag_no"], r["equipment_name"], r["location"])] = r
    nxt = []
    for k, r in latest.items():
        try:
            nd = _d(json.loads(r["form_json"] or "{}").get("next_inspection"))
        except ValueError:
            nd = None
        if nd:
            nxt.append((k, nd))
    overdue = [k for k, nd in nxt if nd < asof]
    soon = {n: sum(1 for _, nd in nxt if asof <= nd <= asof + timedelta(days=n)) for n in (30, 60, 90)}
    open_reqs = sum(1 for r in rows if r["status"] != "C"
                    for x in db._parse_reqs(r) if not x.get("doc_no"))
    sections = [
        _sec("Requests", [("Received", len(rcv)), ("Reports submitted", len(sub)),
                          ("Open at end of month", len(open_end))]),
        _sec("Cycle times", [("Issue → study (days)", study if study is not None else "—"),
                             ("Study → inspection start (days)", prep if prep is not None else "—"),
                             ("Issue → report submitted (days)", cycle if cycle is not None else "—"),
                             ("Preparations still waiting for a Planning document", open_reqs)]),
        _sec("Upcoming inspections (from submitted reports)", [
            ("Overdue", len(overdue)), ("Due within 30 days", soon[30]),
            ("Within 60 days", soon[60]), ("Within 90 days", soon[90])]),
    ]
    if verd:
        sections.append(_sec("Verdicts of reports submitted", cols=["Verdict", "Reports"],
                             rows=[[tf_label(k), n] for k, n in verd.items()]))
    if kinds:
        sections.append(_sec("Requested inspection types", cols=["Type", "Requests"],
                             rows=[[db.INSP_TYPES.get(k, k), n] for k, n in kinds.items()]))
    comps = [
        _comp("insp", "study", "Study speed", study, _inv_days(study, study_t)),
        _comp("insp", "cycle", "Report cycle", cycle, _inv_days(cycle, cyc_t)),
        _comp("insp", "overdue", "Inspections on time", f"{len(overdue)} overdue of {len(nxt)}",
              None if not nxt else 100 - 100.0 * len(overdue) / len(nxt)),
    ]
    return sections, comps, []


def tf_label(k):
    import tir_form
    return tir_form.VERDICTS.get(k, k)


# ----------------------------------------------------------------------------
def kind_of(dept):
    return ("prod" if dept in db.dept_codes("PRODUCTION") else
            "maint" if dept in db.dept_codes("MAINTENANCE") else
            "plan" if dept == "PLN" else "insp")


def report(dept, year, month):
    kind = kind_of(dept)
    if kind == "maint":
        sections, comps, notes = _maint(dept, year, month)
    elif kind == "prod":
        sections, comps, notes = _prod(dept, year, month)
    elif kind == "plan":
        sections, comps, notes = _plan(year, month)
    else:
        sections, comps, notes = _insp(year, month)
    idx = _index(comps)
    # a month with no activity must not score 100: the index needs at least one "activity" component
    anchors = {"maint": {"completion"}, "prod": {"acceptance", "plan"}, "plan": {"response", "plan", "dispatch"},
               "insp": {"study", "cycle"}}[kind]
    if not any(c["score"] is not None and c["key"] in anchors for c in comps):
        idx = dict(idx, value=None, band=None)
        notes = list(notes) + ["No activity recorded for this month — the improvement index is not calculated."]
    return {"dept": dept, "kind": kind, "year": year, "month": month,
            "label": f"{calendar.month_name[month]} {year}", "sections": sections,
            "index": idx, "notes": notes}


def index_series(dept, year, month, n=6):
    """Index of the last n months ending at (year, month), oldest first."""
    out, y, m = [], year, month
    for _ in range(n):
        out.append((f"{calendar.month_abbr[m]} {y}", report(dept, y, m)["index"]["value"]))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]

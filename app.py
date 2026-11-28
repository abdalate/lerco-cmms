# -*- coding: utf-8 -*-
"""
LERCO CMMS - Maintenance Work Management System (Planning & Programming)
Run: streamlit run app.py --server.address 0.0.0.0 --server.port 8501
"""
from datetime import date

import pandas as pd
import streamlit as st

import database as db
import report
import monthly
import tir_form

st.set_page_config(page_title="LERCO CMMS", page_icon="🛠️", layout="wide",
                   initial_sidebar_state="collapsed")

st.markdown("""
<style>
html, body, [data-testid="stAppViewContainer"], .stMarkdown, label,
.stTextInput, .stTextArea, .stSelectbox, [data-testid="stDialog"] {
    direction: ltr; text-align: left;
    font-family: Tahoma, "Segoe UI", Arial, sans-serif;
}
/* No sidebar: tables use the full screen width. */
[data-testid="stSidebar"], [data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"], header[data-testid="stHeader"] { display: none !important; }
.block-container { padding: .5rem 1.2rem .3rem 1.2rem !important; max-width: 100% !important; }
div[data-testid="stVerticalBlock"] { gap: .5rem; }
h1, h2, h3, h4 { padding: .1rem 0 !important; margin: 0 !important; }
[data-testid="stMetricValue"] { font-size: 1.35rem; }
[data-testid="stDataFrame"], [data-testid="stMetric"] { direction: ltr; }
.stButton button, .stDownloadButton button { font-weight: 600; }
code { direction: ltr; unicode-bidi: isolate; }
.stepper { display:flex; gap:6px; margin:4px 0 8px 0; }
.stp { flex:1; text-align:center; padding:4px 2px; border-radius:8px; font-size:.75rem; background:rgba(128,128,128,.15); }
.stp b { display:block; font-size:.95rem; }
.stp.done { background:rgba(40,167,69,.30); }
.stp.cur { background:#0d6efd; color:#fff; }
/* Segmented controls: consistent button radius across wrapped rows. */
div[data-testid="stButtonGroup"] { gap: 4px !important; }
div[data-testid="stButtonGroup"] button,
button[data-testid^="stBaseButton-segmented_control"] {
    border-radius: .5rem !important; margin: 0 !important; }
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def _init():
    db.init_db()
    return True


_init()
db.jc_auto_close()      # safety net: closes Job Cards left open at the end of their day
db.auto_close_unaccepted_jobs()  # apply the requester acceptance deadline


@st.cache_data(show_spinner=False, max_entries=64)
def _cached(fn_name, args, ver, day):
    return getattr(db, fn_name)(*args)


def cdb(fn_name, *args):
    """Cached read of a heavy db function. The cache key includes db.data_version(), which changes on
    ANY write (from any user), so results are never stale; unchanged data is served from memory."""
    return _cached(fn_name, args, db.data_version(), date.today().isoformat())


ROLES = ["REF", "OM", "PLN", "M", "E", "I", "INSP"]
PROD = db.dept_codes("PRODUCTION")
MAINT = db.dept_codes("MAINTENANCE")


def ltr(x):
    """Return a value without RTL isolation; the interface is left-to-right."""
    return str(x)


def _show_blank(df):
    """Replace empty values (None) with an em dash in displayed tables."""
    for c in df.columns:
        if not pd.api.types.is_numeric_dtype(df[c]):
            df[c] = df[c].astype(object).where(df[c].notna(), "—").replace("", "—")
    return df


def seg(label, options, key, fmt=str, default=None):
    """Direct selection using buttons instead of a dropdown."""
    d = default if default in options else options[0]
    v = st.segmented_control(label, options, format_func=fmt, default=d, key=key)
    return v or d


# ----------------------------------------------------------------------------
# الشريط العلوي: Sign in as + القائمة (بدل الشريط الجانبي)
# ----------------------------------------------------------------------------
_top = st.columns([5, 9])
_sel = _top[0].segmented_control("Sign in as", ROLES, format_func=db.dept_en, default="PLN",
                                 key="role", label_visibility="collapsed")
ROLE = _sel or st.session_state.get("_role", "PLN")
st.session_state["_role"] = ROLE


def fmt_status(c): return db.STATUS.get(c, c)
def fmt_cat(c): return db.CATEGORY.get(c, c) if c else "Unclassified"
def fmt_prio(c): return db.PRIORITY.get(c, c)
NEED_LABELS = {"": "", "WO": "Work Order (Maintenance)", "SR": "Service Request",
           "BOTH": "Work Order + Service Request"}


def ex_label(code, short=False):
    if not code:
        return "Not specified"
    return db.dept_en(code) if short else db.dept_label(code)


def _h(n, mx=330):
    """Set table height based on row count, with a maximum to avoid page scrollbars."""
    return int(min(42 + 35 * max(n, 1), mx))


def with_equip(rows):
    """Add a display column for equipment: Tag No. or name with location."""
    for r in rows:
        r["equip"] = db.equipment_label(r.get("tag_no"), r.get("equipment_name"),
                                        r.get("location"))
    return rows


_FSEQ = {}                    # عدّاد يضمن مفتاحاً ثابتاً وفريداً لشريط كل جدول
MAX_ROWS = 300                # أقصى عدد صفوف يُرسل للمتصفح (البحث يعمل على كل السجلات)
MIN_ROWS_FILTER = 8           # الجداول الفرعية الأصغر من هذا لا تحتاج شريط بحث
DATE_KEYS = ("doc_date", "work_date", "from_date")


def _fkey(rename):
    import hashlib
    sig = "|".join(map(str, (rename or {}).values())) or "x"
    h = hashlib.md5(sig.encode("utf-8")).hexdigest()[:8]
    _FSEQ[h] = _FSEQ.get(h, 0) + 1
    return f"flt_{h}_{_FSEQ[h]}"


def _cap(rows, base):
    """Send at most MAX_ROWS rows to the browser unless the user asks for all."""
    if len(rows) <= MAX_ROWS:
        return rows
    if st.toggle(f"Show all {len(rows)} rows (slower)", key=base + "_all"):
        return rows
    st.caption(f"Showing the first {MAX_ROWS} of {len(rows)} — use search / date range to find older records.")
    return rows[:MAX_ROWS]


def filter_bar(rows, rename, always=True):
    """Filter bar above the table: text search across all columns and a date range when a date column exists.
    Return only matching rows."""
    if not rows or (not always and len(rows) < MIN_ROWS_FILTER):
        return rows
    base = _fkey(rename)
    dkey = next((c for c in (rename or {}) if c in DATE_KEYS), None)
    cols = st.columns([5, 3] if dkey else [1])
    q = cols[0].text_input("🔍 Search", key=base + "_q", placeholder="Enter part of a number, description, or equipment name...")
    rng = ()
    if dkey:
        rng = cols[1].date_input("📅 Date range (from – to)", value=(), key=base + "_d")
    terms = (q or "").lower().split()
    start = rng[0].isoformat() if len(rng) >= 1 else None
    end = rng[1].isoformat() if len(rng) == 2 else None
    keys = list(rename) if rename else list(rows[0])
    out = []
    for r in rows:
        if terms:
            text = " ".join(str(r.get(k) if r.get(k) is not None else "") for k in keys).lower()
            if not all(t in text for t in terms):
                continue
        if dkey and (start or end):
            v = str(r.get(dkey) or "")[:10]
            if not v or (start and v < start) or (end and v > end):
                continue
        out.append(r)
    if terms or start or end:
        st.caption(f"Showing {len(out)} of {len(rows)}")
        if not out:
            st.info("No results match the selected filters.")
    return _cap(out, base)


def show_df(rows, rename=None, height=None):
    if not rows:
        st.info("No data available.")
        return
    rows = filter_bar(rows, rename, always=False)
    if not rows:
        return
    df = pd.DataFrame(rows)
    if rename:
        df = df[[c for c in rename if c in df.columns]].rename(columns=rename)
    df = _show_blank(df)
    st.dataframe(df, hide_index=True, width="stretch", height=height or _h(len(df)))


def table_pick(rows, rename, key, height=None, progress=None):
    """Table where a row is selected by clicking; returns the selected row or None."""
    if not rows:
        st.info("No data available.")
        return None
    rows = filter_bar(rows, rename)
    if not rows:
        return None
    key = f"{key}_{len(rows)}"            # يتصفّر التحديد عند تغيّر نتيجة الفلتر
    df = pd.DataFrame(rows)
    df = _show_blank(df[[c for c in rename if c in df.columns]].rename(columns=rename))
    cfg = {}
    if progress and progress in df.columns:
        cfg[progress] = st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                        format="%d%%")
    ev = st.dataframe(df, hide_index=True, width="stretch", height=height or _h(len(df)),
                      on_select="rerun", selection_mode="single-row", key=key,
                      column_config=cfg)
    idx = ev.selection.rows
    return rows[idx[0]] if idx and idx[0] < len(rows) else None


def record_browser(rows, rename, key, title="Records", height=None, progress=None):
    """Show a full-width table until a row is selected, then use a 40/60 table/details layout."""
    if not rows:
        st.info("No data available.")
        return None, None
    filtered = filter_bar(rows, rename)
    if not filtered:
        return None, None

    import hashlib
    identity_key = next((k for k in ("doc_no", "group_no", "tag_no", "code", "id")
                         if k in filtered[0]), None)
    identity = [str(r.get(identity_key, "")) for r in filtered] if identity_key else [repr(r) for r in filtered]
    signature = hashlib.md5("|".join(identity).encode("utf-8")).hexdigest()[:10]
    table_key = f"rb_{key}_{signature}"
    hide_key = f"_rb_hide_{key}"
    if st.session_state.pop(hide_key, False):
        st.session_state.pop(table_key, None)

    state = st.session_state.get(table_key, {})
    try:
        indexes = list(state["selection"]["rows"])
    except (KeyError, TypeError, AttributeError):
        try:
            indexes = list(state.selection.rows)
        except (AttributeError, TypeError):
            indexes = []
    selected = filtered[indexes[0]] if indexes and indexes[0] < len(filtered) else None

    def render_table():
        df = pd.DataFrame(filtered)
        cols = [c for c in rename if c in df.columns]
        df = _show_blank(df[cols].rename(columns=rename))
        cfg = {}
        if progress and progress in df.columns:
            cfg[progress] = st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                            format="%d%%")
        st.dataframe(df, hide_index=True, width="stretch", height=height or _h(len(df)),
                     on_select="rerun", selection_mode="single-row", key=table_key,
                     column_config=cfg)

    if selected is None:
        with st.container(border=True):
            st.markdown(f"### {title}")
            st.caption(f"{len(filtered)} records — Select a row to view its details and available actions.")
            render_table()
        return None, None

    left, right = st.columns([4, 6], gap="medium")
    with left:
        with st.container(border=True):
            st.markdown(f"### {title}")
            st.caption(f"{len(filtered)} records — Select another row to change the details.")
            render_table()
    detail_panel = right.container(border=True)
    with detail_panel:
        head, close = st.columns([5, 2])
        head.markdown("### Selected Record")
        if close.button("↩ Hide Details", key=f"{key}_hide_details", use_container_width=True):
            st.session_state[hide_key] = True
            st.rerun()
    return selected, detail_panel


def hint(txt="Select a row to view its details and available actions."):
    st.caption("👈 " + txt)


def done(msg):
    """Close the dialog and display a brief notification after rerunning."""
    st.session_state["_flash"] = msg
    st.rerun()


def phone_is_valid(value):
    """Phone/contact field accepts digits only; an empty optional value is valid."""
    value = (value or "").strip()
    return not value or value.isdigit()


def equip_inputs(base):
    """Equipment identification fields: Tag No. or (name + location)."""
    c1, c2, c3 = st.columns(3)
    tag = c1.text_input("Equipment Tag No. (if available)", value=base.get("tag_no") or "")
    name = c2.text_input("Equipment name (engineering / industrial)",
                         value=base.get("name") or base.get("equipment_name") or "")
    loc = c3.text_input("Location", value=base.get("location") or "")
    return tag, name, loc


# ----------------------------------------------------------------------------
# مكونات مشتركة: مراحل الطلب والسجل الزمني
# ----------------------------------------------------------------------------
EVENT_AR = {
    "WR_CREATED": "📨 Work Request submitted to Planning", "WR_STUDY": "🔎 Planning review started",
    "WR_REJECTED": "⛔ Request rejected", "WR_CONVERTED": "📋 Work Order issued",
    "WR_TO_SR": "🚧 Converted to Service Request",
    "WO_ISSUED": "📋 Work Order issued", "JC_ISSUED": "⚡ Job Card issued",
    "JOB_CLASSIFIED": "🏷️ Planning set the maintenance category",
    "WO_INSPECTED": "✅ Inspection and stock check completed", "JOB_STARTED": "🛠️ Work started",
    "JOB_PROGRESS": "📈 Progress updated", "JOB_COMPLETED": "🏁 Work completed",
    "JOB_REOPENED": "↩️ Work reopened", "JOB_ACCEPTED": "🤝 Requesting Department accepted the work; closed",
    "JOB_RETURNED": "↩️ Requesting Department returned the work to Maintenance",
    "JOB_AUTO_CLOSED": "⏱️ Work administratively closed — requester did not confirm acceptance",
    "SR_ISSUED": "🚧 Service Request issued",
    "SR_STATUS": "🚧 Service Request status updated",
    "WG_CREATED": "🧩 Work Group created", "WG_LINKED": "🔗 Document linked to Work Group",
    "WG_UNLINKED": "✂️ Document unlinked", "WG_STATUS": "🧩 Work Group status updated",
    "TIR_ISSUED": "🔬 Technical Inspection Request issued",
    "TIR_SUBMITTED": "📋 Inspection Department submitted the technical report",
    "TIR_STATUS": "🔬 Inspection Request status updated",
    "TIR_FORM_SAVED": "📝 Inspection form draft saved",
    "JC_CLOSED_PLN": "⛔ Job Card closed — the work needs Planning",
    "JC_AUTO_CLOSED": "⏱️ Job Card closed automatically (end of day)",
    "WR_FROM_JC": "📨 Work Request raised from a Job Card",
    "TIR_STUDIED": "🔎 Inspection studied the request and sent its requirements to Planning",
    "TIR_REQ_LINKED": "🔗 Planning issued a document for an inspection requirement",
    "TIR_SENT": "📤 Planning sent the inspection report to the requesting department",
    "WR_FROM_TIR": "📨 Work Request raised following an inspection report",
    "PLAN_IMPORTED": "🗓️ Annual Plan approved",
    "PLAN_KEPT": "📝 Plan item retained with reason"}


def stepper(step):
    cells = []
    for n, name in enumerate(db.STEP_NAMES, 1):
        cls = "todo" if step == 0 else ("done" if n < step else ("cur" if n == step else "todo"))
        cells.append(f'<div class="stp {cls}"><b>{n}</b>{name}</div>')
    st.markdown('<div class="stepper">' + "".join(cells) + "</div>", unsafe_allow_html=True)


def show_timeline(doc_no, height=190):
    with st.container(height=height, border=True):
        evs = db.timeline(doc_no)
        if not evs:
            st.caption("No events recorded.")
        for e in evs:
            who = f" _({db.dept_en(e['actor'])})_" if e["actor"] else ""
            note = f" — {e['note']}" if e["note"] else ""
            st.markdown(f"`{ltr(e['ts'])}` · {EVENT_AR.get(e['event'], e['event'])} · "
                        f"**{e['doc_no']}**{note}{who}")


JOB_COLS = {"doc_no": "No.", "doc_type": "Type", "doc_date": "Date", "equip": "Equipment",
            "description": "Description", "category": "Maintenance", "priority": "Priority",
            "stage": "Stage", "progress": "Progress %", "age": "Age (days)"}


def job_rows(jobs):
    out = with_equip([dict(j) for j in jobs])
    for j in out:
        j["category"] = fmt_cat(j["category"] if j.get("cat_set", 1) else None)
        j["priority"] = fmt_prio(j["priority"])
    return out


# ============================================================================
# النوافذ المنبثقة (كل إصدار جديد في أي قسم)
# ============================================================================
def _link_req(sel_req, doc_no):
    """Link a newly issued WO / SR to the inspection requirement it satisfies; returns a message suffix."""
    if not sel_req:
        return ""
    tir_no, rid = sel_req.split("|")
    try:
        db.tir_link_req(tir_no, rid, doc_no, ROLE)
        return f" — linked to inspection requirement {tir_no} {rid}"
    except ValueError as e:
        return f" — could not link the inspection requirement: {e}"


@st.dialog("New Work Request (WR)", width="large")
def dlg_wr(parent=None, from_tir=None, from_jc=None):
    """A Work Request can be submitted by any department for equipment maintenance or assistance. Planning determines the required document(s)."""
    maint_req = ROLE in MAINT
    if parent:
        st.info(f"Request from {db.dept_en(ROLE)} Related to Job {parent['doc_no']} — "
                "Planning reviews it and issues a Work Order, Service Request, or both.")
        eq = {}
        base = {"tag_no": parent["tag_no"], "name": parent["equipment_name"],
                "location": parent["location"]}
    elif from_tir:
        st.info(f"Work Request to carry out the recommendations of Inspection Report {from_tir['doc_no']} — "
                "Planning reviews it and issues the Work Order / Service Request.")
        eq = {}
        base = {"tag_no": from_tir.get("tag_no"), "name": from_tir.get("equipment_name"),
                "location": from_tir.get("location")}
    elif from_jc:
        st.info(f"Work Request to continue the work of Job Card {from_jc['doc_no']}, which was closed because it needed "
                f"Planning ({db.JC_CLOSE_REASONS.get(from_jc.get('close_reason'), '')}).")
        eq = {}
        base = {"tag_no": from_jc.get("tag_no"), "name": from_jc.get("equipment_name"),
                "location": from_jc.get("location")}
    else:
        st.caption("For equipment maintenance or assistance. Planning issues a Work Order and/or Service Request "
                   "(crane, scaffolding, transport, contractor, etc.) as required.")
        eq = {}
        base = {}
    with st.form(f"wr_form_{eq.get('id', 0)}_{(parent or {}).get('doc_no', '')}"
                 f"_{(from_tir or {}).get('doc_no', '')}_{(from_jc or {}).get('doc_no', '')}", border=False):
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("Issue Date", value=date.today(), disabled=True)
        if ROLE in PROD or maint_req:
            req = ROLE
            c2.markdown(f"**Requesting Department:** {db.dept_label(ROLE)}")
        else:
            req = c2.selectbox("Requesting Department", PROD + ["PLN", "INSP"], format_func=db.dept_label)
        contact = c3.text_input("Contact / Phone")
        ex = seg("Executing Party", [""] + [m for m in MAINT if m != ROLE], "wr_ex",
                 lambda x: "Not specified" if not x else db.dept_label(x))
        prio = seg("Priority", list(db.PRIORITY), "wr_prio", fmt_prio)
        tag, ename, eloc = equip_inputs(base)
        desc = st.text_area("Work or assistance description", height=80,
                            value=(f"Per Inspection Report {from_tir['doc_no']}:\n"
                                   + (from_tir.get("recommendations") or "")) if from_tir else
                            (f"Continuation of Job Card {from_jc['doc_no']} ({from_jc['description']}).\n"
                             f"Closed because: {db.JC_CLOSE_REASONS.get(from_jc.get('close_reason'), '')} — "
                             f"{from_jc.get('close_note') or ''}") if from_jc else "")
        k1, k2, k3 = st.columns(3)
        cc = k1.text_input("Benefiting Cost Center", value=(from_jc or {}).get("cost_center") or "")
        mats = k2.text_input("Materials (if known)")
        spec = k3.text_input("Special equipment (if known)")
        legacy = st.text_input("Legacy Reference (optional)")
        if st.form_submit_button("Save & Submit to Planning", type="primary"):
            try:
                if not phone_is_valid(contact):
                    st.error("Contact / Phone must contain digits only.")
                    return
                no = db.create_wr(d, req, ex, desc, tag, prio, contact, legacy or None,
                                  equipment_name=ename, location=eloc, actor=ROLE,
                                  parent_job=(parent or {}).get("doc_no"),
                                  cost_center=cc, materials=mats, special_equip=spec)
                if from_jc:
                    db.link_wr_to_jc(no, from_jc["doc_no"], ROLE)
                if from_tir:
                    db.link_wr_to_tir(no, from_tir["doc_no"], ROLE)
                done(f"Saved — Request No.: {no} — Submitted to Planning")
            except ValueError as e:
                st.error(str(e))


@st.dialog("Job Card (JC)", width="large")
def dlg_jc():
    st.caption("For minor jobs completed on the same day without complex spare parts or contractors. "
               "It goes directly to Maintenance, bypasses Planning, and is excluded from Planning reports.")
    eq = {}
    with st.form(f"jc_form_{eq.get('id', 0)}", border=False):
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("Issue Date", value=date.today(), disabled=True)
        if ROLE in PROD:
            req = ROLE
            c2.markdown(f"**Requesting Department:** {db.dept_label(ROLE)}")
        else:
            req = c2.selectbox("Requesting Department", PROD, format_func=db.dept_label)
        contact = c3.text_input("Contact / Phone")
        ex = seg("Executing Party", MAINT, "jc_ex", db.dept_label)
        prio = seg("Priority", list(db.PRIORITY), "jc_prio", fmt_prio)
        tag, ename, loc = equip_inputs(eq)
        c4, c5 = st.columns(2)
        cc = c4.text_input("Cost Center", value=eq.get("cost_center") or "")
        legacy = c5.text_input("Legacy Reference (optional)")
        desc = st.text_area("Work Description", height=80)
        if st.form_submit_button("Issue Job Card", type="primary"):
            try:
                if not phone_is_valid(contact):
                    st.error("Contact / Phone must contain digits only.")
                    return
                no = db.create_job("JC", d, ex, desc, requesting_dept=req, actor=ROLE,
                                   tag_no=tag, equipment_name=ename, cost_center=cc,
                                   location=loc, priority=prio, contact=contact,
                                   legacy_no=legacy or None)
                done(f"Issued — Job Card No.: {no}")
            except ValueError as e:
                st.error(str(e))


@st.dialog("Issue Work Order (WO)", width="large")
def dlg_wo(wr=None):
    if not wr or not wr.get("doc_no"):
        st.error("A Work Order cannot be issued without a Work Request.")
        return
    extra = wr.get("status") in ("CONVERTED", "SERVICE")
    st.info(f"Additional Work Order for request {wr['doc_no']}" if extra
            else f"Convert request {wr['doc_no']} to a Work Order")
    pinfo = db.plan_wr_info(wr["doc_no"])
    if pinfo:
        st.info("🗓️ Annual Plan — Required Departments: "
                + " + ".join(db.plan_dept_en(x) for x in pinfo["depts"])
                + "  (issue one Work Order per Maintenance department and a Service Request for any external party)")
    if not wr.get("exec_dept"):
        st.warning("The requester did not specify the executing department. Select it below.")
    base = {"tag_no": wr.get("tag_no"), "name": wr.get("equipment_name"),
            "location": wr.get("location")}
    with st.form(f"wo_form_{wr.get('doc_no', '')}", border=False):
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("Issue Date", value=date.today(), disabled=True)
        est = c2.number_input("Estimated Duration (hours)", min_value=0.0, step=1.0)
        cc = c3.text_input("Cost Center", value=wr.get("cost_center") or "")
        req = wr.get("requesting_dept")
        st.markdown(f"**Requesting Department:** {db.dept_label(req)}")
        ex = seg("Executing Party", MAINT, "wo_ex", db.dept_label, default=wr.get("exec_dept"))
        cat = seg("Maintenance Category (set by Planning)", list(db.CATEGORY), "wo_cat", fmt_cat,
                  default="PM" if "Scheduled" in (wr.get("need") or "") else "CM")
        prio = seg("Priority", list(db.PRIORITY), "wo_prio", fmt_prio, default=wr.get("priority"))
        tag, ename, loc = equip_inputs(base)
        desc = st.text_area("Work Description", value=wr.get("description") or "", height=80)
        h1, h2, h3, h4 = st.columns(4)
        crane = h1.checkbox("Crane")
        fork = h2.checkbox("Forklift")
        scaf = h3.checkbox("Scaffolding")
        trans = h4.checkbox("Transport")
        note = st.text_input("Planning Notes")
        _reqs = {r["tir_no"] + "|" + r["id"]: r["label"] for r in db.tir_open_reqs(wr.get("doc_no"))}
        sel_req = st.selectbox("Satisfies Inspection requirement (optional)", [""] + list(_reqs),
                               format_func=lambda k: "—" if not k else _reqs[k]) if _reqs else ""
        if st.form_submit_button("Issue Work Order", type="primary"):
            try:
                no = db.create_job("WO", d, ex, desc, wr_no=wr.get("doc_no"), actor=ROLE,
                                   requesting_dept=req, tag_no=tag, equipment_name=ename,
                                   location=loc, cost_center=cc, category=cat, priority=prio,
                                   est_hours=est or None, contact=wr.get("contact"),
                                   planning_note=note, need_crane=crane, need_forklift=fork,
                                   need_scaffold=scaf, need_transport=trans)
                msg = f"Issued — Work Order No.: {no}"
                msg += _link_req(sel_req, no)
                if crane or fork or scaf or trans:
                    msg += " — Support services required: issue a Service Request (SR)"
                done(msg)
            except ValueError as e:
                st.error(str(e))


@st.dialog("Issue Service Request (SR)", width="large")
def dlg_sr(wr=None, job_no=None):
    if not wr or not wr.get("doc_no"):
        st.error("A Service Request cannot be issued without a Work Request.")
        return
    wr = wr or {}
    if wr:
        extra = wr.get("status") in ("CONVERTED", "SERVICE")
        st.info(f"Additional Service Request for request {wr['doc_no']}" if extra
                else f"Convert request {wr['doc_no']} directly to a Service Request (without a Work Order)")
    pinfo = db.plan_wr_info(wr["doc_no"])
    if pinfo and "OTH" in pinfo["depts"]:
        st.info("🗓️ Annual Plan item maintained by an external party"
                + (f" — Plan Notes (company): {pinfo['notes']}" if pinfo["notes"] else ""))
    jobs = [j["doc_no"] for j in db.query(
        "SELECT doc_no FROM jobs WHERE doc_type='WO' AND status!='C' ORDER BY doc_no")]
    # Executing Party: من قائمة الجهات السابقة المحفوظة , وإلا تُكتب جهة جديدة فتُضاف في آخر القائمة
    new_key = "__new__"
    saved = db.list_executors()
    ex_sel = st.selectbox("Executing Party *", saved + [new_key], index=None,
                          placeholder="Select the executing party from the list...",
                          key=f"sr_exec_{wr.get('doc_no', '')}",
                          format_func=lambda x: "➕ New party (not in the list)…"
                          if x == new_key else x)
    ex_new = ""
    if ex_sel == new_key:
        ex_new = st.text_input("New party name (will be added to the list)",
                               key=f"sr_exec_new_{wr.get('doc_no', '')}")
    with st.form(f"sr_form_{wr.get('doc_no', '')}_{job_no or ''}", border=False):
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("Issue Date", value=date.today(), disabled=True)
        est = c2.number_input("Estimated Duration (hours)", min_value=0.0, step=1.0)
        if wr:
            job = None
            c3.markdown(f"**Request:** {wr['doc_no']}")
        else:
            job = c3.selectbox("Related Work Order (optional)", [""] + jobs,
                               index=(jobs.index(job_no) + 1) if job_no in jobs else 0,
                               format_func=lambda x: "—" if not x else x)
        req = wr.get("requesting_dept")
        st.markdown(f"**Requesting Department:** {db.dept_label(req)}")
        stype = seg("Service Type", list(db.SERVICE_TYPES), "sr_type", lambda x: db.SERVICE_TYPES[x],
                    default="OTHER" if pinfo and "OTH" in pinfo["depts"] else None)
        tag, ename, eloc = equip_inputs({"tag_no": wr.get("tag_no"),
                                         "name": wr.get("equipment_name"),
                                         "location": wr.get("location")})
        c7, c8, c9 = st.columns(3)
        fd = c7.date_input("From Date", date.today())
        td = c8.date_input("To Date", date.today())
        contact = c9.text_input("Contact / Phone", value=wr.get("contact") or "")
        desc = st.text_area("Required Service Description", value=wr.get("description") or "", height=70)
        note = st.text_input("Planning Notes")
        _reqs = {r["tir_no"] + "|" + r["id"]: r["label"] for r in db.tir_open_reqs(wr.get("doc_no"))}
        sel_req = st.selectbox("Satisfies Inspection requirement (optional)", [""] + list(_reqs),
                               format_func=lambda k: "—" if not k else _reqs[k]) if _reqs else ""
        if st.form_submit_button("Issue Service Request", type="primary"):
            try:
                if not phone_is_valid(contact):
                    st.error("Contact / Phone must contain digits only.")
                    return
                no = db.create_sr(d, stype, desc, req, job or None, tag, fd, td,
                                  est or None, None, contact, note,
                                  equipment_name=ename, location=eloc, actor=ROLE,
                                  wr_no=wr.get("doc_no"),
                                  executor=(ex_new if ex_sel == new_key else ex_sel))
                done(f"Issued — Service Request No.: {no}" + _link_req(sel_req, no))
            except ValueError as e:
                st.error(str(e))


@st.dialog("Reject Work Request")
def dlg_reject(wr_no):
    st.markdown(f"**{wr_no}**")
    reason = st.text_input("Rejection Reason (visible to requester)")
    if st.button("Confirm Rejection", type="primary"):
        try:
            db.wr_review(wr_no, "REJECT", ROLE, reason)
            done(f"Request rejected {wr_no}")
        except ValueError as e:
            st.error(str(e))


@st.dialog("Set Maintenance Category")
def dlg_classify(job):
    st.markdown(f"**{job['doc_no']}** — {job['description'][:80]}")
    cat = seg("Maintenance Category", list(db.CATEGORY), f"cls_{job['doc_no']}", fmt_cat, default="CM")
    if st.button("Save Classification", type="primary"):
        db.set_job_category(job["doc_no"], cat, ROLE)
        done(f"Classified {job['doc_no']}: {fmt_cat(cat)}")


@st.dialog("New Work Group", width="large")
def dlg_group():
    free = db.query("SELECT doc_no, description FROM work_requests WHERE group_no IS NULL"
                    " AND status!='REJECTED' ORDER BY doc_date DESC, doc_no DESC")
    names = {w["doc_no"]: f"{w['doc_no']} — {(w['description'] or '')[:45]}" for w in free}
    with st.form("wg_form", border=False):
        c1, c2 = st.columns(2)
        d = c1.date_input("Issue Date", value=date.today(), disabled=True)
        title = c2.text_input("Work Title")
        req = seg("Requesting Department", [""] + PROD, "wg_req",
                  lambda x: "—" if not x else db.dept_en(x))
        tag, ename, loc = equip_inputs({})
        wrs = st.multiselect("Link Work Requests (WR)", list(names), format_func=names.get,
                             help="Only the Work Request is linked; its Work Orders, Services, and Inspections appear "
                                  "under the Work Group automatically.")
        if st.form_submit_button("Create", type="primary"):
            try:
                no = db.create_group(d, title, req or None, tag, ename, loc, actor=ROLE,
                                     wr_nos=wrs)
                done(f"Created — {no}" + (f" and linked {len(wrs)} Work Request(s)" if wrs else ""))
            except ValueError as e:
                st.error(str(e))


@st.dialog("Link Work Requests to Work Group", width="large")
def dlg_link(group_no):
    st.markdown(f"Work Group: **{group_no}** — select a Work Request from the table:")
    rows = db.query("SELECT doc_no, doc_date, requesting_dept, description, status, group_no"
                    " FROM work_requests ORDER BY doc_date DESC, doc_no DESC")
    for r in rows:
        r["requesting_dept"] = db.dept_en(r["requesting_dept"])
        r["status"] = db.WR_STATUS.get(r["status"], r["status"])
    sel = table_pick(rows, {"doc_no": "No.", "doc_date": "Date", "requesting_dept": "Requester",
                            "description": "Description", "status": "Status",
                            "group_no": "Work Group"}, "lnk_tbl", height=300)
    if sel:
        c1, c2 = st.columns(2)
        if sel["group_no"] != group_no and c1.button(f"🔗 Link {sel['doc_no']}", type="primary"):
            try:
                db.link_to_group(sel["doc_no"], group_no, ROLE)
                done(f"Linked {sel['doc_no']} to {group_no}")
            except ValueError as e:
                st.error(str(e))
        if sel["group_no"] == group_no and c2.button("✂️ Unlink"):
            db.link_to_group(sel["doc_no"], None, ROLE)
            done(f"Unlinked {sel['doc_no']}")


@st.dialog("Issue Technical Inspection Request (TIR)", width="large")
def dlg_tir(wr=None):
    """Planning issues a general Technical Inspection Request to INSP, linked to the main Work Request.
    The inspection types decide which sections the Inspection report contains."""
    if not wr or not wr.get("doc_no"):
        st.error("A Technical Inspection Request cannot be issued without a Work Request.")
        return
    st.info(f"Technical Inspection Request for Work Request {wr['doc_no']} — routed to Inspection (INSP). "
            "Inspection studies it and sends Planning any preparation it needs (scaffolding, opening, cleaning...).")
    base = {"tag_no": wr.get("tag_no"), "name": wr.get("equipment_name"), "location": wr.get("location")}
    with st.form(f"tir_form_{wr.get('doc_no', '')}", border=False):
        c1, c2, c3 = st.columns(3)
        d = c1.date_input("Issue Date", value=date.today(), disabled=True)
        reason = c2.selectbox("Reason for inspection", list(db.TIR_REASONS),
                              format_func=lambda x: db.TIR_REASONS[x])
        state = c3.selectbox("Equipment status", list(db.EQUIP_STATES),
                             format_func=lambda x: db.EQUIP_STATES[x])
        types = st.multiselect("Inspection types required *", list(db.INSP_TYPES),
                               format_func=lambda x: db.INSP_TYPES[x],
                               help="The inspection report shows a section for each selected type")
        c4, c5 = st.columns(2)
        prio = c4.selectbox("Priority", list(db.PRIORITY), format_func=fmt_prio)
        due = c5.date_input("Required by (optional)", value=None)
        tag, ename, eloc = equip_inputs(base)
        note = st.text_area("Inspection Note (required scope of work)",
                            value=wr.get("description") or "", height=80)
        if st.form_submit_button("Issue Inspection Request", type="primary"):
            try:
                no = db.create_tir(d, wr.get("doc_no"), types[0] if types else "", tag, ename, eloc,
                                   requesting_note=note or None, actor=ROLE, types=types,
                                   reason=reason, priority=prio, due_date=due, equip_state=state)
                done(f"Issued — Inspection Request No.: {no} — submitted to Inspection")
            except ValueError as e:
                st.error(str(e))


@st.dialog("Inspection Requirements", width="large")
def dlg_tir_study(tir):
    """Inspection studies the request and tells Planning what must be prepared before the inspection."""
    st.markdown(f"**{tir['doc_no']}** — {tir['types_ar']}")
    if tir.get("requesting_note"):
        st.info(f"Planning Note: {tir['requesting_note']}")
    st.caption("Record what Planning must arrange before inspection (scaffolding, opening a tank / vessel, surface "
               "cleaning, insulation removal, isolation...). Planning issues the Service Request or Work Order on the "
               "main Work Request. Leave the table empty if no preparation is needed.")
    kinds = list(db.REQ_KINDS.values())
    code = {v: k for k, v in db.REQ_KINDS.items()}
    base = pd.DataFrame([{"id": r["id"], "kind": db.REQ_KINDS.get(r["kind"]), "detail": r["detail"],
                          "state": r["state_ar"]} for r in tir["reqs"]],
                        columns=["id", "kind", "detail", "state"])
    ed = st.data_editor(base, key=f"treq_{tir['doc_no']}", num_rows="dynamic", hide_index=True,
                        width="stretch", disabled=["id", "state"],
                        column_config={"id": st.column_config.TextColumn("Ref"),
                                       "kind": st.column_config.SelectboxColumn("Requirement", options=kinds),
                                       "detail": st.column_config.TextColumn("Location / details", width="large"),
                                       "state": st.column_config.TextColumn("Planning document")})
    rows = [{"id": (r["id"] if isinstance(r["id"], str) and r["id"] else None), "kind": code.get(r["kind"], ""),
             "detail": r["detail"] if isinstance(r["detail"], str) else ""}
            for r in ed.to_dict("records") if isinstance(r["kind"], str) and r["kind"]]
    label = "📤 Send Requirements to Planning" if rows else "Confirm: no preparation required"
    if st.button(label, type="primary", key=f"treq_send_{tir['doc_no']}"):
        try:
            db.tir_study(tir["doc_no"], rows, ROLE)
            done(f"Requirements sent to Planning for {tir['doc_no']}" if rows
                 else f"Study recorded for {tir['doc_no']} — no preparation required")
        except ValueError as e:
            st.error(str(e))


@st.dialog("Technical Inspection Report", width="large")
def dlg_tir_submit(tir):
    """INSP records the summary of its (paper) report: save a draft or submit and close."""
    st.markdown(f"**{tir['doc_no']}** — {tir.get('types_ar') or '—'}")
    if tir.get("requesting_note"):
        st.info(f"Planning Note: {tir['requesting_note']}")
    form = tir_form.edit_form(tir, tir_form.load(tir))
    errs, warns = tir_form.validate(form)
    for e in errs:
        st.error(e)
    for w in warns:
        st.warning(w)
    c1, c2 = st.columns(2)
    if c1.button("💾 Save Draft", key=f"tf_draft_{tir['doc_no']}", use_container_width=True):
        try:
            db.save_tir_form(tir["doc_no"], form, ROLE)
            done(f"Draft saved {tir['doc_no']}")
        except ValueError as e:
            st.error(str(e))
    if c2.button("Submit Report to Planning", type="primary", disabled=bool(errs),
                 key=f"tf_submit_{tir['doc_no']}", use_container_width=True):
        s_ = tir_form.summarize(form)
        try:
            db.submit_tir(tir["doc_no"], s_["findings"], s_["recommendations"] or None, actor=ROLE,
                          form_data=form)
            done(f"✅ Inspection report {tir['doc_no']} submitted to Planning")
        except ValueError as e:
            st.error(str(e))


@st.dialog("Add Equipment")
def dlg_equipment():
    st.caption("Major equipment is registered by Tag No.; other equipment by name and location.")
    with st.form("eq_form", border=False):
        a_, b_ = st.columns(2)
        tag = a_.text_input("Tag No. (if available)")
        name = b_.text_input("Equipment Name")
        c_, d_ = st.columns(2)
        loc = c_.text_input("Location")
        cc = d_.text_input("Cost Center")
        e1, e2, e3 = st.columns(3)
        ecls = e1.text_input("Equipment Type (Pumps, Valves, etc.)")
        edept = e2.selectbox("Responsible Maintenance Department", [""] + MAINT,
                             format_func=lambda x: "—" if not x else db.dept_label(x))
        eint = e3.number_input("Overhaul Interval (operating hours)", min_value=0.0, step=1000.0)
        if st.form_submit_button("Add", type="primary"):
            try:
                db.save_equipment(tag, name, loc, cc, eq_class=ecls or None,
                                  maint_dept=edept or None, overhaul_interval=eint or None)
                done("Equipment added")
            except ValueError as e:
                st.error(str(e))


@st.dialog("Record Work Hours", width="large")
def dlg_labor(job_no):
    st.markdown(f"**{job_no}**")
    with st.form(f"lab_{job_no}", border=False):
        a, b = st.columns(2)
        wd = a.date_input("Work Date", date.today(), disabled=True)
        h1, h2, h3, h4 = st.columns(4)
        spv = h1.number_input("SPV", min_value=0.0, step=0.5)
        tech = h2.number_input("TECH", min_value=0.0, step=0.5)
        ot = h3.number_input("OV.T", min_value=0.0, step=0.5)
        cont = h4.number_input("CONT", min_value=0.0, step=0.5)
        note = st.text_input("Note")
        if st.form_submit_button("Record", type="primary"):
            try:
                db.add_labor(job_no, wd, 0, spv, tech, ot, cont, note or None)
                done("Hours recorded")
            except ValueError as e:
                st.error(str(e))


@st.dialog("Add Spare Part", width="large")
def dlg_spare(job_no):
    st.markdown(f"**{job_no}**")
    with st.form(f"sp_{job_no}", border=False):
        a, b, c = st.columns([1, 3, 1])
        stock = a.text_input("Stock No.")
        sd = b.text_input("Description")
        un = c.text_input("Unit")
        d_, e = st.columns(2)
        q = d_.number_input("Quantity", min_value=0.0, step=1.0)
        rt = e.number_input("Returned", min_value=0.0, step=1.0)
        if st.form_submit_button("Add", type="primary"):
            db.add_spare(job_no, stock, sd, un, q, rt)
            done("Added successfully")


# ----------------------------------------------------------------------------
# لوحة المتابعة
# ----------------------------------------------------------------------------
def page_dashboard():
    rows = db.stats("WO")
    tot = {"P": 0, "O": 0, "C": 0}
    for r in rows:
        tot[r["status"]] += r["n"]
    wr_new = db.query("SELECT COUNT(*) n FROM work_requests WHERE status='NEW'")[0]["n"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("New Work Requests (WR)", wr_new)
    c2.metric("Pending", tot["P"])
    c3.metric("In Progress", tot["O"])
    c4.metric("Completed", tot["C"])
    if rows:
        df = pd.DataFrame(rows)
        df["Department"] = df["exec_dept"].map(db.dept_en)
        df["Status"] = df["status"].map(fmt_status)
        pivot = df.pivot_table(index="Department", columns="Status", values="n",
                               aggfunc="sum", fill_value=0)
        st.bar_chart(pivot, height=300)
    else:
        st.info("No Work Orders have been recorded yet.")
    jc = db.query("SELECT COUNT(*) n FROM jobs WHERE doc_type='JC'")[0]["n"]
    st.caption(f"Job Cards ({jc}) go directly to Maintenance and are excluded from general Planning reports.")


# ----------------------------------------------------------------------------
# متابعة الأعمال: عرض فقط للتخطيط , وإدخال لأقسام الصيانة
# ----------------------------------------------------------------------------
def _jc_close_ui(job):
    """Maintenance closes a Job Card whose work turned out to need Planning (spare parts, welding...)."""
    if job["doc_type"] != "JC" or job["status"] == "C":
        return
    no = job["doc_no"]
    with st.expander("⛔ Close — this work needs Planning (spare parts, welding...)"):
        st.caption("The Job Card closes today and the requesting department starts a Work Request. "
                   "If the work is finished, set the status to Completed instead.")
        reasons = [k for k in db.JC_CLOSE_REASONS if k != "AUTO"]
        r = st.selectbox("Reason", reasons, format_func=db.JC_CLOSE_REASONS.get, key=f"jcr_{no}")
        note = st.text_area("Notes * (what was done and what remains)", height=70, key=f"jcn_{no}")
        if st.button("Close Job Card", key=f"jcc_{no}"):
            try:
                db.close_job_planning(no, r, note, ROLE)
                done(f"Job Card {no} closed — the requesting department will raise a Work Request")
            except ValueError as e:
                st.error(str(e))


def _job_tir(job):
    """Inspection verdict + welding / coating requirements for this job. Maintenance sees a report only after
    Planning sent it with recommendations; Planning and Inspection see it as soon as it is submitted."""
    for t in db.tir_for_job(job):
        data = tir_form.load(t)
        if t["status"] != "C" or not data:
            continue
        if ROLE in MAINT and not (t.get("sent_at") and t.get("sent_with_recs")):
            continue
        with st.expander(f"🔬 {t['doc_no']} — Inspection Requirements", expanded=False):
            tir_form.requirements_card(data)


def _job_readonly(job):
    """View job status only (Planning): no data entry."""
    st.markdown(f"**{job['doc_no']}** — "
                f"{db.equipment_label(job['tag_no'], job['equipment_name'], job['location'])}")
    st.caption(job["description"])
    t1, t2, t3, t4 = st.tabs(["Status", "Hours", "Spare Parts", "Timeline"])
    with t1:
        _job_tir(job)
        c = st.columns(3)
        c[0].metric("Status", fmt_status(job["status"]))
        c[1].metric("Progress", f"{100 if job['status'] == 'C' else (job['progress'] or 0)}%")
        c[2].metric("Executing Department", db.dept_en(job["exec_dept"]))
        st.progress((100 if job["status"] == "C" else (job["progress"] or 0)) / 100)
        info = [f"**Maintenance Category:** {fmt_cat(job['category'] if job['cat_set'] else None)}",
                f"**Started:** {ltr(job['start_date'] or '—')}",
                f"**End Date:** {ltr(job['end_date'] or '—')}",
                f"**Estimated Hours:** {job['est_hours'] or '—'}"]
        if job["group_no"]:
            info.append(f"**Work Group:** {job['group_no']}")
        st.markdown(" · ".join(info))
        if job["remarks"]:
            st.info(job["remarks"])
        srs = db.query("SELECT doc_no, service_type, status FROM service_requests"
                       " WHERE job_no=?", (job["doc_no"],))
        if srs:
            st.markdown("**Service Requests:** " + ", ".join(
                f"{s['doc_no']} ({db.SERVICE_TYPES.get(s['service_type'], '')} — "
                f"{fmt_status(s['status'])})" for s in srs))
        cw = db.query("SELECT doc_no, requesting_dept, exec_dept FROM work_requests"
                      " WHERE parent_job=?", (job["doc_no"],))
        if cw:
            st.markdown("**Work Requests Raised During Execution:** " + ", ".join(
                f"{w['doc_no']} ({db.dept_en(w['requesting_dept'])}→{db.dept_en(w['exec_dept'])})"
                for w in cw))
    with t2:
        rows = db.query(
            "SELECT work_date, workers, spv_hours, tech_hours, ot_hours, cont_hours"
            " FROM labor_log WHERE job_no=? ORDER BY work_date DESC", (job["doc_no"],))
        show_df(rows, {"work_date": "Date", "workers": "Workers", "spv_hours": "SPV",
                       "tech_hours": "TECH", "ot_hours": "OV.T", "cont_hours": "CONT"}, 220)
    with t3:
        show_df(db.query("SELECT * FROM spare_parts WHERE job_no=?", (job["doc_no"],)),
                {"stock_no": "Stock No.", "description": "Description", "unit": "Unit",
                 "qty": "Quantity", "returned": "Returned"}, 220)
    with t4:
        show_timeline(job["doc_no"], 230)


def _job_editable(job):
    """Track and update jobs (Maintenance departments)."""
    no = job["doc_no"]
    if job.get("status") == "C" and (job.get("accepted_date") or job.get("auto_closed_at")):
        if job.get("auto_closed_at"):
            st.warning(f"🔒 Administratively closed on {job['auto_closed_at']} because the requesting "
                       "department did not confirm acceptance within the allowed period. "
                       "This is not an actual acceptance.")
        else:
            st.success(f"🔒 Work is complete and finally accepted on {job['accepted_date']} "
                       f"({job.get('accepted_by') or ''}) — closed and cannot be modified.")
        _job_readonly(job)
        return
    st.markdown(f"**{no}** — "
                f"{db.equipment_label(job['tag_no'], job['equipment_name'], job['location'])}")
    st.caption(job["description"])
    t1, t2, t3, t4 = st.tabs(["Status & Inventory", "Work Hours", "Spare Parts", "Related Requests"])
    with t1:
        _job_tir(job)
        if job["doc_type"] == "WO":
            chk = st.checkbox("Theoretical stock check completed", value=bool(job["stock_checked"]),
                              key=f"chk_{no}")
            if chk != bool(job["stock_checked"]):
                db.set_stock_checked(no, chk, ROLE)
                st.rerun()
        _jc_close_ui(job)
        new = seg("New Status", list(db.STATUS), f"st_{no}", fmt_status, default=job["status"])
        prog = st.slider("Progress %", 0, 100, int(job["progress"] or 0), key=f"pg_{no}")
        rem = st.text_input("Remarks", value=job["remarks"] or "", key=f"rm_{no}")
        if st.button("Update Status", type="primary", key=f"up_{no}"):
            try:
                db.set_job_status(no, new, prog, rem or None, actor=ROLE)
                st.session_state["_flash"] = "Updated"
                st.rerun()
            except ValueError as e:
                st.error(str(e))
    with t2:
        if st.button("➕ Record Hours", key=f"labbtn_{no}"):
            dlg_labor(no)
        show_df(db.query("SELECT * FROM labor_log WHERE job_no=? ORDER BY work_date DESC", (no,)),
                {"work_date": "Date", "workers": "Workers", "spv_hours": "SPV",
                 "tech_hours": "TECH", "ot_hours": "OV.T", "cont_hours": "CONT",
                 "note": "Note"}, 190)
    with t3:
        if st.button("➕ Spare Part", key=f"spbtn_{no}"):
            dlg_spare(no)
        show_df(db.query("SELECT * FROM spare_parts WHERE job_no=?", (no,)),
                {"stock_no": "Stock No.", "description": "Description", "unit": "Unit",
                 "qty": "Quantity", "returned": "Returned"}, 190)
    with t4:
        if job.get("plan_code"):
            st.warning(f"🔒 Work scheduled in the Annual Plan (item code {job['plan_code']}): Planning & Programming "
                       "is responsible for issuing all required Work Orders, Service Requests, and Inspections. Maintenance departments must not submit requests for this item. "
                       "Maintenance departments must not submit requests against it. Enter requirements in the Status tab Remarks for Planning to follow up.")
        else:
            st.caption("Need equipment maintenance or assistance during execution? Submit a Work Request — Planning will review it "
                       "and issue a Work Order and/or Service Request, or assign the executing department if not specified.")
            if st.button("➕ Work Request / Assistance", key=f"subwr_{no}", type="primary"):
                dlg_wr(parent=job)
        wrs = db.query("SELECT * FROM work_requests WHERE parent_job=? ORDER BY doc_no", (no,))
        show_df([dict(w, status=db.WR_STATUS.get(w["status"], w["status"]),
                      target=ex_label(w["exec_dept"], True),
                      need_ar=NEED_LABELS.get(w["need"], w["need"] or ""),
                      docs=", ".join(x["doc_no"] for x in
                                     db.wr_children(w["doc_no"])[0] + db.wr_children(w["doc_no"])[1]))
                 for w in wrs],
                {"doc_no": "Request", "target": "Executor", "need_ar": "Requested",
                 "description": "Description", "status": "Status", "docs": "Issued Documents"}, 150)
        sr_rows = db.query("SELECT * FROM service_requests WHERE job_no=? ORDER BY doc_no", (no,))
        show_df([dict(r, service_type=db.SERVICE_TYPES.get(r["service_type"], ""),
                      status=fmt_status(r["status"])) for r in sr_rows],
                {"doc_no": "Service Request", "service_type": "Service", "description": "Description",
                 "status": "Status"}, 130)


def page_tracking():
    f = st.columns([4, 4, 3])
    dept = ROLE if ROLE in MAINT else f[0].segmented_control(
        "Executing Department", [""] + MAINT, default="", key="trk_dept",
        format_func=lambda x: "All" if not x else db.dept_en(x))
    stat = f[1].segmented_control("Status", ["", "P", "O", "C"], default="", key="trk_stat",
                                  format_func=lambda x: "All" if not x else fmt_status(x))
    if ROLE == "PLN":
        typ = f[2].segmented_control(
            "Type", ["WO", "JC", ""], default="WO", key="trk_typ_pln",
            format_func=lambda x: {"WO": "Work Orders", "JC": "Job Cards (excluded from Planning reports)",
                                   "": "All"}[x])
    else:
        typ = f[2].segmented_control("Type", ["", "WO", "JC"], default="", key="trk_typ",
                                     format_func=lambda x: "All" if not x else x)
    sql, params = "SELECT * FROM jobs WHERE 1=1", []
    for col, val in (("exec_dept", dept), ("status", stat), ("doc_type", typ)):
        if val:
            sql += f" AND {col}=?"
            params.append(val)
    jobs = db.query(sql + " ORDER BY doc_date DESC, doc_no DESC", params)
    view = [dict(j, status=fmt_status(j["status"]),
                 category=fmt_cat(j["category"] if j["cat_set"] else None),
                 exec_dept=db.dept_en(j["exec_dept"])) for j in with_equip(jobs)]
    sel, detail = record_browser(view, {"doc_no": "No.", "plan_code": "Plan Code", "exec_dept": "Department", "equip": "Equipment",
                                        "description": "Description", "category": "Type", "status": "Status",
                                        "progress": "Progress %"},
                                 f"trk_{dept}_{stat}_{typ}", title="Work Order & Job Card List",
                                 height=_h(len(view), 520), progress="Progress %")
    if sel:
        with detail:
            job = db.get_job(sel["doc_no"])
            if ROLE == "PLN":
                _job_readonly(job)
            else:
                _job_editable(job)


# ----------------------------------------------------------------------------
# Production Department (REF / OM): طلباتي ومتابعتها
# ----------------------------------------------------------------------------
FILTERS = {"All": lambda i: True,
           "Awaiting Planning": lambda i: i["kind"] == "WR" and i["step"] in (1, 2),
           "In Progress": lambda i: i["step"] in (3, 4),
           "Awaiting My Acceptance": lambda i: bool(i["awaiting"]),
           "Closed": lambda i: i["step"] == 6,
           "Rejected": lambda i: i["step"] == 0}


def _sent_reports(wr_no):
    """Inspection reports Planning sent to the requesting department."""
    for t in db.tir_sent_for_wr(wr_no):
        data = tir_form.load(t)
        recs = bool(t.get("sent_with_recs"))
        with st.expander(f"🔬 Inspection Report {t['doc_no']}", expanded=True):
            tir_form.view_ui(data)
            if recs:
                raised = db.query("SELECT doc_no, status FROM work_requests WHERE src_tir=? ORDER BY doc_no",
                                  (t["doc_no"],))
                if raised:
                    st.caption("Work Requests raised for these recommendations: "
                               + ", ".join(f"{r['doc_no']} ({db.WR_STATUS.get(r['status'], r['status'])})"
                                           for r in raised))
                if st.button("➕ Raise Work Request for the recommendations", key=f"fromtir_{t['doc_no']}"):
                    dlg_wr(from_tir=t)


def page_my_requests():
    items = db.my_items(ROLE)
    is_maint = ROLE in MAINT
    h = st.columns([6, 2, 2])
    h[0].subheader(f"Requests — {db.dept_label(ROLE)}")
    if h[1].button("➕ Work Request / Assistance", type="primary"):
        dlg_wr()
    if not is_maint and h[2].button("⚡ Job Card"):
        dlg_jc()
    labels = {k: f"{k} ({sum(1 for i in items if f(i))})" for k, f in FILTERS.items()}
    flt = st.segmented_control("View", list(FILTERS), format_func=labels.get, default="All",
                               key="my_flt", label_visibility="collapsed") or "All"
    shown = [i for i in items if FILTERS[flt](i)]
    rows = [dict(i, kind_ar="Job Card" if i["kind"] == "JC" else "Work Request",
                 equip=db.equipment_label(i["tag_no"], i["equipment_name"], i["location"]),
                 ex=ex_label(i["exec_dept"], True)) for i in shown]
    sel, detail = record_browser(rows, {"doc_no": "No.", "plan_code": "Plan Code", "kind_ar": "Type", "doc_date": "Date",
                                        "equip": "Equipment", "description": "Description", "stage": "Stage",
                                        "ex": "Executor", "progress": "Progress %"},
                                 f"my_{ROLE}_{flt}", title="My Requests",
                                 height=_h(len(rows), 480), progress="Progress %")
    if sel:
        with detail:
            stepper(sel["step"])
            st.markdown(f"**Status:** {sel['stage']}")
            st.markdown(f"**Equipment:** {sel['equip']}  \n**Executing Party:** "
                        f"{ex_label(sel['exec_dept'])}  \n**Related Documents:** "
                        f"{sel['job_no'] or '—'}" +
                        (f"  \n**Related to Job:** {sel['parent_job']}" if sel.get("parent_job") else ""))
            for jn in sel.get("awaiting", []):
                st.success(f"Work completed {jn} — Do you accept the completed work?")
                note = st.text_input("Note (required when returning to Maintenance)", key=f"acc_{jn}")
                b1, b2 = st.columns(2)
                if b1.button("✅ Confirm Acceptance", type="primary", key=f"ok_{jn}"):
                    db.accept_job(jn, ROLE, True, note or None)
                    st.rerun()
                if b2.button("↩️ Return to Maintenance", key=f"back_{jn}"):
                    try:
                        db.accept_job(jn, ROLE, False, note)
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
            if sel["kind"] == "JC":
                jc = db.get_job(sel["doc_no"])
                if jc and jc.get("close_reason"):
                    st.warning(f"Closed: {db.JC_CLOSE_REASONS.get(jc['close_reason'], '')}"
                               + (f" — {jc['close_note']}" if jc.get("close_note") else ""))
                    raised = db.query("SELECT doc_no FROM work_requests WHERE src_jc=?", (jc["doc_no"],))
                    if raised:
                        st.caption("Work Request raised: " + ", ".join(r["doc_no"] for r in raised))
                    elif st.button("➕ Raise Work Request from this Job Card", type="primary",
                                   key=f"fromjc_{jc['doc_no']}"):
                        dlg_wr(from_jc=jc)
            if sel["kind"] == "WR":
                _sent_reports(sel["doc_no"])
            show_timeline(sel["doc_no"], 200)

# ----------------------------------------------------------------------------
# التخطيط: الرئيسية + مراجعة الطلبات
# ----------------------------------------------------------------------------
def page_planning_home():
    ov = cdb("planning_overview")
    today = date.today()
    new = [w for w in ov["new_wr"] if w["status"] == "NEW"]
    c = st.columns(5)
    c[0].metric("New Work Requests", len(new))
    c[1].metric("Open Work Orders", len(ov["open_wo"]))
    c[2].metric("Open Service Requests", len(ov["open_sr"]))
    c[3].metric("Response Time (hours)",
                "—" if ov["avg_response_h"] is None else ov["avg_response_h"])
    c[4].metric("Planned Work % (PM+PD)", "—" if ov["planned_pct"] is None else f"{ov['planned_pct']}%")
    t1, t2, t3, t4 = st.tabs([
        f"📨 Requests Awaiting Review ({len(ov['new_wr'])})",
        f"📋 Open Work Orders ({len(ov['open_wo'])})",
        f"🚧 Open Service Requests ({len(ov['open_sr'])})", "📊 KPIs"])
    with t1:
        rows = with_equip([dict(w) for w in ov["new_wr"]])
        for w in rows:
            w["status"] = db.WR_STATUS.get(w["status"], w["status"])
            w["priority"] = fmt_prio(w["priority"])
            w["age"] = (today - date.fromisoformat(w["doc_date"])).days
            w["exec_dept"] = w["exec_dept"] or "—"
        show_df(rows, {"doc_no": "No.", "doc_date": "Date", "requesting_dept": "Requester",
                       "exec_dept": "Executor", "equip": "Equipment", "description": "Description",
                       "priority": "Priority", "status": "Status", "age": "Age (days)"}, 280)
        if new:
            st.warning(f"{len(new)} new request(s) — open Work Request Review to issue a Work Order or Service Request.")
    with t2:
        rows = job_rows(ov["open_wo"])
        for r in rows:
            r["exec_dept"] = db.dept_en(r["exec_dept"])
        sel, detail = record_browser(rows, {"exec_dept": "Department", **JOB_COLS, "open_sr": "Open SRs"},
                                     "home_wo", title="Open Work Order List", height=280,
                                     progress="Progress %")
        if sel:
            with detail:
                job = db.get_job(sel["doc_no"])
                _job_readonly(job)
    with t3:
        rows = with_equip([dict(s) for s in ov["open_sr"]])
        for s in rows:
            s["service_type"] = db.SERVICE_TYPES.get(s["service_type"], s["service_type"])
            s["status"] = fmt_status(s["status"])
        show_df(rows, {"doc_no": "No.", "doc_date": "Date", "service_type": "Service",
                       "executor": "Executing Party",
                       "job_no": "Work Order", "wr_no": "Work Request", "equip": "Equipment",
                       "description": "Description", "from_date": "From", "to_date": "To",
                       "status": "Status"}, 280)
    with t4:
        st.caption("General Planning reports include Work Orders only; Job Cards "
                   "go directly to Maintenance and are excluded.")
        m1, m2 = st.columns(2)
        m1.metric("Estimated Backlog Hours in Open Work Orders", f"{ov['backlog_hours']:g}")
        m2.metric("Open Work Orders Older than 14 Days", ov["aged"])
        if ov["open_wo"]:
            df = pd.DataFrame(ov["open_wo"])
            df["Department"] = df["exec_dept"].map(db.dept_en)
            df["Stage"] = df["stage"]
            st.bar_chart(df.pivot_table(index="Department", columns="Stage", values="doc_no",
                                        aggfunc="count", fill_value=0), height=230)


REVIEW_VIEWS = {"pending": ("Pending Review", ("NEW", "STUDY")),
                "follow": ("Converted — Follow-up", ("CONVERTED", "SERVICE")),
                "all": ("All", ("NEW", "STUDY", "CONVERTED", "SERVICE", "REJECTED"))}


def page_wr_review():
    st.markdown("## Work Request Review")
    st.caption("Browse requests by status, then select a request to view its details and available actions.")
    cnt = {k: db.query("SELECT COUNT(*) n FROM work_requests WHERE status IN (%s)" %
                       ",".join("?" * len(v[1])), v[1])[0]["n"] for k, v in REVIEW_VIEWS.items()}

    # مؤشرات سريعة تساعد المخطط على معرفة حجم العمل قبل فتح القائمة.
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Pending Review", db.query(
        "SELECT COUNT(*) n FROM work_requests WHERE status='NEW'")[0]["n"])
    m2.metric("Under Review", db.query(
        "SELECT COUNT(*) n FROM work_requests WHERE status='STUDY'")[0]["n"])
    m3.metric("Converted / Follow-up", cnt["follow"])
    m4.metric("Total Requests", db.query(
        "SELECT COUNT(*) n FROM work_requests")[0]["n"])

    view = st.segmented_control("Request Status", list(REVIEW_VIEWS), default="pending", key="rev_view",
                                format_func=lambda k: f"{REVIEW_VIEWS[k][0]} ({cnt[k]})") or "pending"
    sts = REVIEW_VIEWS[view][1]
    wrs = db.query("SELECT * FROM work_requests WHERE status IN (%s) ORDER BY doc_date DESC,"
                   " doc_no DESC" % ",".join("?" * len(sts)), sts)
    if not wrs:
        st.success("No requests in this view.")
        return

    rows = with_equip([dict(w, req=db.dept_en(w["requesting_dept"]),
                            src="Maintenance" if w["requesting_dept"] in MAINT else "Production",
                            ex=ex_label(w["exec_dept"], True),
                            need_ar=NEED_LABELS.get(w["need"], w["need"] or ""),
                            prio=fmt_prio(w["priority"]),
                            st_ar=db.WR_STATUS.get(w["status"], w["status"])) for w in wrs])
    rename = {"doc_no": "No.", "plan_code": "Plan Code", "doc_date": "Date", "req": "Requester",
              "src": "Source", "ex": "Executor", "need_ar": "Requested", "equip": "Equipment",
              "description": "Description", "prio": "Priority", "st_ar": "Status"}

    # عند طلب العودة إلى عرض الجدول , نمسح اختيار الصف قبل إنشاء الجدول مجدداً.
    if st.session_state.pop("_rev_force_list", False):
        for state_key in list(st.session_state.keys()):
            if str(state_key).startswith("rev_pick_"):
                del st.session_state[state_key]

    filtered = filter_bar(rows, rename)
    if not filtered:
        return

    # تُحفظ حالة اختيار الصف بواسطة Streamlit؛ وجود اختيار سابق يفعّل عرض التفاصيل.
    table_key = f"rev_pick_{view}_{len(filtered)}"
    selection_state = st.session_state.get(table_key, {})
    selected_indexes = []
    try:
        selected_indexes = list(selection_state["selection"]["rows"])
    except (KeyError, TypeError, AttributeError):
        try:
            selected_indexes = list(selection_state.selection.rows)
        except (AttributeError, TypeError):
            selected_indexes = []
    selected = filtered[selected_indexes[0]] if selected_indexes and selected_indexes[0] < len(filtered) else None

    def render_request_table():
        df = pd.DataFrame(filtered)
        df = _show_blank(df[[c for c in rename if c in df.columns]].rename(columns=rename))
        st.dataframe(df, hide_index=True, width="stretch", height=_h(len(df), 500),
                     on_select="rerun", selection_mode="single-row", key=table_key)

    if selected is None:
        # العرض الأولي: الجدول بكامل عرض الصفحة.
        with st.container(border=True):
            st.markdown("### Work Request List")
            st.caption(f"Requests in current view: {len(filtered)} — Select a row to view details and actions.")
            render_request_table()
        return

    # بعد اختيار طلب: تصغير الجدول وإظهار التفاصيل بجانبه.
    left, right = st.columns([4, 6], gap="medium")
    with left:
        with st.container(border=True):
            st.markdown("### Work Request List")
            st.caption(f"Requests: {len(filtered)} — Select another row to change the details.")
            render_request_table()
    with right:
        with st.container(border=True):
            head, close = st.columns([5, 2])
            head.markdown("### Request Details & Actions")
            if close.button("↩ Hide Details", key="rev_hide_details", use_container_width=True):
                st.session_state["_rev_force_list"] = True
                st.rerun()
            w = selected
            st.markdown(f"**Request No.:** {ltr(w['doc_no'])}　·　**Status:** {db.WR_STATUS.get(w['status'], w['status'])}")
            info = db.my_items(w["requesting_dept"], only=w["doc_no"])
            stepper(info[0]["step"] if info else db.wr_stage(w)[0])
            st.markdown("#### Request Information")
            st.markdown(f"**Requesting Department:** {db.dept_label(w['requesting_dept'])}  \n"
                        f"**Requested Executing Department:** {ex_label(w['exec_dept'])}  \n"
                        f"**Requested Item:** {NEED_LABELS.get(w['need'], w['need'] or '')}  \n"
                        f"**Priority:** {fmt_prio(w['priority'])}  \n"
                        f"**Equipment:** {w['equip']}" +
                        (f"  \n**Contact:** {w['contact']}" if w["contact"] else "") +
                        (f"  \n**Cost Center:** {w['cost_center']}" if w.get("cost_center") else "") +
                        (f"  \n**Materials:** {w['materials']}" if w.get("materials") else "") +
                        (f"  \n**Special equipment:** {w['special_equip']}" if w.get("special_equip") else ""))
            if w["parent_job"]:
                st.warning(f"Request from Maintenance Department during execution of Work Order {w['parent_job']}")
            if w.get("src_tir"):
                st.info(f"Raised by the requesting department following Inspection Report {w['src_tir']}")
            if w.get("src_jc"):
                st.info(f"Raised from Job Card {w['src_jc']}, which was closed because it needed Planning")
            st.markdown("#### Work Description")
            st.info(w["description"] or "No description recorded.")
            pinfo = db.plan_wr_info(w["doc_no"]) if w.get("plan_ref") else None
            if pinfo:
                with st.expander("Annual Plan Link Details", expanded=False):
                    st.success(
                        f"🗓️ Item Code **{pinfo['code'] or '—'}** · "
                        f"{pinfo['eq_type'] or 'Equipment'} · "
                        f"{db.PLAN_TYPES[pinfo['plan_type']].split(' (')[0]} · "
                        f"{db.MONTHS_EN[pinfo['month'] - 1]} {pinfo['year']}  \n"
                        f"**Equipment:** {', '.join(pinfo['tags'])}  \n"
                        f"**Requested Executing Departments:** "
                        f"{' + '.join(db.plan_dept_en(x) for x in pinfo['depts'])}"
                        + (f"  \n**Plan Notes:** {pinfo['notes']}" if pinfo["notes"] else ""))
            st.markdown("#### Planning Actions")
            w_closed = db.is_wr_closed(w["doc_no"])
            if w["status"] == "REJECTED":
                st.error(f"⛔ Work Request rejected: {w.get('reject_reason') or ''} — closed and cannot be modified.")
            elif w_closed:
                st.success("🔒 Work Request is fully completed, accepted, and closed — it cannot be modified or receive additional documents.")
            else:
                done_st = w["status"] in ("CONVERTED", "SERVICE")
                b = st.columns(3)
                nd = w["need"] or ""
                if b[0].button("📋 Additional Work Order" if done_st else "📋 Issue Work Order",
                               type="primary" if nd in ("", "WO", "BOTH") else "secondary",
                               key=f"mkwo_{w['doc_no']}", use_container_width=True):
                    dlg_wo(w)
                if b[1].button("🚧 Additional Service" if done_st else "🚧 Issue Service Request",
                               type="primary" if nd in ("SR", "BOTH") else "secondary",
                               key=f"mksr_{w['doc_no']}", use_container_width=True):
                    dlg_sr(wr=w)
                if b[2].button("🔬 Additional Inspection" if db.tir_children(w["doc_no"]) else "🔬 Inspection Request",
                               type="secondary", key=f"mktir_{w['doc_no']}", use_container_width=True):
                    dlg_tir(w)
                if w["status"] in ("NEW", "STUDY"):
                    b2 = st.columns(2)
                    if w["status"] == "NEW" and b2[0].button("🔎 Start Review", key=f"study_{w['doc_no']}",
                                                               use_container_width=True):
                        db.wr_review(w["doc_no"], "STUDY", ROLE)
                        st.rerun()
                    if b2[1].button("⛔ Reject Request", key=f"rej_{w['doc_no']}",
                                    use_container_width=True):
                        dlg_reject(w["doc_no"])
            jobs, srs = db.wr_children(w["doc_no"])
            if jobs or srs:
                with st.expander("Documents Issued for this Request", expanded=True):
                    show_df([{"No.": j["doc_no"], "Department": db.dept_en(j["exec_dept"]),
                              "Status": fmt_status(j["status"]), "Progress %": j["progress"] or 0}
                             for j in jobs] +
                            [{"No.": s_["doc_no"], "Department": db.SERVICE_TYPES.get(s_["service_type"], ""),
                              "Status": fmt_status(s_["status"]), "Progress %": 100 if s_["status"] == "C" else 0}
                             for s_ in srs], height=_h(len(jobs) + len(srs), 150))
            tirs = db.tir_children(w["doc_no"])
            if tirs:
                with st.expander("Related Technical Inspection Requests", expanded=False):
                    show_df([{"No.": t["doc_no"], "Scope": db.TIR_SCOPE.get(t.get("scope", ""), "—"),
                              "Status": t["status_ar"],
                              "Recommendations": (t.get("recommendations") or "—")[:60]}
                             for t in tirs], height=_h(len(tirs), 120))
            with st.expander("Request Timeline", expanded=False):
                show_timeline(w["doc_no"], 180)
            # سجل الأعمال السابقة يبقى في نهاية تفاصيل الطلب ومغلقاً افتراضياً.
            if w["tag_no"]:
                hist = [h for h in db.equipment_history(w["tag_no"])
                        if h["doc_no"] != w["doc_no"]][:5]
                if hist:
                    with st.expander(f"Previous Work on Same Equipment ({len(hist)})", expanded=False):
                        show_df([{"Document No.": x["doc_no"], "Status": x["status_ar"]}
                                 for x in hist], height=180)


# ----------------------------------------------------------------------------
# الصيانة (M / E / I): Work Orders + Today's Hours
# ----------------------------------------------------------------------------
def page_maint_home():
    g = db.maint_overview(ROLE)
    t1, t2 = st.tabs(["📋 Work Orders", "⏱️ Daily Hours Entry"])
    with t1:
        names = {"all": "All", "new": "New (Awaiting Inspection)", "ready": "Ready for Execution",
                 "waiting_sr": "Awaiting Service", "in_progress": "In Progress",
                 "awaiting": "Awaiting Requester Acceptance"}
        allj = [j for k in ("new", "ready", "waiting_sr", "in_progress", "awaiting") for j in g[k]]
        labels = {k: f"{n} ({len(allj) if k == 'all' else len(g[k])})" for k, n in names.items()}
        flt = st.segmented_control("View", list(names), format_func=labels.get,
                                   default="new" if g["new"] else "all", key="mh_flt",
                                   label_visibility="collapsed") or "all"
        jobs = allj if flt == "all" else g[flt]
        if not allj:
            st.success("No open Work Orders.")
            return
        rows = job_rows(jobs)
        sel, detail = record_browser(rows, {"doc_no": "No.", "doc_type": "Type", "equip": "Equipment",
                                            "description": "Description", "category": "Maintenance",
                                            "stage": "Stage", "progress": "Progress %", "age": "Age"},
                                     f"mh_{flt}", title="Work Order List",
                                     height=_h(len(rows), 430), progress="Progress %")
        if sel:
            with detail:
                job = db.get_job(sel["doc_no"])
                st.markdown(f"**{job['doc_no']}** — {sel['equip']}")
                st.info(job["description"])
                st.caption(f"Stage: {sel['stage']}")
                q = st.columns(2)
                if job["doc_type"] == "WO" and not job["stock_checked"]:
                    if q[0].button("✅ Inspection and stock check completed", key=f"q_ins_{job['doc_no']}"):
                        db.set_stock_checked(job["doc_no"], True, ROLE)
                        st.rerun()
                if job["status"] == "P" and q[1].button("▶️ Start Execution", type="primary",
                                                        key=f"q_start_{job['doc_no']}"):
                    try:
                        db.set_job_status(job["doc_no"], "O", 0, actor=ROLE)
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                _jc_close_ui(job)
                st.caption("To update progress, hours, or spare parts, open Work Order Tracking.")
    with t2:
        page_hours(embedded=True)


# ----------------------------------------------------------------------------
MP_LABELS = {"approved": "Approved", "supervisors": "Available Supervisors",
             "technicians": "Available Technicians", "leave_n": "Leave", "absent": "Absent",
             "sick": "Sick", "in_train": "Internal Training", "ex_train": "External Training",
             "shift_rest": "Shift Rest", "other": "Other", "contract": "Contracts"}


def _num(x):
    return 0.0 if x is None or pd.isna(x) else float(x)


def page_hours(embedded=False):
    if not embedded:
        st.header("Daily Work Hours Entry")
    dept = ROLE if ROLE in MAINT else seg("Department", MAINT, "hrs_dept", db.dept_en)
    # Daily entry is always recorded against the server date; users cannot select another date.
    d = date.today()
    st.caption(f"Work Date: {d.isoformat()} (set automatically)")

    approved_technicians = db.get_department_approved(dept)
    with st.expander("Manpower (Headcount)", expanded=False):
        mp = db.get_manpower(d, dept)
        st.caption(f"Approved Technicians (administrator-controlled): {approved_technicians}")
        with st.form(f"mp_{dept}_{d}"):
            cols = st.columns(6)
            daily_fields = [k for k in db.MANPOWER_FIELDS if k != "approved"]
            vals = {k: cols[i % 6].number_input(MP_LABELS[k], min_value=0, step=1,
                                                value=int(mp.get(k, 0) or 0))
                    for i, k in enumerate(daily_fields)}
            if st.form_submit_button("Save Manpower"):
                try:
                    db.save_manpower(d, dept, **vals)
                    st.success("Saved")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    capacity = db.get_daily_labor_capacity(d, dept)
    if capacity["has_manpower"]:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Technicians Present", capacity["technicians"])
        m2.metric("Regular Hours Capacity", f'{capacity["capacity"]:g} h')
        m3.metric("Regular TECH Hours Used", f'{capacity["used"]:g} h')
        m4.metric("Remaining Capacity", f'{capacity["remaining"]:g} h')
        if capacity["technicians"] < approved_technicians:
            shortage = approved_technicians - capacity["technicians"]
            st.warning(
                f"Available technicians ({capacity['technicians']}) are below the "
                f"approved strength ({approved_technicians}) by {shortage} technician(s)."
            )
    else:
        st.warning("Daily manpower has not been entered. Save technician attendance before recording regular TECH hours.")

    rows = db.daily_report(dept, d)
    st.subheader(f"Jobs — {db.dept_en(dept)} on {d.isoformat()}")
    if not rows:
        st.info("No pending or in-progress jobs for this department on this date. "
                "Issue a Job Card or Work Order first.")
        return
    st.caption("Edit the cells directly in the table, then click Save. You can also change job status and progress.")

    code_of = {v: k for k, v in db.STATUS.items()}
    hcols = ("Workers", "SPV", "TECH", "OV.T", "CONT")
    base = pd.DataFrame([{
        "No.": r["doc_no"], "Equipment": r["equip"], "Description": r["description"],
        "Status": fmt_status(r["status"]), "Progress %": int(r["progress"] or 0),
        "Workers": int(r["workers"] or 0), "SPV": float(r["spv"] or 0),
        "TECH": float(r["tech"] or 0), "OV.T": float(r["ot"] or 0),
        "CONT": float(r["cont"] or 0), "Remarks": r["remarks"] or ""} for r in rows])
    hrs = st.column_config.NumberColumn(min_value=0.0, step=0.5, format="%.1f")
    edited = st.data_editor(
        base, hide_index=True, width="stretch", key=f"hrs_{dept}_{d}_{st.session_state.get('hrs_v', 0)}",
        disabled=["No.", "Equipment", "Description"],
        column_config={
            "Status": st.column_config.SelectboxColumn(options=list(db.STATUS.values()),
                                                      required=True),
            "Progress %": st.column_config.NumberColumn(min_value=0, max_value=100, step=5),
            "Workers": st.column_config.NumberColumn(min_value=0, step=1),
            "SPV": hrs, "TECH": hrs, "OV.T": hrs, "CONT": hrs,
            "Remarks": st.column_config.TextColumn()})
    # Daily labor summary by workforce category; keep supervisors and overtime separate.
    spv_total = sum(_num(edited["SPV"].sum()),)
    tech_total = sum(_num(edited["TECH"].sum()),)
    ot_total = sum(_num(edited["OV.T"].sum()),)
    cont_total = sum(_num(edited["CONT"].sum()),)
    total = spv_total + tech_total + ot_total + cont_total
    summary = st.columns(5)
    summary[0].metric("Supervisor Hours", f"{spv_total:g} h")
    summary[1].metric("Technician Hours", f"{tech_total:g} h")
    summary[2].metric("Overtime Hours", f"{ot_total:g} h")
    summary[3].metric("Contractor Hours", f"{cont_total:g} h")
    summary[4].metric("Total Hours", f"{total:g} h")
    st.caption(f"Number of jobs in this report: {len(edited)}")

    if st.button("Save Daily Hours", type="primary", key=f"save_{dept}_{d}"):
        saved, errors, warns = 0, [], []
        for i in range(len(edited)):
            new, old = edited.iloc[i], base.iloc[i]
            job = new["No."]
            try:
                hours = [_num(new[c]) for c in hcols]
                if hours != [_num(old[c]) for c in hcols]:
                    db.set_daily_labor(job, d, int(hours[0]), *hours[1:])
                    saved += 1
                    if sum(hours[1:]) > 0 and new["Status"] == db.STATUS["P"]:
                        warns.append(f"{job}: Hours were recorded for a job that is still Pending")
                if (new["Status"] != old["Status"]
                        or int(_num(new["Progress %"])) != int(old["Progress %"])
                        or (new["Remarks"] or "") != (old["Remarks"] or "")):
                    db.set_job_status(job, code_of[new["Status"]],
                                      int(_num(new["Progress %"])),
                                      new["Remarks"] or None, d, actor=ROLE)
                    saved += 1
            except ValueError as e:
                errors.append(f"{job}: {e}")
        for e in errors:
            st.error(e)
        for w in warns:
            st.warning(w)
        if saved:
            # تصفير حالة الجدول حتى يُعاد بناؤه من القاعدة بعد الحفظ
            st.session_state["hrs_v"] = st.session_state.get("hrs_v", 0) + 1
        if saved and not errors and not warns:
            st.success("Saved")
            st.rerun()
        elif saved and not errors:
            st.success("Saved")
        elif not saved and not errors:
            st.info("No changes to save.")


# ----------------------------------------------------------------------------
# طلب الخدمة SR
# ----------------------------------------------------------------------------
def page_sr():
    st.subheader("Service Requests (SR)")
    st.caption("Service Requests are issued from Work Request Review after the Work Request has been reviewed. "
               "Track their status and record daily work hours and consumed materials here.")
    flt = st.segmented_control("View", ["Open", "All"], default="Open", key="sr_flt",
                               label_visibility="collapsed") or "Open"
    rows = with_equip(db.query(
        "SELECT * FROM service_requests" + (" WHERE status!='C'" if flt == "Open" else "")
        + " ORDER BY doc_date DESC, doc_no DESC"))
    for r in rows:
        r["service_ar"] = db.SERVICE_TYPES.get(r["service_type"], r["service_type"])
        r["status_ar"] = fmt_status(r["status"])
    sel, detail = record_browser(rows, {"doc_no": "No.", "plan_code": "Plan Code", "service_ar": "Service",
                                        "executor": "Executing Party", "status_ar": "Status",
                                        "doc_date": "Date", "wr_no": "Work Request", "job_no": "Work Order",
                                        "equip": "Equipment", "description": "Description"},
                                 f"sr_{flt}", title="Service Request List", height=_h(len(rows), 480))
    if sel:
        with detail:
            no, closed = sel["doc_no"], sel["status"] == "C"
            st.markdown(f"**{no}** — {sel['service_ar']}")
            st.markdown(f"**Executing Party:** {sel['executor'] or '—'} · "
                        f"**Requester:** {db.dept_en(sel['requesting_dept']) or '—'} · "
                        f"**Equipment:** {sel['equip'] or '—'}")
            st.info(sel["description"])
            st.caption(f"From {ltr(sel['from_date'] or '—')} to {ltr(sel['to_date'] or '—')}")
            if closed:
                st.success("🔒 Service Request is complete and closed — it cannot be modified.")
            t1, t2, t3, t4 = st.tabs(["Status", "Work Hours", "Inventory & Materials", "Timeline"])
            with t1:
                if not closed:
                    ns = seg("Status", list(db.STATUS), f"srs_{no}", fmt_status, default=sel["status"])
                    if st.button("Update Status", type="primary", key=f"srb_{no}"):
                        try:
                            db.set_sr_status(no, ns, actor=ROLE)
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
                else:
                    st.caption(f"Status: {fmt_status(sel['status'])}")
            with t2:
                if not closed:
                    st.caption("Recording hours for the same date replaces the previous entry.")
                    with st.form(f"srh_{no}", border=False):
                        a_, b_, c_ = st.columns(3)
                        wd = a_.date_input("Work Date", date.today())
                        wk = b_.number_input("Number of Workers", min_value=0, step=1)
                        hr_ = c_.number_input("Total Hours", min_value=0.0, step=0.5)
                        note = st.text_input("Note")
                        if st.form_submit_button("Save Daily Hours", type="primary"):
                            try:
                                db.set_sr_daily_hours(no, wd, wk, hr_, note or None)
                                st.session_state["_flash"] = "Service hours recorded"
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                hrs_rows = db.query("SELECT work_date, workers, hours, note FROM sr_labor_log"
                                    " WHERE sr_no=? ORDER BY work_date DESC", (no,))
                if hrs_rows:
                    st.metric("Total Hours", f"{sum(r_['hours'] or 0 for r_ in hrs_rows):g}")
                show_df(hrs_rows, {"work_date": "Date", "workers": "Workers",
                                   "hours": "Hours", "note": "Note"}, 170)
            with t3:
                if not closed:
                    with st.form(f"srm_{no}", border=False):
                        a_, b_, c_ = st.columns([1, 3, 1])
                        stock = a_.text_input("Stock No.")
                        desc = b_.text_input("Description *")
                        unit = c_.text_input("Unit")
                        d_, e_ = st.columns(2)
                        q = d_.number_input("Quantity", min_value=0.0, step=1.0)
                        rt = e_.number_input("Returned", min_value=0.0, step=1.0)
                        if st.form_submit_button("Add Material", type="primary"):
                            try:
                                db.add_sr_material(no, stock, desc, unit, q, rt)
                                st.session_state["_flash"] = "Material added"
                                st.rerun()
                            except ValueError as e:
                                st.error(str(e))
                show_df(db.query("SELECT * FROM sr_materials WHERE sr_no=?", (no,)),
                        {"stock_no": "Stock No.", "description": "Description", "unit": "Unit",
                         "qty": "Quantity", "returned": "Returned"}, 170)
            with t4:
                show_timeline(no, 190)

def page_daily():
    st.header("Daily Report")
    c1, c2 = st.columns(2)
    dept = ROLE if ROLE in MAINT else seg("Department", MAINT, "dly_dept", db.dept_en)
    d = c2.date_input("Report Date", date.today())

    mp = db.get_manpower(d, dept)
    st.subheader("Manpower (Headcount)")
    st.dataframe(pd.DataFrame([{MP_LABELS[k]: mp.get(k, 0) for k in db.MANPOWER_FIELDS}]),
                 hide_index=True, width="stretch")
    st.caption("Manpower and work hours are entered on the Daily Work Hours page.")

    rows = db.daily_report(dept, d)
    st.subheader(f"Jobs — {db.dept_en(dept)} on {d.isoformat()}")
    view = [dict(r, cat_pm="*" if r["category"] == "PM" else "",
                 cat_cm="*" if r["category"] == "CM" else "",
                 cat_pd="*" if r["category"] == "PD" else "") for r in rows]
    show_df(view, {"doc_no": "W.O & J.C", "equip": "Equipment / Tag", "description": "Description",
                   "status": "Status", "start_date": "Started", "end_date": "End Date",
                   "progress": "Progress %", "spv": "SPV", "tech": "TECH", "ot": "OV.T",
                   "cont": "CONT", "cat_pm": "PM", "cat_cm": "CM", "cat_pd": "PD",
                   "remarks": "Remarks"})
    spv_total = sum((r["spv"] or 0) for r in rows)
    tech_total = sum((r["tech"] or 0) for r in rows)
    ot_total = sum((r["ot"] or 0) for r in rows)
    cont_total = sum((r["cont"] or 0) for r in rows)
    total = spv_total + tech_total + ot_total + cont_total
    st.subheader("Consumed Man-Hours Breakdown")
    summary = st.columns(5)
    summary[0].metric("Supervisor Hours", f"{spv_total:g} h")
    summary[1].metric("Technician Hours", f"{tech_total:g} h")
    summary[2].metric("Overtime Hours", f"{ot_total:g} h")
    summary[3].metric("Contractor Hours", f"{cont_total:g} h")
    summary[4].metric("Total Hours", f"{total:g} h")
    xlsx = report.build_daily_xlsx(dept, d, rows, mp)
    st.download_button("Download Report (Excel)", xlsx,
                       file_name=f"Daily_Report_{db.dept_en(dept)}_{d.isoformat()}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                       type="primary")


# ----------------------------------------------------------------------------
# Master Data
# ----------------------------------------------------------------------------
def page_admin_control():
    st.header("Admin Control Panel")
    token = st.session_state.get("admin_token")
    if token and not db.admin_session_valid(token):
        st.session_state.pop("admin_token", None)
        token = None
    if not token:
        st.caption("Administrator access is separate from the department role selector.")
        with st.form("admin_login_form"):
            username = st.text_input("Administrator username", value="admin")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary")
        if submitted:
            token = db.admin_login(username, password)
            if token:
                st.session_state["admin_token"] = token
                st.rerun()
            else:
                st.error("Invalid administrator username or password.")
        return

    head = st.columns([7, 2])
    head[0].success("Administrator session is active.")
    if head[1].button("Sign out", key="admin_signout"):
        db.admin_logout(token)
        st.session_state.pop("admin_token", None)
        st.rerun()

    st.subheader("Approved Technician Headcount")
    st.caption("Set the approved technician establishment for each maintenance department. Daily department users cannot change this value.")
    dept = st.selectbox("Maintenance Department", MAINT, format_func=db.dept_en, key="admin_approved_dept")
    current = db.get_department_approved(dept)
    with st.form("admin_approved_form"):
        approved = st.number_input("Approved Technicians", min_value=0, step=1, value=current)
        save = st.form_submit_button("Save Approved Headcount", type="primary")
    if save:
        try:
            db.set_department_approved(dept, approved, token)
            done(f"Approved headcount for {db.dept_en(dept)} saved")
        except (ValueError, PermissionError) as e:
            st.error(str(e))

    st.subheader("Administrative Audit Log")
    audit = db.admin_audit_log()
    show_df(audit, {"created_at": "Date / Time", "username": "Administrator",
                    "action": "Action", "dept": "Department",
                    "old_value": "Previous Value", "new_value": "New Value"}, _h(len(audit), 300))


def page_master():
    h = st.columns([8, 2])
    h[0].subheader("Master Data")
    if h[1].button("➕ Add Equipment", type="primary"):
        dlg_equipment()
    t1, t2, t3, t4 = st.tabs(["Equipment", "Approved Departments", "Executing Parties (Service Requests)",
                              "Report Settings"])
    with t1:
        eqs = db.list_equipment()
        for e in eqs:
            e["cls_ar"] = db.EQ_CLASSES.get(e["eq_class"], e["eq_class"]) if e["eq_class"] else ""
            e["dept_ar"] = db.dept_en(e["maint_dept"]) if e["maint_dept"] else ""
        sel, detail = record_browser(eqs, {"tag_no": "Tag No.", "name": "Name", "location": "Location",
                                           "cost_center": "Cost Center", "cls_ar": "Equipment Type",
                                           "dept_ar": "Maintenance Department"},
                                     "eq_tbl", title="Equipment List", height=340)
        if sel and sel["tag_no"]:
            with detail:
                hist = db.equipment_history(sel["tag_no"])
                st.markdown(f"**Work History {sel['tag_no']}** — Includes plan items that grouped it with other equipment")
                if hist:
                    show_df([dict(h, via="From Plan Item" if h["via_plan"] else "") for h in hist],
                            {"kind": "Type", "doc_no": "No.", "doc_date": "Date",
                             "status_ar": "Status", "plan_code": "Plan Code", "via": "Note"},
                            _h(len(hist), 240))
                else:
                    st.caption("No documents recorded for this equipment yet.")
    with t2:
        show_df([{"Code": c, "Name": en, "Name": ar, "Type": k}
                 for c, en, ar, k in db.DEPARTMENTS])
    with t3:
        st.caption("List of parties assigned Service Requests. New parties are automatically added to the end of the "
                   "list when the first Service Request is assigned to them.")
        use = db.executor_usage()
        show_df(use, {"id": "No.", "name": "Executing Party", "total": "Service Requests",
                      "open": "Open"}, _h(len(use), 360))
    with t4:
        st.caption("Targets and limits used by the monthly reports and the Job Card automatic closure time.")
        with st.form("settings_form", border=False):
            cur = {x["key"]: x for x in db.settings_all()}
            vals = {k: st.text_input(x["label"], value=str(x["value"]), key=f"set_{k}") for k, x in cur.items()}
            if st.form_submit_button("Save Settings", type="primary"):
                errs, n = [], 0
                for k, v in vals.items():
                    if v.strip() != str(cur[k]["value"]):
                        try:
                            db.set_setting(k, v, ROLE)
                            n += 1
                        except ValueError as e:
                            errs.append(f"{cur[k]['label']}: {e}")
                for e in errs:
                    st.error(e)
                if n and not errs:
                    done(f"{n} setting(s) saved")


# ----------------------------------------------------------------------------
# Work Groups: Link الطلبات والأوامر وحساب Overall Progress
# ----------------------------------------------------------------------------
KIND_LABELS = {"WG": "Work Group", "WR": "Work Request", "WO": "Work Order", "SR": "Service Request",
           "TIR": "Inspection Request"}


def _wr_label(w):
    """Work Request status: before conversion it reflects the request itself; after conversion it reflects all issued documents."""
    if w["wr_status"] in ("NEW", "STUDY", "REJECTED"):
        return db.WR_STATUS.get(w["wr_status"], w["wr_status"])
    return fmt_status(w["status"])


def hier_rows(works, hours=True):
    """Hierarchical table rows: Work Group ← Work Request(s) ← (Work Order / Service Request / Inspection Request)."""
    flat = []

    def row(pad, kind, key, desc, equip, party, status, prog, mh, st_, en_):
        r = {"No.": pad + key, "Type": KIND_LABELS[kind], "Description": desc, "Equipment": equip or "",
             "Department": party, "Status": status, "Progress %": prog,
             "Started": st_ or "—", "End Date": en_ or "—"}
        if hours:
            r["Today's Hours"] = mh
        flat.append(r)

    def wr_rows(w, wpad, cpad):
        party = db.dept_en(w["requesting_dept"]) + (
            f" → {db.dept_en(w['exec_dept'])}" if w["exec_dept"] else "")
        row(wpad, "WR", w["key"], w["title"], w["equip"], party, _wr_label(w),
            w["progress"], w["mh_today"], w["start_date"], w["end_date"])
        for c in w["children"]:
            row(cpad, c["kind"], c["doc_no"], c["description"], "", c["party"],
                fmt_status(c["status"]), c["progress"], c["mh_today"],
                c["start_date"], c["end_date"])

    for w in works:
        if w["kind"] == "WG":
            row("", "WG", w["key"], w["title"], w["equip"],
                db.dept_en(w["requesting_dept"]) if w["requesting_dept"] else "",
                fmt_status(w["status"]), w["progress"], w["mh_today"],
                w["start_date"], w["end_date"])
            for x in w["wrs"]:
                wr_rows(x, "   └ ", "        └ ")
        else:
            wr_rows(w, "", "   └ ")
    return flat


def page_groups():
    h = st.columns([8, 2])
    h[0].subheader("Work Groups (Link Work Requests)")
    if h[1].button("➕ Work Group", type="primary"):
        dlg_group()
    st.caption("A Work Group combines Work Requests related to the same job. Its Work Orders and "
               "Service Requests and Inspections appear under it. Its status and progress are tracked independently: completing one order does not mean "
               "the overall job is complete while other documents remain open.")
    show_closed = st.toggle("Show Closed", key="wg_closed")
    groups = cdb("list_groups", show_closed)
    if not groups:
        st.info("No Work Groups found.")
        return
    rows = [dict(g, depts_s=" + ".join(map(db.dept_en, g["depts"])) or "—",
                 st_ar=fmt_status(g["status"]), n_wr=len(g["wrs"]), n_docs=len(g["docs"]))
            for g in groups]
    sel, detail = record_browser(rows, {"group_no": "No.", "title": "Work", "st_ar": "Status",
                                        "progress": "Overall Progress %", "n_wr": "Requests",
                                        "n_docs": "Documents", "depts_s": "Departments"},
                                 f"wg_{show_closed}", title="Work Group List",
                                 height=_h(len(rows), 440), progress="Overall Progress %")
    if sel:
        with detail:
            st.markdown(f"**{sel['group_no']}** — {sel['title']}")
            st.progress(sel["progress"] / 100,
                        text=f"Overall Progress {sel['progress']}%")
            if sel["wrs"]:
                show_df(hier_rows(sel["wrs"], hours=False) and
                        [dict(r) for r in hier_rows([dict(x, kind="WR") for x in sel["wrs"]],
                                                    hours=False)],
                        None, _h(len(sel["docs"]) + len(sel["wrs"]), 320))
            else:
                st.info("No Work Requests linked yet — click Link Work Request.")
            closed = sel["status"] == "C"
            st.markdown(f"**Work Group Status:** {fmt_status(sel['status'])}. This status is calculated automatically from linked jobs "
                        "and is complete only when all linked documents are complete.")
            if not closed:
                if st.button("🔗 Link Work Request", type="primary", key=f"lnk_{sel['group_no']}"):
                    dlg_link(sel["group_no"])
            else:
                st.success("🔒 Work Group complete — all its documents are complete.")
            show_timeline(sel["group_no"], 130)

def page_planning_daily():
    h = st.columns([3, 3, 3])
    d = h[0].date_input("Report Date", date.today())
    works = cdb("planning_daily_report", d)
    xlsx = report.build_planning_daily_xlsx(d, works)
    h[2].write("")
    h[2].download_button("⬇️ Download Report (Excel)", xlsx,
                         file_name=f"Planning_Daily_{d.isoformat()}.xlsx",
                         mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         type="primary")
    st.caption("Work Group (if any) ← Work Request ← Work Orders, Service Requests, and Inspections. "
               "The Work Group has its own status and progress; progress is weighted by estimated duration.")
    if not works:
        st.info("No open jobs on this date.")
        return
    q = st.text_input("🔍 Search", key="pd_q",
                      placeholder="Search Work Group, Work Request, Work Order, Service Request, Inspection, description, or equipment...")
    terms = (q or "").lower().split()
    if terms:
        def _txt(w):
            parts = [w["key"], w["title"], w.get("equip") or ""]
            for c in w.get("children", []):
                parts += [c["doc_no"], c["description"], c["party"]]
            for x in w.get("wrs", []):
                parts.append(_txt(x))
            return " ".join(map(str, parts)).lower()
        works = [w for w in works if all(t in _txt(w) for t in terms)]
        st.caption(f"Showing {len(works)} top-level items")
        if not works:
            st.info("No matching results.")
            return
    wr_all = [x for w in works for x in (w["wrs"] if w["kind"] == "WG" else [w])]
    docs = [c for w in wr_all for c in w["children"]]
    m = st.columns(6)
    m[0].metric("Work Groups", sum(1 for w in works if w["kind"] == "WG"))
    m[1].metric("Work Requests", len(wr_all))
    m[2].metric("Work Orders", sum(1 for c in docs if c["kind"] == "WO"))
    m[3].metric("Service Requests", sum(1 for c in docs if c["kind"] == "SR"))
    m[4].metric("Inspection Requests", sum(1 for c in docs if c["kind"] == "TIR"))
    m[5].metric("Today's Hours", f"{sum(c['mh_today'] for c in docs):g}")
    flat = hier_rows(works)
    st.dataframe(pd.DataFrame(flat), hide_index=True, width="stretch",
                 height=_h(len(flat), 520),
                 column_config={"Progress %": st.column_config.ProgressColumn(
                     min_value=0, max_value=100, format="%d%%")})


# ----------------------------------------------------------------------------
# صفحة التفتيش الفني TIR
# التخطيط: يُصدر طلبات التفتيش / التفتيش: يرفع النتائج وRecommendations
# ----------------------------------------------------------------------------
def _tir_detail(tir):
    """Request, requirements, report and role actions for one TIR."""
    equip_lbl = tir["equip"]
    no = tir["doc_no"]
    st.markdown(f"**{no}**" + (f" — {equip_lbl}" if equip_lbl else ""))
    st.markdown(
        f"**Stage:** {tir['stage_ar']}  \n"
        f"**Inspection types:** {tir['types_ar']}  \n"
        f"**Reason:** {db.TIR_REASONS.get(tir.get('reason'), '—')} · "
        f"**Priority:** {fmt_prio(tir['priority']) if tir.get('priority') else '—'} · "
        f"**Required by:** {tir.get('due_date') or '—'} · "
        f"**Equipment:** {db.EQUIP_STATES.get(tir.get('equip_state'), '—')}  \n"
        + (f"**Work Request:** {tir['wr_no']}" if tir.get("wr_no") else ""))
    if tir.get("requesting_note"):
        st.info(f"**Planning Note:** {tir['requesting_note']}")

    # --- requirements studied by Inspection / issued by Planning
    if tir.get("studied_at"):
        st.markdown("**Preparations required before inspection**")
        if tir["reqs"]:
            show_df([{"Ref": r["id"], "Requirement": r["kind_ar"], "Details": r["detail"],
                      "Planning document": r["state_ar"]} for r in tir["reqs"]], height=_h(len(tir["reqs"]), 200))
        else:
            st.caption("None — Inspection confirmed that no preparation is required.")
    elif tir["status"] == "P":
        st.caption("Inspection has not studied the request yet.")

    # --- report (visible to Planning as soon as Inspection submits it)
    data = tir_form.load(tir)
    if data:
        with st.expander("Inspection Report", expanded=tir["status"] == "C"):
            tir_form.view_ui(data)
    if tir.get("submitted_at"):
        st.caption(f"📅 Report submitted to Planning: {tir['submitted_at']}")
    if tir.get("sent_at"):
        st.caption(f"📤 Sent to the requesting department: {tir['sent_at']}")
    st.markdown("---")

    # --- actions by role
    if ROLE == "INSP" and tir["status"] != "C":
        b = st.columns(3)
        if b[0].button("🔎 Study request / Requirements", key=f"tir_study_{no}", use_container_width=True):
            dlg_tir_study(tir)
        if tir["status"] == "P" and b[1].button("▶️ Start Inspection", key=f"tir_start_{no}",
                                                use_container_width=True):
            db.set_tir_status(no, "O", actor=ROLE)
            st.rerun()
        if b[2].button("📋 Fill / Submit Report", type="primary", key=f"tir_sub_{no}",
                       use_container_width=True):
            dlg_tir_submit(tir)
        if tir["stage"] in ("STUDY", "ISSUE", "PREP"):
            st.warning("Preparations are not complete yet — you can still start, but check the requirements above.")
    elif ROLE == "PLN":
        open_reqs = [r for r in tir["reqs"] if not r.get("doc_no")]
        if open_reqs and tir["status"] != "C":
            st.warning(f"{len(open_reqs)} requirement(s) need a Planning document. Issue them on the main Work Request "
                       "and select the requirement in the dialog.")
            w = db.query("SELECT * FROM work_requests WHERE doc_no=?", (tir["wr_no"],))
            if w:
                c = st.columns(2)
                if c[0].button("📋 Issue Work Order", key=f"tirwo_{no}", use_container_width=True):
                    dlg_wo(w[0])
                if c[1].button("🚧 Issue Service Request", key=f"tirsr_{no}", use_container_width=True):
                    dlg_sr(wr=w[0])
        if tir["status"] == "C" and not tir.get("sent_at"):
            has_rec = tir_form.has_recommendations(data)
            st.info("Review the report above, then send it to the requesting department."
                    + (" It contains recommendations: the requester will be able to raise a Work Request."
                       if has_rec else ""))
            note = st.text_input("Note to the requester (optional)", key=f"tirsend_note_{no}")
            if st.button("📤 Send to Requesting Department", type="primary", key=f"tirsend_{no}"):
                try:
                    db.tir_send(no, ROLE, note, has_rec)
                    done(f"Report {no} sent to the requesting department")
                except ValueError as e:
                    st.error(str(e))
    show_timeline(no, 170)


def page_tir():
    st.subheader("Technical Inspection (TIR)")
    if ROLE == "PLN":
        st.caption("Inspection Requests are issued from Work Request Review. Inspection studies each request and sends "
                   "its requirements; Planning issues the documents, then reviews the report and sends it to the requester.")
    role_ = ROLE if ROLE in ("PLN", "INSP") else "PLN"
    rows = db.list_tirs()
    for r in rows:
        r["view"] = db.tir_view(r["stage"], role_)
    cnt = {k: sum(1 for r in rows if r["view"] == k) for k in db.TIR_VIEWS}
    default = "ACT" if cnt["ACT"] else "WAIT"
    flt = st.segmented_control("View", list(db.TIR_VIEWS), default=default, key=f"tir_flt_{ROLE}",
                               format_func=lambda k: f"{db.TIR_VIEWS[k]} ({cnt[k]})",
                               label_visibility="collapsed") or default
    shown = [r for r in rows if r["view"] == flt]
    sel, detail = record_browser(
        shown,
        {"doc_no": "No.", "plan_code": "Plan Code", "doc_date": "Date", "wr_no": "Work Request",
         "equip": "Equipment", "types_ar": "Inspection Types", "stage_ar": "Stage"},
        f"tir_{flt}", title="Technical Inspection Request List", height=_h(len(shown), 500))
    if sel:
        with detail:
            _tir_detail(db.get_tir(sel["doc_no"]))


    # ----------------------------------------------------------------------------
    # Annual Overhaul & Preventive Maintenance Plan
    # تُعتمد في اجتماع نهاية Year خارج المنظومة , ثم يُنزَّل قالب Excel فارغ لكل قسم إنتاج،
    # يعبّئه التخطيط (معدات × شهور بعلامة X) ويستورده هنا. بعد الاستيراد تُقفل الخطة.
    # ----------------------------------------------------------------------------
def _pm_type_table(year, prod, dept, ptype, etype, hdr, idx):
    grid = cdb("plan_grid", year, prod, dept, ptype, etype)
    counts = {m: sum(1 for g in grid if m in g["months"]) for m in range(1, 13)}
    k = st.columns(3)
    k[0].metric("Equipment", len(grid))
    k[1].metric("Items (Equipment × Month)", sum(counts.values()))
    k[2].metric("No Work Request Issued",
                sum(1 for g in grid for i in g["months"].values() if i["status"] == "PLANNED"))
    st.caption("Number of equipment items planned per month")
    st.dataframe(pd.DataFrame([{db.MONTHS_EN[m - 1]: n for m, n in counts.items()}]),
                 hide_index=True, width="stretch")
    rows = []
    for g in grid:
        r = {"tag": g["tag"], "loc": g["location"], "notes": g["notes"],
             "shared": ", ".join(db.plan_dept_en(d) for d in g["shared"])}
        for m in range(1, 13):
            r[f"m{m}"] = g["months"][m]["symbol"] if m in g["months"] else ""
        rows.append(r)
    cols = {"tag": "Tag No", "loc": "Location",
            **{f"m{m}": db.MONTHS_EN[m - 1] for m in range(1, 13)},
            "shared": "Shared With", "notes": "Remarks"}
    show_df(rows, cols, _h(len(rows), 480))
    xlsx = report.build_plan_grid_xlsx(year, prod, dept, ptype, etype, hdr, grid)
    st.download_button("⬇️ Download Table (Excel)", xlsx, key=f"pmx_{prod}_{dept}_{ptype}_{idx}",
                       file_name=f"Plan_{year}_{prod}_{dept}_{ptype}_{idx + 1}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _pm_grid_view(year, prod, dept, ptype):
    hdr = db.plan_header(year, prod)
    if not hdr:
        st.info("No approved plan exists for this operating department and year."
                + (" Import the plan from the Import Plan tab." if ROLE == "PLN" else ""))
        return
    st.caption(f"🔒 Approved Plan — Approval Meeting: {ltr(hdr['approved_on'])} · "
               f"Minutes No.: {hdr['minutes_no'] or '—'} · Last Import: {ltr(hdr['imported_at'])}")
    types = db.plan_types_present(year, prod, dept, ptype)
    if not types:
        st.info("No items for this department and plan type.")
        return
    st.caption("🟦 Planned · 📨 Work Request Sent · 📋 Work Order Issued · 🔧 In Progress · ✅ Completed · ⛔ Rejected · "
               "🟥 Overdue, awaiting Planning decision · 🟧 Overdue, retained by Planning decision")
    for i, (tab, et) in enumerate(zip(st.tabs([t or "Other" for t in types]), types)):
        with tab:
            _pm_type_table(year, prod, dept, ptype, et, hdr, i)


def _pm_template_tab():
    st.caption("After plan approval: enter equipment types (up to five per template), "
               "download a template for each Production department, complete it, then upload it through Import Plan. "
               "If there are more than five types, download additional templates.")
    c = st.columns([2, 4, 2])
    year = int(c[0].number_input("Plan Year", min_value=2024, max_value=2100,
                                 value=date.today().year + 1, step=1, key="plt_year"))
    with c[1]:
        prod = seg("Production Department (Operating)", PROD, "plt_prod", db.dept_label)
    part = int(c[2].number_input("Template Number", min_value=1, max_value=9, value=1, step=1,
                                 key="plt_part"))
    known = db.plan_known_types()
    picked = st.multiselect("Previously Used Types (Quick Select)", known, key="plt_known") \
        if known else []
    st.markdown(f"**Equipment Types for this Template** (up to {report.MAX_TYPES}) — "
                "e.g. Pumps, Safety Valves, Control Valves, Electric Motors")
    cols = st.columns(report.MAX_TYPES)
    typed = [cols[i].text_input(f"Type {i + 1}", key=f"plt_t{i}") for i in range(report.MAX_TYPES)]
    names, err = [], None
    for raw in list(picked) + [t for t in typed if t.strip()]:
        try:
            n = report.clean_sheet_name(raw)
        except ValueError as e:
            err = str(e)
            break
        if n.lower() not in (x.lower() for x in names):
            names.append(n)
    if err:
        st.error(err)
    elif not names:
        st.info("Enter at least one equipment type to download the template.")
    elif len(names) > report.MAX_TYPES:
        st.error(f"Each template supports up to {report.MAX_TYPES} types — you selected {len(names)}. "
                 "Put the remaining types in another template.")
    else:
        st.download_button(
            f"⬇️ Download Template ({len(names)} sheets)", report.build_plan_template_xlsx(
                year, prod, names, part),
            file_name=f"Maintenance_Plan_Template_{prod}_{year}_part{part}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary")
        st.caption("Sheets: " + " | ".join(names))
    st.caption("Each sheet has one row per equipment and plan type: Tag No., Location, Plan Type (Overhaul or PM), "
               "mark X under each executing department (M / E / I), or Other for equipment maintained by external companies "
               "(enter the company name in Notes), mark X in the planned months, then add notes. "
               "The year and Production department are embedded in the template.")


def _pm_import_tab():
    up = st.file_uploader("Plan File (completed template)", type=["xlsx"], key="pli_file")
    if not up:
        st.caption("Upload the completed template to preview it before saving. Upload each template separately; "
                   "uploading one template does not affect other templates in the same plan.")
        return
    try:
        parsed = report.parse_plan_xlsx(up.getvalue())
    except ValueError as e:
        st.error(str(e))
        return
    year, prod = parsed["year"], parsed["prod_dept"]
    if prod not in PROD:
        st.error("The Production department recorded in the template is invalid.")
        return
    types_txt = ", ".join(parsed["sheets"]) or "—"
    st.markdown(f"**Year:** {year}  ·  **Production Department:** {db.dept_label(prod)}  ·  "
                f"**Template No.:** {parsed['part'] or '—'}  ·  **Types:** {types_txt}")
    prev = db.plan_preview(year, prod, parsed["sheets"])
    if parsed["issues"]:
        with st.expander(f"⚠️ {len(parsed['issues'])} file issue(s) (these rows will not be imported)",
                         expanded=True):
            for i in parsed["issues"]:
                st.write("• " + i)
    if not prev:
        st.error("No valid items to import from this file.")
        return
    n = {k: sum(1 for p in prev if p["status"] == k) for k in ("MATCH", "SIMILAR", "NEW")}
    m = st.columns(4)
    m[0].metric("Rows", len(prev))
    m[1].metric("Registered Equipment", n["MATCH"])
    m[2].metric("Tag Similar to Registered", n["SIMILAR"])
    m[3].metric("New Equipment", n["NEW"])
    links = {}
    sim = [p for p in prev if p["status"] == "SIMILAR"]
    if sim:
        st.markdown("**Tag similar to registered equipment — choose:**")
        for p in sim:
            links[p["key"]] = st.radio(
                f'{p["eq_type"]} — Row {p["row"]}: "{p["tag"]}"',
                ["LINK", "NEW"], horizontal=True, key=f"pli_{p['key']}",
                format_func=lambda x, p=p: (f"Link to registered equipment {p['match_tag']}" if x == "LINK"
                                            else f"Add {p['tag']} as new equipment"))
    for p in prev:
        if p["warn"]:
            st.warning(f"{p['tag']}: {p['warn']}")
    ar = {"MATCH": "Registered", "SIMILAR": "Similar", "NEW": "New"}
    show_df([dict(p, plan_ar=db.PLAN_TYPES[p["ptype"]].split(" (")[0],
                  dep="+".join(p["depts"]), st_ar=ar[p["status"]],
                  mon=", ".join(db.MONTHS_EN[x - 1] for x in p["months"])) for p in prev],
            {"eq_type": "Equipment Type", "row": "Row", "tag": "Tag No", "n_tags": "Item Equipment",
             "location": "Location", "plan_ar": "Plan", "dep": "Executing Party", "mon": "Months",
             "st_ar": "Equipment in System", "notes": "Remarks"}, _h(len(prev), 420))
    old = db.plan_header(year, prod)
    if old:
        st.warning(f"A plan for {year} already exists for this department (last imported {old['imported_at']}). This import will replace "
                   "only the items for equipment types in this file that have not yet generated a Work Request. "
                   "Other template types and items with issued requests will remain unchanged.")
    c = st.columns(2)
    when = c[0].date_input("Approval Meeting Date", value=None, key="pli_date")
    minutes = c[1].text_input("Minutes No. (optional)", key="pli_minutes")
    if st.button("✅ Import & Approve Plan", type="primary", key="pli_ok"):
        try:
            res = db.plan_import(year, prod, prev, links, when, minutes or None, actor=ROLE)
            done(f"Plan approved {year} for {db.dept_en(prod)}: {res['items']} item(s) "
                 f"({', '.join(res['types'])})"
                 + (f" and {res['new_equipment']} new equipment" if res["new_equipment"] else ""))
        except ValueError as e:
            st.error(str(e))


def pick_multi(rows, rename, key, height=None):
    """Table for selecting multiple rows; returns the selected rows."""
    if not rows:
        st.info("No data available.")
        return []
    rows = filter_bar(rows, rename)
    if not rows:
        return []
    df = _show_blank(pd.DataFrame(rows)[[c for c in rename if c in rows[0]]].rename(columns=rename))
    ev = st.dataframe(df, hide_index=True, width="stretch", height=height or _h(len(df), 420),
                      on_select="rerun", selection_mode="multi-row", key=f"{key}_{len(rows)}")
    return [rows[i] for i in ev.selection.rows if i < len(rows)]


def _due_rows(items):
    out = []
    for it in items:
        if it["state"] == "SENT":
            state = f"Submitted {it['wr_no']} {db.PM_SYMBOL[it['status']]}"
        elif it["state"] == "OVERDUE":
            state = f"Overdue by {it['late']} month(s)"
        else:
            state = "Due"
        if it.get("rejected_wr"):
            state += f" (rejected {it['rejected_wr']} — resubmit)"
        if it.get("unexec"):
            state += " · 🟧 Retained by Planning decision" if it.get("decision") == "KEPT" \
                else " · 🟥 Overdue — awaiting decision"
        out.append(dict(it, month_ar=f"{db.MONTHS_EN[it['month'] - 1]} {it['year']}",
                        plan_ar=db.PLAN_TYPES[it["plan_type"]].split(" (")[0],
                        tags_s=", ".join(it["tags"]), depts_s=" + ".join(
                            db.plan_dept_en(x) for x in it["depts"]), state_ar=state,
                        loc_s=", ".join(it["locations"])))
    return out


DUE_COLS = {"code": "Item Code", "month_ar": "Month", "eq_type": "Equipment Type", "tags_s": "Equipment",
            "loc_s": "Location", "plan_ar": "Plan", "depts_s": "Executing Party",
            "state_ar": "Status", "reason": "Retention Reason", "notes": "Remarks"}


def _pm_due_tab(prod):
    """Production Department: plan items become due on the first day of each month; submit one Work Request per item."""
    st.caption("Annual Plan items due this month (from the first day). Select the items, then submit Work Request(s) "
               "for each item. Planning will then issue the required Work Orders and Service Requests.")
    items = cdb("plan_due_items", prod)
    n_due = sum(1 for i in items if i["state"] != "SENT")
    opts = {"DUE": f"Not submitted ({n_due})", "SENT": f"Request Submitted ({len(items) - n_due})",
            "ALL": f"All ({len(items)})"}
    flt = st.segmented_control("View", list(opts), default="DUE", key=f"pmd_flt_{prod}",
                               format_func=opts.get, label_visibility="collapsed") or "DUE"
    shown = [i for i in items if flt == "ALL" or (flt == "SENT") == (i["state"] == "SENT")]
    sel = pick_multi(_due_rows(shown), DUE_COLS, f"pmd_{prod}_{flt}")
    todo = [r for r in sel if r["state"] != "SENT"]
    if ROLE == prod:
        note = st.text_input("Note to Planning (optional — added to each request)", key=f"pmd_note_{prod}")
        if st.button(f"📨 Submit Work Request(s) for Selected Items ({len(todo)})", type="primary",
                     disabled=not todo, key=f"pmd_send_{prod}"):
            ok, errs = [], []
            for r in todo:
                try:
                    ok.append(db.plan_send_wr(r["ref"], ROLE, note or None))
                except ValueError as e:
                    errs.append(f"{r['tags_s']}: {e}")
            for e in errs:
                st.error(e)
            if ok:
                done(f"Submitted {len(ok)} Work Request(s) to Planning: {', '.join(ok)}"
                     + (f" — failed: {len(errs)}" if errs else ""))
    else:
        st.caption("Only the operating Production department is authorized to submit these requests.")


def _pm_follow_tab():
    """Planning: monitor items and record decisions for overdue items (retain in the plan with a reason). No documents are issued before a Production request."""
    st.caption("Planning does not issue a Work Order, Service Request, or Inspection until Work Request(s) are received from the Production department "
               "(through Work Request Review). An overdue incomplete item remains in the plan, "
               "and Planning records the reason for retaining it.")
    for p in PROD:
        its = cdb("plan_due_items", p)
        m = st.columns(4)
        m[0].metric(f"{db.dept_en(p)} — Work Request not submitted",
                    sum(1 for i in its if i["state"] in ("DUE", "OVERDUE")))
        m[1].metric("Overdue (month elapsed)", sum(1 for i in its if i["state"] == "OVERDUE"))
        m[2].metric("Overdue (month elapsed)", sum(1 for i in its if i["unexec"]))
        m[3].metric("Request Submitted", sum(1 for i in its if i["state"] == "SENT"))
    un = [dict(r, prod_s=db.dept_en(r["prod_dept"])) for r in _due_rows(
        [i for p in PROD for i in cdb("plan_due_items", p) if i["unexec"]])]
    if not un:
        st.success("No overdue items.")
        return
    waiting = sum(1 for r in un if r.get("decision") != "KEPT")
    st.markdown(f"**Overdue Incomplete Items ({len(un)}) — Awaiting Planning Decision:  {waiting}**")
    sel = pick_multi(un, {"prod_s": "Production", **DUE_COLS}, "pmf", _h(len(un), 360))
    with st.form("pmf_form", clear_on_submit=True, border=False):
        reason = st.text_input("Retention Reason (required), e.g. shutdown unavailable, awaiting spare parts...")
        if st.form_submit_button(f"📝 Retain Selected Items with Reason ({len(sel)})", type="primary"):
            if not sel:
                st.error("Select at least one item from the table.")
            elif not reason.strip():
                st.error("Retention reason is required.")
            else:
                errs, ok = [], 0
                for r in sel:
                    try:
                        db.plan_keep(r["code"], reason, ROLE)
                        ok += 1
                    except ValueError as e:
                        errs.append(f"{r['tags_s']}: {e}")
                for e in errs:
                    st.error(e)
                if ok:
                    done(f"Retention reason recorded for {ok} plan item(s)")


def _pm_items_tab(year, prod, dept, ptype):
    """Plan items by code: search by equipment or code to find the item and all its documents."""
    st.caption("Each plan item (template row × month) has a permanent code shown in the Work Request description and all documents issued against it. "
               "Search by Tag No. to find the equipment plan items and all related documents, including items shared by multiple equipment tags.")
    items = cdb("plan_items_list", year, prod, dept, ptype)
    if not items:
        st.info("No items exist for this department and plan type in this year.")
        return
    rows = [dict(it, month_ar=db.MONTHS_EN[it["month"] - 1], tags_s=", ".join(it["tags"]),
                 plan_ar=db.PLAN_TYPES[it["plan_type"]].split(" (")[0],
                 depts_s=" + ".join(db.plan_dept_en(x) for x in it["depts"]),
                 wr=it["wr_no"] or "—", loc_s=", ".join(it["locations"]),
                 state=((("🟧 " if it["decision"] == "KEPT" else "🟥 ") + "Overdue — ")
                        if it["unexec"] else f"{db.PM_SYMBOL[it['status']]} ")
                       + db.PM_STATUS_AR[it["status"]])
            for it in items]
    sel, detail = record_browser(rows, {"code": "Item Code", "month_ar": "Month", "eq_type": "Equipment Type",
                                        "tags_s": "Equipment", "loc_s": "Location", "plan_ar": "Plan",
                                        "depts_s": "Executing Party", "wr": "Work Request", "state": "Status",
                                        "reason": "Retention Reason", "notes": "Remarks"},
                                 f"pmi_{year}_{prod}_{dept}_{ptype}", title="Annual Plan Items",
                                 height=_h(len(rows), 380))
    if sel and sel["code"]:
        with detail:
            docs = db.plan_code_docs(sel["code"])
            for ev in reversed(db.plan_events(sel["code"])):
                st.warning(f"📝 {ev['ts']} · {db.dept_en(ev['actor'])} · Retention decision: {ev['note']}")
            st.markdown(f"**Documents for Plan Item {sel['code']}** — {sel['tags_s']}")
            if docs:
                show_df(docs, {"kind": "Type", "doc_no": "No.", "doc_date": "Date",
                               "status_ar": "Status", "description": "Description"}, _h(len(docs), 220))
            else:
                st.caption("No documents have been issued for this item yet.")

def page_pm_plan():
    is_pln = ROLE == "PLN"
    st.subheader("Annual Overhaul & Preventive Maintenance Plan")
    c = st.columns([2, 3, 3, 4])
    year = int(c[0].number_input("Year", min_value=2024, max_value=2100,
                                 value=date.today().year, step=1, key="pm_year"))
    if ROLE in PROD:
        prod = ROLE
        c[1].markdown(f"**Production Department:** {db.dept_label(ROLE)}")
    else:
        prod = c[1].segmented_control("Production Department", PROD, default="REF", format_func=db.dept_en,
                                      key="pm_prod") or "REF"
    if ROLE in MAINT:
        dept = ROLE
        c[2].markdown(f"**Maintenance Department:** {db.dept_label(ROLE)}")
    else:
        dept = c[2].segmented_control("Executing Party", MAINT + ["OTH"], default="M",
                                      format_func=db.plan_dept_en, key="pm_dept") or "M"
    ptype = c[3].segmented_control("Plan Type", list(db.PLAN_TYPES), default="OVERHAUL",
                                   format_func=lambda x: db.PLAN_TYPES[x],
                                   key="pm_type") or "OVERHAUL"
    if is_pln:
        t1, t5, t4, t2, t3 = st.tabs(["📅 Annual Schedule", "📋 Items", "📌 Due Items Tracking",
                                      "⬇️ Template", "⬆️ Import Plan"])
        with t1:
            _pm_grid_view(year, prod, dept, ptype)
        with t5:
            _pm_items_tab(year, prod, dept, ptype)
        with t4:
            _pm_follow_tab()
        with t2:
            _pm_template_tab()
        with t3:
            _pm_import_tab()
    elif ROLE in PROD:
        t0, t1, t5 = st.tabs(["📌 Due This Month", "📅 Annual Schedule", "📋 Items"])
        with t0:
            _pm_due_tab(ROLE)
        with t1:
            _pm_grid_view(year, prod, dept, ptype)
        with t5:
            _pm_items_tab(year, prod, dept, ptype)
    else:
        t1, t5 = st.tabs(["📅 Annual Schedule", "📋 Items"])
        with t1:
            _pm_grid_view(year, prod, dept, ptype)
        with t5:
            _pm_items_tab(year, prod, dept, ptype)


# ----------------------------------------------------------------------------
# القوائم حسب الدور (تنقل علوي مباشر)
# ----------------------------------------------------------------------------
@st.cache_data(show_spinner=False, max_entries=48)
def _mrep(dept, y, m, ver, day):
    return monthly.report(dept, y, m)


@st.cache_data(show_spinner=False, max_entries=48)
def _mseries(dept, y, m, ver, day):
    return monthly.index_series(dept, y, m, 6)


def page_monthly():
    st.subheader("Monthly Report")
    today = date.today()
    c = st.columns([2, 2, 6])
    year = int(c[0].number_input("Year", min_value=2024, max_value=2100, value=today.year, step=1, key="mr_year"))
    month = int(c[1].number_input("Month", min_value=1, max_value=12, value=today.month, step=1, key="mr_month"))
    if ROLE == "PLN":
        dept = c[2].segmented_control("Report for", ["PLN"] + MAINT + PROD + ["INSP"], default="PLN",
                                      format_func=db.dept_en, key="mr_dept") or "PLN"
    else:
        dept = ROLE
    ver, day = db.data_version(), today.isoformat()
    rep = _mrep(dept, year, month, ver, day)
    series = _mseries(dept, year, month, ver, day)
    idx = rep["index"]
    st.markdown(f"### {db.dept_en(dept)} — {rep['label']}")
    for n_ in rep["notes"]:
        st.warning(n_)
    # internal improvement index (never a ranking between departments)
    k = st.columns([2, 5])
    k[0].metric("Improvement index", "—" if idx["value"] is None else f"{idx['value']:g}",
                help="Internal indicator: compare this department with its own previous months.")
    if idx["band"]:
        k[0].caption(idx["band"])
    pts = [(lab, v) for lab, v in series if v is not None]
    if len(pts) > 1:
        k[1].line_chart(pd.DataFrame({"Improvement index": [v for _, v in pts]}, index=[lab for lab, _ in pts]),
                        height=150)
    if idx["components"]:
        df = pd.DataFrame([{"Component": c_["label"], "Result": str(c_["raw"]),
                            "Score": c_["score"], "Weight %": c_["weight"]} for c_ in idx["components"]])
        st.dataframe(_show_blank(df), hide_index=True, width="stretch",
                     column_config={"Score": st.column_config.ProgressColumn(min_value=0, max_value=100,
                                                                            format="%d")})
        if idx["weak"]:
            st.info("Where to improve this month: " + " · ".join(idx["weak"]))
        if idx["missing"]:
            st.caption("Not calculated (no data): " + ", ".join(idx["missing"]))
    for sec in rep["sections"]:
        st.markdown(f"**{sec['title']}**")
        if sec["metrics"]:
            show_df([{"Item": a, "Value": "" if b is None else str(b)} for a, b in sec["metrics"]],
                    height=_h(len(sec["metrics"]), 260))
        if sec["table"]:
            show_df([{c_: ("" if x is None else str(x)) for c_, x in zip(sec["table"]["cols"], r)}
                     for r in sec["table"]["rows"]], height=_h(len(sec["table"]["rows"]), 260))
        if sec.get("note"):
            st.caption(sec["note"])
    st.caption("Improvement index = weighted average of the components above (each scored 0–100); components "
               "without data are left out. It is an internal indicator for improving against the department's own "
               "history — not a ranking. Targets are set by Planning in Master Data › Report Settings.")
    st.download_button("⬇️ Download Report (Excel)", report.build_monthly_xlsx(rep, series),
                       file_name=f"Monthly_{dept}_{year}-{month:02d}.xlsx", key=f"mrx_{dept}_{year}_{month}",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _badges():
    if ROLE in PROD:
        return {"my": db.awaiting_count(ROLE)}
    if ROLE == "PLN":
        return {"review": db.query("SELECT COUNT(*) n FROM work_requests WHERE status='NEW'")[0]["n"],
                "sr": db.query("SELECT COUNT(*) n FROM service_requests WHERE status!='C'")[0]["n"],
                "tir": db.tir_badge("PLN")}
    if ROLE == "INSP":
        return {"tir": db.tir_badge("INSP")}
    return {"home": db.new_wo_count(ROLE), "my": db.awaiting_count(ROLE)}


if ROLE in PROD:
    MENU = [("my", "📊 My Requests", page_my_requests),
            ("pm", "🗓️ Maintenance Plan", page_pm_plan),
            ("monthly", "📅 Monthly Report", page_monthly)]
elif ROLE == "PLN":
    MENU = [("home", "🏠 Home", page_planning_home),
            ("review", "🔎 Work Request Review", page_wr_review),
            ("sr", "🚧 Service Requests", page_sr),
            ("track", "👁️ Work Tracking", page_tracking),
            ("groups", "🧩 Work Groups", page_groups),
            ("pm", "🗓️ Maintenance Plans", page_pm_plan),
            ("tir", "🔬 Technical Inspection (TIR)", page_tir),
            ("daily", "📄 Daily Report", page_planning_daily),
            ("monthly", "📅 Monthly Report", page_monthly),
            ("dash", "📈 Overview Dashboard", page_dashboard),
            ("master", "🗂️ Master Data", page_master)]
elif ROLE == "INSP":
    MENU = [("tir", "🔬 Technical Inspection Reports", page_tir),
            ("monthly", "📅 Monthly Report", page_monthly)]
else:
    MENU = [("home", "🏠 Work Orders & Today's Hours", page_maint_home),
            ("track", "🧰 Work Order Tracking", page_tracking),
            ("my", "📨 My Requests", page_my_requests),
            ("pm", "🗓️ Maintenance Plan", page_pm_plan),
            ("daily", "📄 Daily Report", page_daily),
            ("monthly", "📅 Monthly Report", page_monthly)]

MENU.append(("admin", "Admin Control Panel", page_admin_control))

_badge = _badges()
_labels = {k: (f"{l} ({_badge[k]})" if _badge.get(k) else l) for k, l, _ in MENU}
_keys = [k for k, _, _ in MENU]
_choice = _top[1].segmented_control("Menu", _keys, format_func=_labels.get,
                                    default=_keys[0], key=f"menu_{ROLE}",
                                    label_visibility="collapsed") or _keys[0]

_msg = st.session_state.pop("_flash", None)
if _msg:
    st.toast(_msg, icon="✅")

{k: f for k, _, f in MENU}[_choice]()

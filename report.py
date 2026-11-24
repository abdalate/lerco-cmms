# -*- coding: utf-8 -*-
"""Generate the daily Excel report using the IMD paper-form layout."""
import re
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

import database as db

THIN = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_FILL = PatternFill("solid", fgColor="D9E1F2")
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

COLS = ["S.N", "Tag No. / Equipment", "W.O & J.C No.", "Job description", "Job status",
        "Started date", "Finished date", "Ach. %", "SPV.", "TECH", "OV.T",
        "CONT", "PM", "CM", "PD", "Remarks"]
WIDTHS = [5, 26, 17, 42, 8, 12, 12, 8, 7, 7, 7, 7, 5, 5, 5, 34]


def _box(ws, r1, c1, r2, c2):
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            ws.cell(r, c).border = BORDER


def build_daily_xlsx(dept, report_date, rows, manpower):
    wb = Workbook()
    ws = wb.active
    ws.title = "Daily Report"
    ws.sheet_view.rightToLeft = False
    for i, w in enumerate(WIDTHS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws["A1"] = "Libyan Emirates Oil Refining Company"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A3"] = f"DATE: {report_date.strftime('%d-%b-%y')}   ({report_date.strftime('%A')})"
    ws["D3"] = f"Division: {db.dept_en(dept)}"
    ws["D3"].font = Font(bold=True)
    ws["K3"] = "DAILY REPORT"
    ws["K3"].font = Font(bold=True, size=14)

    hr = 5
    for i, h in enumerate(COLS, 1):
        c = ws.cell(hr, i, h)
        c.font = Font(bold=True)
        c.fill = HEAD_FILL
        c.alignment = CENTER
    ws.cell(hr - 1, 9, "Consumed M/H").font = Font(bold=True)
    ws.cell(hr - 1, 9).alignment = CENTER
    ws.merge_cells(start_row=hr - 1, start_column=9, end_row=hr - 1, end_column=12)
    ws.cell(hr - 1, 13, "Job Category").font = Font(bold=True)
    ws.cell(hr - 1, 13).alignment = CENTER
    ws.merge_cells(start_row=hr - 1, start_column=13, end_row=hr - 1, end_column=15)
    _box(ws, hr - 1, 9, hr - 1, 15)

    r = hr + 1
    tot = {"spv": 0, "tech": 0, "ot": 0, "cont": 0}
    for n, row in enumerate(rows, 1):
        for k in tot:
            tot[k] += row[k] or 0
        vals = [n, row["equip"], row["doc_no"], row["description"],
                row["status"], row["start_date"], row["end_date"],
                f"{row['progress'] or 0}%",
                row["spv"] or None, row["tech"] or None,
                row["ot"] or None, row["cont"] or None,
                "*" if row["category"] == "PM" else None,
                "*" if row["category"] == "CM" else None,
                "*" if row["category"] == "PD" else None,
                row["remarks"]]
        for i, v in enumerate(vals, 1):
            c = ws.cell(r, i, v)
            c.alignment = LEFT if i in (4, 16) else CENTER
        r += 1
    # Row المجاميع
    ws.cell(r, 4, "total").font = Font(bold=True)
    ws.cell(r, 4).alignment = CENTER
    for i, k in zip(range(9, 13), ("spv", "tech", "ot", "cont")):
        ws.cell(r, i, tot[k] or None).font = Font(bold=True)
        ws.cell(r, i).alignment = CENTER
    _box(ws, hr, 1, r, len(COLS))
    total_mh = sum(tot.values())
    r += 1
    ws.cell(r, 4, "Total consumed M/H").alignment = CENTER
    ws.cell(r, 9, total_mh).font = Font(bold=True)
    ws.cell(r, 9).alignment = CENTER
    _box(ws, r, 4, r, 12)

    r += 2
    ws.cell(r, 1, "Notes: Job Status: O=In Progress ; C=Completed ; P=Pending  -  "
                  "Job Category: ( PM=Preventive ; CM=Corrective ; PD=Predictive ) "
                  "Maintenance.").font = Font(italic=True, size=9)

    # جدول القوى العاملة (أعداد فقط)
    r += 2
    heads = ["Approved M/P", "Supervisor", "Technician", "Leave", "Absent", "Sick",
             "In. Train.", "Ex. Train.", "Shift rest", "Other", "Contract"]
    keys = ["approved", "supervisors", "technicians", "leave_n", "absent", "sick",
            "in_train", "ex_train", "shift_rest", "other", "contract"]
    ws.cell(r, 4, "Manpower").font = Font(bold=True)
    for i, h in enumerate(heads):
        c = ws.cell(r, 5 + i, h)
        c.font = Font(bold=True)
        c.fill = HEAD_FILL
        c.alignment = CENTER
    ws.cell(r, 4).fill = HEAD_FILL
    ws.cell(r, 4).alignment = CENTER
    ws.cell(r + 1, 4, db.dept_en(dept)).alignment = CENTER
    for i, k in enumerate(keys):
        ws.cell(r + 1, 5 + i, manpower.get(k, 0)).alignment = CENTER
    _box(ws, r, 4, r + 1, 5 + len(heads) - 1)
    ws.row_dimensions[r].height = 32

    ws.cell(r + 4, 1, "Approved by: Division Head").font = Font(bold=True)
    ws.cell(r + 6, 1, "Maintenance Planning Division").font = Font(size=9)

    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.freeze_panes = ws.cell(hr + 1, 1)

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_planning_daily_xlsx(report_date, works):
    """Planning Daily Report: Work Group (if any), linked Work Requests, Work Orders, Service Requests, and Inspection Requests. Work Group status and progress are calculated from all linked records."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Planning Daily"
    for i, w in enumerate([5, 22, 7, 18, 26, 42, 20, 12, 11, 11, 8, 9, 34], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws["A1"] = "Libyan Emirates Oil Refining Company"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A3"] = f"DATE: {report_date.strftime('%d-%b-%y')}   ({report_date.strftime('%A')})"
    ws["F3"] = "Division: Planning & Programming"
    ws["F3"].font = Font(bold=True)
    ws["K3"] = "DAILY REPORT"
    ws["K3"].font = Font(bold=True, size=14)

    heads = ["S.N", "Doc No.", "Type", "WR No.", "Equipment", "Description", "Dept / Service",
             "Status", "Started", "Finished", "Ach. %", "M/H today", "Remarks"]
    hr = 5
    for i, h in enumerate(heads, 1):
        c = ws.cell(hr, i, h)
        c.font, c.fill, c.alignment = Font(bold=True), HEAD_FILL, CENTER

    wr_state = {"NEW": "New", "STUDY": "Under study", "REJECTED": "Rejected"}
    fills = {"WG": PatternFill("solid", fgColor="D9E1F2"),
             "WR": PatternFill("solid", fgColor="F2F2F2")}
    LEFT_COLS = (5, 6, 13)
    r = hr + 1
    n = 0

    def put(vals, font, fill=None):
        nonlocal r
        for i, v in enumerate(vals, 1):
            c = ws.cell(r, i, v)
            c.alignment = LEFT if i in LEFT_COLS else CENTER
            c.font = font
            if fill:
                c.fill = fill
        r += 1

    def wr_block(w, pad, cpad):
        nonlocal n
        n += 1
        state = wr_state.get(w["wr_status"], w["status"])
        party = db.dept_en(w["requesting_dept"]) + (
            f" > {db.dept_en(w['exec_dept'])}" if w["exec_dept"] else "")
        remarks = (f"Rejected: {w['reject_reason']}"
                   if w["wr_status"] == "REJECTED" and w["reject_reason"] else "")
        put([n, pad + w["key"], "WR", w["key"], w["equip"], w["title"], party, state,
             w["start_date"], w["end_date"], f"{w['progress']}%",
             w["mh_today"] or None, remarks], Font(bold=True), fills["WR"])
        for c_ in w["children"]:
            put([None, cpad + c_["doc_no"], c_["kind"], w["key"], None, c_["description"],
                 c_["party"], c_["status"], c_["start_date"], c_["end_date"],
                 f"{c_['progress']}%", c_["mh_today"] or None, c_["remarks"]],
                Font(italic=True, size=9))

    for w in works:
        if w["kind"] == "WG":
            n += 1
            linked = "Linked WRs: " + (", ".join(w["wr_list"]) if w.get("wr_list") else "none")
            put([n, w["key"], "WG", None, w["equip"], w["title"],
                 db.dept_en(w["requesting_dept"]) if w["requesting_dept"] else "",
                 w["status"], w["start_date"], w["end_date"], f"{w['progress']}%",
                 w["mh_today"] or None,
                 linked + (f" | {w['remarks']}" if w["remarks"] else "")],
                Font(bold=True), fills["WG"])
            for x in w["wrs"]:
                wr_block(x, "   ", "       ")
        else:
            wr_block(w, "", "   ")
    if not works:
        r += 1
    _box(ws, hr, 1, r - 1, len(heads))
    ws.cell(r + 1, 1, "Notes: WG=Main Work ; WR=Work Request ; WO=Work Order ; SR=Service Request ; "
                      "TIR=Technical Inspection Report.  Status O=In Progress ; C=Completed ; "
                      "P=Pending.  Documents completed on a previous day are not listed.  A main "
                      "work's status and Ach. % come from ALL its linked documents (weighted by "
                      "estimated hours); it is completed only when all of them are completed."
            ).font = Font(italic=True, size=9)
    ws.cell(r + 3, 1, "Approved by: Division Head").font = Font(bold=True)
    ws.cell(r + 5, 1, "Maintenance Planning Division").font = Font(size=9)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.freeze_panes = ws.cell(hr + 1, 1)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


PM_FILL = {"PLANNED": "BDD7EE", "REQ": "FFF2CC", "WO": "F8CBAD", "O": "F4B183",
           "C": "C6E0B4", "REJ": "FF9999"}
PM_MARK = {"PLANNED": "●", "REQ": "WR", "WO": "WO", "O": "▶", "C": "✔", "REJ": "✘"}
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def build_pm_plan_xlsx(year, dept, plan_type, items):
    """Annual Maintenance Plan (Overhaul / Preventive): equipment in rows and calendar months in columns."""
    wb = Workbook()
    ws = wb.active
    ws.title = f"{plan_type} {year}"
    for i, w in enumerate([5, 30, 14, 9] + [7] * 12 + [52], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    last = 4 + 12 + 1
    ws["A1"] = "Libyan Emirates Oil Refining Company"
    ws["A1"].font = Font(bold=True, size=13)
    ws.cell(1, last, "Libyan Emirates Oil Refining Company").font = Font(bold=True, size=13)
    ws.cell(1, last).alignment = Alignment(horizontal="right")
    title = ("ANNUAL OVERHAUL PLAN" if plan_type == "OVERHAUL"
             else "ANNUAL PREVENTIVE MAINTENANCE PLAN") + f" {year}"
    ws["A3"] = f"Division: {db.dept_en(dept)}"
    ws["A3"].font = Font(bold=True)
    ws["F3"] = title
    ws["F3"].font = Font(bold=True, size=14)

    heads = ["S.N", "Tag No. / Equipment", "Class", "Priority"] + MONTHS_EN + ["Selection basis"]
    hr = 5
    for i, h in enumerate(heads, 1):
        c = ws.cell(hr, i, h)
        c.font, c.fill, c.alignment = Font(bold=True), HEAD_FILL, CENTER
    r = hr + 1
    for n, it in enumerate(items, 1):
        vals = [n, it["equip"], db.EQ_CLASSES.get(it["eq_class"], it["eq_class"] or ""),
                it["score"]] + [None] * 12 + [it["reason"]]
        for i, v in enumerate(vals, 1):
            c = ws.cell(r, i, v)
            c.alignment = LEFT if i in (2, last) else CENTER
        c = ws.cell(r, 4 + it["month"], PM_MARK[it["status"]])
        c.fill = PatternFill("solid", fgColor=PM_FILL[it["status"]])
        c.font = Font(bold=True)
        c.alignment = CENTER
        r += 1
    ws.cell(r, 2, "Total equipment per month").font = Font(bold=True)
    for m in range(1, 13):
        cnt = sum(1 for it in items if it["month"] == m)
        c = ws.cell(r, 4 + m, cnt or None)
        c.font, c.alignment = Font(bold=True), CENTER
    ws.cell(r, 4, len(items)).font = Font(bold=True)
    ws.cell(r, 4).alignment = CENTER
    _box(ws, hr, 1, r, last)
    ws.cell(r + 2, 1, "Legend:  ● Planned   WR Work request issued   WO Work order issued   "
                      "▶ In progress   ✔ Completed   ✘ Request rejected").font = Font(size=9)
    ws.cell(r + 3, 1, "Priority = 50% running hours since last overhaul + 30% vibration + 20% "
                      "condition (inspection reports); the highest priority equipment are "
                      "scheduled in the earliest months.").font = Font(italic=True, size=9)
    ws.cell(r + 5, 1, "Approved by: Division Head").font = Font(bold=True)
    ws.cell(r + 7, 1, "Maintenance Planning Division").font = Font(size=9)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.freeze_panes = ws.cell(hr + 1, 3)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# Approved annual plan: downloadable blank Excel template and parser for the completed template
# One file per Production department. The template supports up to five sheets; each sheet name is an equipment type entered by the technician
# (Pumps, Safety Valves, etc.). Download additional templates if there are more than five types. Each row contains:
#   Tag No | Location | Plan Type (Overhaul/PM) | M | E | I | Jan..Dec | Notes
# Executing maintenance departments (multiple allowed) and plan type are specified on each row; use X marks only.
# ----------------------------------------------------------------------------
TEMPLATE_ID = "LERCO-PLAN-4"
TEMPLATE_IDS_OK = ("LERCO-PLAN-3", "LERCO-PLAN-4")   # الإصدار 3 بلا عمود Other ويُقرأ كما هو
TEMPLATE_ROWS = 300
MAX_TYPES = 5
SHEET_NAME = {"OVERHAUL": "Overhaul", "PM": "PM"}
_MON = {m.lower(): i for i, m in enumerate(MONTHS_EN, 1)}
_MON_FULL = {n.lower(): i for i, n in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
_MON_AR = {n: i for i, n in enumerate(db.MONTHS_AR, 1)}
_PTYPE = {"overhaul": "OVERHAUL", "oh": "OVERHAUL", "Overhaul": "OVERHAUL",
          "شاملة": "OVERHAUL", "صيانة شاملة": "OVERHAUL", "صيانة Overhaul": "OVERHAUL",
          "pm": "PM", "preventive": "PM", "Preventive": "PM",
          "وقائية": "PM", "صيانة وقائية": "PM", "صيانة Preventive": "PM"}
BRAND_GREEN, BRAND_BROWN = "98C848", "6D5634"
C_TAG, C_LOC, C_TYPE, C_DEPT0, C_MON0, C_NOTES = 1, 2, 3, 4, 8, 20      # مواضع الأعمدة في القالب
_RESERVED = {"readme", "_meta"}


def clean_sheet_name(s):
    """اسم نوع المعدة كما سيكون اسم الورقة (حدود Excel: 31 حرفاً وبلا []:*?/\\)."""
    s = " ".join(re.sub(r"[\[\]:*?/\\]", " ", s or "").split())[:31].strip()
    if not s:
        raise ValueError("Equipment type name is empty")
    if s.lower() in _RESERVED:
        raise ValueError(f'Name "{s}" is reserved — choose another name')
    return s


def build_plan_template_xlsx(year, prod_dept, types, part=1):
    """Blank template for one Production department and one year: one worksheet per equipment type (up to five)."""
    names = []
    for t in types:
        n = clean_sheet_name(t)
        if n.lower() in (x.lower() for x in names):
            raise ValueError(f"Duplicate type: {n}")
        names.append(n)
    if not 1 <= len(names) <= MAX_TYPES:
        raise ValueError(f"The template supports 1 to {MAX_TYPES} equipment types")
    wb = Workbook()
    rd = wb.active
    rd.title = "README"
    lines = [
        f"LERCO - Annual Maintenance Plan Template (part {part})  |  Year {year}  |  "
        f"Production dept: {db.dept_label(prod_dept)}",
        "",
        "How to Fill:",
        "1) One sheet per equipment type: " + " | ".join(names) + ".",
        "2) One row per equipment AND plan type. If a machine has both overhaul and PM, use two rows.",
        "3) Tag No is required. Location is optional.",
        "   If one plan item covers several machines in the same month, write all their tags "
        "in the Tag No cell separated by commas (P-101A, P-101B). One request is issued for "
        "the whole item.",
        "4) Plan Type: choose Overhaul or PM from the list.",
        "5) Put X under M / E / I for each maintenance department that takes part "
        "(more than one if the work is shared).",
        "   Other = equipment maintained by an outside company, not by our maintenance "
        "departments (write the company in Notes).",
        "",
        "6) Put X in each month the work is planned.",
        "7) Do not rename sheets or change the header row.",
        "8) More than five equipment types? Download another template for the rest.",
    ]
    for i, t in enumerate(lines, 1):
        rd.cell(i, 1, t).font = Font(bold=(i in (1, 3)), size=12 if i == 1 else 11)
    rd.column_dimensions["A"].width = 125

    head_fill = PatternFill("solid", fgColor=BRAND_GREEN)
    heads = ["Tag No", "Location", "Plan Type", "M", "E", "I", "Other"] + MONTHS_EN + ["Notes"]
    for sheet in names:
        ws = wb.create_sheet(sheet)
        ws.sheet_properties.tabColor = BRAND_GREEN
        for i, h in enumerate(heads, 1):
            c = ws.cell(1, i, h)
            c.font = Font(bold=True, color="3B2F1A")
            c.fill = head_fill
            c.alignment = CENTER
            c.border = BORDER
        for i, w in enumerate([16, 30, 12, 5, 5, 5, 7] + [6] * 12 + [40], 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for rr in range(2, TEMPLATE_ROWS + 1):
            ws.cell(rr, C_TAG).number_format = "@"
            for cc in range(C_TYPE, C_NOTES):
                ws.cell(rr, cc).alignment = CENTER
        dv_t = DataValidation(type="list", formula1='"Overhaul,PM"', allow_blank=True,
                              showErrorMessage=True, errorTitle="Plan Type",
                              error="Select Overhaul or PM")
        dv_x = DataValidation(type="list", formula1='"X"', allow_blank=True,
                              showErrorMessage=True, errorTitle="Mark",
                              error="Use X only")
        ws.add_data_validation(dv_t)
        ws.add_data_validation(dv_x)
        dv_t.add(f"C2:C{TEMPLATE_ROWS}")
        dv_x.add(f"D2:S{TEMPLATE_ROWS}")
        ws.freeze_panes = "D2"
        ws["W1"] = f"{year} - {db.dept_en(prod_dept)} - {sheet}"
        ws["W1"].font = Font(bold=True, color=BRAND_BROWN)
    meta = wb.create_sheet("_meta")
    rows = [("template", TEMPLATE_ID), ("year", int(year)), ("prod_dept", prod_dept),
            ("part", int(part))] + [("type", n) for n in names]
    for i, (k, v) in enumerate(rows, 1):
        meta.cell(i, 1, k)
        meta.cell(i, 2, v)
    meta.sheet_state = "hidden"
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _split_tags(s):
    """A Tag cell may contain multiple equipment tags for one plan item, separated by commas, semicolons, or new lines."""
    out, seen = [], set()
    for t in re.split(r"[,\u060c;\n]+", s or ""):
        t = t.strip()
        if t and t.upper() not in seen:
            seen.add(t.upper())
            out.append(t)
    return out


def _txt(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _month_index(v):
    v = (v or "").strip().lower()
    if not v:
        return None
    if v in _MON_AR:
        return _MON_AR[v]
    if v in _MON_FULL:
        return _MON_FULL[v]
    return _MON.get(v[:3]) if v.isalpha() and len(v) <= 4 else None


def _is_mark(v):
    return _txt(v).lower() not in ("", "0", "-", "false", "no")


def parse_plan_xlsx(data):
    """Reads the completed template. Returns {year, prod_dept, part, sheets:{equipment_type: rows}, issues}."""
    try:
        wb = load_workbook(BytesIO(data), data_only=True)
    except Exception:
        raise ValueError("Unable to read file — ensure it is an Excel workbook (.xlsx)")
    if "_meta" not in wb.sheetnames:
        raise ValueError("This file is not a system template (download a new template from the Template tab)")
    meta_rows = [(_txt(r[0].value), r[1].value) for r in wb["_meta"].iter_rows(max_col=2)
                 if r[0].value]
    meta = dict(meta_rows)
    if meta.get("template") not in TEMPLATE_IDS_OK:
        raise ValueError("Unsupported template version — download a new template from the Template tab")
    try:
        year = int(meta["year"])
        prod = _txt(meta["prod_dept"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Year or Production department is missing from the template")
    types = [_txt(v) for k, v in meta_rows if k == "type"]
    sheets, issues, tag_sheet = {}, [], {}
    for etype in types:
        if etype not in wb.sheetnames:
            issues.append(f'Worksheet "{etype}" is missing (was it renamed or deleted?) — this type is ignored')
            continue
        ws = wb[etype]
        hdr = {}
        for c in ws[1]:
            v = _txt(c.value).lower()
            if v in ("tag no", "tag no.", "tag", "tagno"):
                hdr["tag"] = c.column
            elif v in ("location", "loc", "site", "الموقع"):
                hdr["loc"] = c.column
            elif v in ("plan type", "type", "plan"):
                hdr["ptype"] = c.column
            elif v in ("m", "e", "i"):
                hdr[v.upper()] = c.column
            elif v in ("other", "others", "oth", "أخرى", "اخرى"):
                hdr["OTH"] = c.column
            elif v in ("notes", "note", "remarks"):
                hdr["notes"] = c.column
            elif _month_index(v):
                hdr[f"m{_month_index(v)}"] = c.column
        need = ["tag", "ptype", "M", "E", "I"] + [f"m{i}" for i in range(1, 13)]
        miss = [k for k in need if k not in hdr]
        if miss:
            issues.append(f"{etype}: Missing columns in header row ({', '.join(miss)}) — worksheet ignored")
            continue
        rows, seen = [], {}
        for rr in range(2, ws.max_row + 1):
            def cell(k, rr=rr):
                return ws.cell(rr, hdr[k]).value if k in hdr else None
            tag_txt, loc, notes = _txt(cell("tag")), _txt(cell("loc")), _txt(cell("notes"))
            ptxt = _txt(cell("ptype"))
            depts = [d for d in ("M", "E", "I", "OTH") if d in hdr and _is_mark(cell(d))]
            marks = [i for i in range(1, 13) if _is_mark(cell(f"m{i}"))]
            if not (tag_txt or loc or notes or ptxt or depts or marks):
                continue
            where = f"{etype} — Row {rr}" + (f" ({tag_txt})" if tag_txt else "")
            tags = _split_tags(tag_txt)
            if not tags:
                issues.append(f"{where}: Tag No. is missing — not imported")
                continue
            ptype = _PTYPE.get(ptxt.lower())
            if not ptype:
                issues.append(f"{where}: Plan Type «{ptxt or 'blank'}» unknown (Overhaul or PM) — not imported")
                continue
            if not depts:
                issues.append(f"{where}: No maintenance department selected (mark X under M, E, I, or Other) — not imported")
                continue
            if not marks:
                issues.append(f"{where}: No month is marked — not imported")
                continue
            keep = []
            for tag in tags:
                if tag.upper() in tag_sheet and tag_sheet[tag.upper()] != etype:
                    issues.append(f"{where}: Tag {tag} also appears in worksheet {tag_sheet[tag.upper()]} — "
                                  "Equipment can belong to only one equipment type — not imported")
                elif (tag.upper(), ptype) in seen:
                    issues.append(f"{where}: Tag {tag} duplicate for the same plan type (row "
                                  f"{seen[(tag.upper(), ptype)]}) — not imported")
                else:
                    keep.append(tag)
            if not keep:
                continue
            for tag in keep:
                seen[(tag.upper(), ptype)] = rr
                tag_sheet[tag.upper()] = etype
            rows.append({"row": rr, "tags": keep, "location": loc, "ptype": ptype,
                         "depts": depts, "months": marks, "notes": " ".join(notes.split())})
        if rows:
            sheets[etype] = rows
    return {"year": year, "prod_dept": prod, "part": meta.get("part"), "sheets": sheets,
            "issues": issues}


def build_plan_grid_xlsx(year, prod_dept, dept, ptype, eq_type, header, grid):
    """Build a table for one equipment type: equipment × months; the symbol indicates item status."""
    wb = Workbook()
    ws = wb.active
    ws.title = (eq_type or "Plan")[:31]
    for i, w in enumerate([5, 16, 30] + [6] * 12 + [14, 40], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws["A1"] = "Libyan Emirates Oil Refining Company"
    ws["A1"].font = Font(bold=True, size=13)
    ws["Q1"] = "Libyan Emirates Oil Refining Company"
    ws["Q1"].font = Font(bold=True, size=13)
    ws["Q1"].alignment = Alignment(horizontal="right")
    ws["A3"] = f"ANNUAL {SHEET_NAME[ptype].upper()} MAINTENANCE PLAN {year}"
    ws["A3"].font = Font(bold=True, size=14)
    ws["A4"] = (f"Equipment type: {eq_type or '-'}   |   "
                f"Production: {db.dept_en(prod_dept)}   |   Maintenance: {db.plan_dept_en(dept)}"
                + (f"   |   Approved: {header['approved_on'] or '-'}   |   Minutes No: "
                   f"{header['minutes_no'] or '-'}" if header else ""))
    hr = 6
    for i, h in enumerate(["S.N", "Tag No.", "Location"] + MONTHS_EN + ["Shared with", "Notes"], 1):
        c = ws.cell(hr, i, h)
        c.font, c.fill, c.alignment = Font(bold=True), HEAD_FILL, CENTER
    r = hr + 1
    counts = [0] * 12
    for n, g in enumerate(grid, 1):
        ws.cell(r, 1, n).alignment = CENTER
        ws.cell(r, 2, g["tag"]).alignment = CENTER
        ws.cell(r, 3, g["location"]).alignment = LEFT
        for m, it in g["months"].items():
            c = ws.cell(r, 3 + m, "!" if it.get("unexec") else PM_MARK[it["status"]])
            c.fill = PatternFill("solid", fgColor=(
                ("F4B183" if it["decision"] == "KEPT" else "FF9999") if it.get("unexec")
                else PM_FILL[it["status"]]))
            c.alignment = CENTER
            counts[m - 1] += 1
        ws.cell(r, 16, ", ".join(db.plan_dept_en(d) for d in g["shared"]) or None).alignment = CENTER
        ws.cell(r, 17, g["notes"]).alignment = LEFT
        r += 1
    ws.cell(r, 3, "Total").font = Font(bold=True)
    for i, n in enumerate(counts, 1):
        c = ws.cell(r, 3 + i, n or None)
        c.font, c.alignment = Font(bold=True), CENTER
    _box(ws, hr, 1, r, 17)
    ws.cell(r + 2, 1, "● planned  WR request sent  WO work order  ▶ in progress  ✔ completed  "
            "! not executed (red = awaiting decision, orange = kept with reason)"
            ).font = Font(italic=True, size=9)
    ws.cell(r + 4, 1, "Approved by: Division Head").font = Font(bold=True)
    ws.cell(r + 6, 1, "Maintenance Planning Division").font = Font(size=9)
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.freeze_panes = ws.cell(hr + 1, 4)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ----------------------------------------------------------------------------
# Monthly department report (Excel): improvement index, components, then every section
# ----------------------------------------------------------------------------
def build_monthly_xlsx(rep, series=None):
    wb = Workbook()
    ws = wb.active
    ws.title = "Monthly Report"
    for i, w in enumerate([46, 22, 16, 16, 16, 16, 16], 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws["A1"] = "Libyan Emirates Oil Refining Company"
    ws["A1"].font = Font(bold=True, size=13)
    ws["A2"] = f"MONTHLY REPORT — {db.dept_en(rep['dept'])} — {rep['label']}"
    ws["A2"].font = Font(bold=True, size=14)
    r = 4

    def row(vals, bold=False, fill=None):
        nonlocal r
        for i, v in enumerate(vals, 1):
            c = ws.cell(r, i, v)
            c.border, c.font = BORDER, Font(bold=bold)
            c.alignment = LEFT if i == 1 else CENTER
            if fill:
                c.fill = fill
        r += 1

    idx = rep["index"]
    row(["Improvement index (internal — for improving against own history)",
         "—" if idx["value"] is None else idx["value"], idx["band"] or ""], True, HEAD_FILL)
    if series:
        row(["Last months"] + [f"{lab}: {'—' if v is None else v}" for lab, v in series[-5:]])
    if idx["components"]:
        r += 1
        row(["Component", "Result", "Score", "Weight %"], True, HEAD_FILL)
        for c_ in idx["components"]:
            row([c_["label"], c_["raw"], c_["score"], c_["weight"]])
        if idx["weak"]:
            row(["Where to improve: " + " · ".join(idx["weak"])])
    for n_ in rep["notes"]:
        row(["Note: " + n_])
    for sec in rep["sections"]:
        r += 1
        row([sec["title"]], True, HEAD_FILL)
        for a, b in sec["metrics"]:
            row([a, b])
        if sec["table"]:
            row(sec["table"]["cols"], True)
            for x in sec["table"]["rows"]:
                row(list(x))
        if sec.get("note"):
            row([sec["note"]])
    r += 1
    ws.cell(r, 1, "The improvement index is an internal indicator; it is not used to rank departments. "
                  "Targets are set by Planning (Master Data > Report Settings).").font = Font(italic=True, size=9)
    ws.cell(r + 2, 1, "Approved by: Division Head").font = Font(bold=True)
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()

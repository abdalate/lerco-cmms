# -*- coding: utf-8 -*-
"""
LERCO CMMS - طبقة قاعدة البيانات (SQLite + WAL)
قسم التخطيط والبرمجة
"""
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta

DB_PATH = os.environ.get(
    "CMMS_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cmms.db"),
)

# ----------------------------------------------------------------------------
# الثوابت المعتمدة
# ----------------------------------------------------------------------------
# (الرمز, الاسم المعروض EN, الاسم AR, النوع)
DEPARTMENTS = [
    ("REF", "Refinery", "التكرير", "PRODUCTION"),
    ("OM", "Oil Movement", "حركة الزيت", "PRODUCTION"),
    ("M", "Mechanical", "الصيانة الميكانيكية", "MAINTENANCE"),
    ("E", "Electrical", "الصيانة الكهربائية", "MAINTENANCE"),
    ("I", "Instrumentation", "الآلات الدقيقة", "MAINTENANCE"),
    ("PLN", "Planning", "التخطيط والبرمجة", "SUPPORT"),
    ("INSP", "Inspection", "التفتيش الفني", "SUPPORT"),
]

# الرمز الذي يظهر داخل رقم المستند. لتغييره إلى MECH/ELEC/INST عدّل هنا فقط.
NUM_CODE = {"REF": "REF", "OM": "OM", "M": "M", "E": "E", "I": "I",
            "PLN": "PLN", "INSP": "INSP"}

STATUS = {"P": "Pending", "O": "In Progress", "C": "Completed"}
# سبب إغلاق بطاقة العمل السريع قبل إنجازها (يغلقها قسم الصيانة، أو النظام آخر اليوم)
JC_CLOSE_REASONS = {"PARTS": "Spare parts needed", "WELD": "Welding / hot work needed",
                    "EQUIP": "Crane / special equipment needed", "SCAFFOLD": "Scaffolding needed",
                    "EXTERNAL": "Contractor / external service needed",
                    "OTHER": "Other (needs Planning)",
                    "AUTO": "Closed automatically — not completed by end of day"}
# إعدادات التقارير الشهرية: المفتاح → (القيمة الافتراضية, الوصف)
SETTINGS_DEFAULT = {
    "backlog_age_days": ("14", "Open Work Order age considered overdue (days)"),
    "response_target_h": ("24", "Planning response target to a Work Request (hours)"),
    "shift_hours": ("5", "Productive hours per worker per day (utilization)"),
    "util_target": ("80", "Manpower utilization target (%)"),
    "planned_target": ("40", "Planned work (PM+PD) target (% of hours)"),
    "accept_target_days": ("3", "Requester acceptance target after completion (days)"),
    "study_target_days": ("2", "Inspection study target after issue (days)"),
    "insp_target_days": ("10", "Inspection report target after issue (days)"),
    "dispatch_target_days": ("2", "Planning dispatch target after report submission (days)"),
    "jc_cutoff": ("23:59", "Job Card automatic closure time (HH:MM)")}
CATEGORY = {"CM": "Corrective (CM)", "PM": "Preventive (PM)", "PD": "Predictive (PD)"}
PRIORITY = {"Normal": "Normal", "Urgent": "Urgent",
            "Shut down": "Shutdown", "Emergency": "Emergency"}
SERVICE_TYPES = {"CRANE": "Crane", "FORKLIFT": "Forklift",
                 "SCAFFOLD": "Scaffolding", "TRANSPORT": "Transport",
                 "PAINT": "Painting", "WELD": "Welding", "OTHER": "Other"}
WR_STATUS = {"NEW": "New", "STUDY": "Under Review", "CONVERTED": "Converted to Work Order",
             "SERVICE": "Converted to Service Request", "REJECTED": "Rejected"}
TIR_SCOPE = {"VISUAL": "Visual Inspection", "THICKNESS": "Thickness Measurement",
             "NDT": "Non-Destructive Testing (NDT)", "HYDRO": "Hydrostatic Pressure Test",
             "GENERAL": "General Inspection", "OTHER": "Other"}
TIR_STATUS = {"P": "Awaiting Inspection", "O": "Inspection in Progress", "C": "Report Issued"}
# نموذج طلب/تقرير تفتيش عام: أنواع الفحص تُحدد أقسام التقرير الظاهرة
INSP_TYPES = {"VISUAL": "General visual inspection", "UT": "Thickness measurement (UT)",
              "NDT": "Non-destructive testing (NDT)", "CUI": "CUI inspection",
              "WELD": "Welding method / weld inspection", "COATING": "Coating specification / inspection",
              "HYDRO": "Hydrostatic test", "PSV": "Safety valve test",
              "SHUTDOWN": "Shutdown / final inspection"}
TIR_SCOPE.update(INSP_TYPES)
TIR_REASONS = {"PERIODIC": "Periodic inspection", "AFTER_REPAIR": "After repair",
               "SHUTDOWN": "During shutdown", "FFP": "Fitness for purpose",
               "PLAN": "Annual plan item", "OTHER": "Other"}
EQUIP_STATES = {"IN_SERVICE": "In service", "SHUTDOWN": "Shut down / isolated"}
# متطلبات يحددها التفتيش بعد دراسة الطلب ويصدر التخطيط مستنداتها (SR أو WO)
REQ_KINDS = {"SCAFFOLD": "Scaffolding", "OPEN": "Open tank / vessel / manway",
             "CLEAN": "Surface cleaning", "INSUL": "Remove thermal insulation",
             "ISOLATE": "Operational isolation (blinds / draining)", "OTHER": "Other"}
TIR_STAGES = {"STUDY": "Awaiting Inspection study", "ISSUE": "Awaiting Planning documents",
              "PREP": "Preparations in progress", "READY": "Ready for inspection",
              "INSP": "Inspection in progress", "SEND": "Awaiting dispatch to requester",
              "SENT": "Sent to requester"}

MONTHS_EN = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
# Legacy month names retained for importing older Arabic Excel templates.
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
             "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
# تسميات عربية للأنواع القديمة فقط؛ نوع المعدة الآن نص حر (يكتبه الفني عند تنزيل القالب)
EQ_CLASSES = {"PUMP": "Pumps", "SAFETY_VALVE": "Safety Valves", "CONTROL_VALVE": "Control Valves",
              "MOTOR": "Electric Motors", "COMPRESSOR": "Compressors", "VALVE": "Valves",
              "HEX": "Heat Exchangers", "TANK": "Tanks", "INSTR": "Instrumentation",
              "OTHER": "Other"}
CONDITION = {"GOOD": "Good", "FAIR": "Fair", "POOR": "Poor"}
PLAN_TYPES = {"OVERHAUL": "Overhaul", "PM": "Preventive Maintenance (PM)"}
# معيار اختيار المعدات للخطة: ساعات التشغيل منذ آخر عمرة + الاهتزاز + حالة التفتيش
PM_WEIGHTS = {"hours": 0.5, "vib": 0.3, "cond": 0.2}
VIB_ALARM = 7.1   # mm/s: حد الإنذار الافتراضي للاهتزاز (يُعدَّل هنا)
PM_SYMBOL = {"PLANNED": "🟦", "REQ": "📨", "WO": "📋", "O": "🔧", "C": "✅", "REJ": "⛔"}
PM_STATUS_AR = {"PLANNED": "Planned", "REQ": "Work Request Sent", "WO": "Work Order Issued",
                "O": "In Progress", "C": "Completed", "REJ": "Work Request Rejected"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS departments(
    code TEXT PRIMARY KEY, name_en TEXT NOT NULL,
    name_ar TEXT NOT NULL, kind TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS sequences(
    doc_type TEXT NOT NULL, dept TEXT NOT NULL, yy INTEGER NOT NULL,
    last_no INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (doc_type, dept, yy));

-- المعدة إما برقم (Tag) أو باسمها الهندسي/الصناعي مع موقعها
CREATE TABLE IF NOT EXISTS equipment(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tag_no TEXT UNIQUE, name TEXT DEFAULT '', location TEXT DEFAULT '',
    cost_center TEXT DEFAULT '', description TEXT DEFAULT '',
    eq_class TEXT, maint_dept TEXT, run_hours REAL, overhaul_interval REAL,
    last_overhaul_hours REAL, last_overhaul_date TEXT, vibration REAL,
    condition TEXT, readings_date TEXT, op_dept TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_equipment_name_loc
    ON equipment(name, location) WHERE tag_no IS NULL;

CREATE TABLE IF NOT EXISTS work_requests(
    doc_no TEXT PRIMARY KEY, doc_date TEXT NOT NULL,
    requesting_dept TEXT NOT NULL, exec_dept TEXT NOT NULL,
    tag_no TEXT, equipment_name TEXT, location TEXT,
    description TEXT NOT NULL, priority TEXT DEFAULT 'Normal',
    contact TEXT, status TEXT NOT NULL DEFAULT 'NEW', linked_wo TEXT, linked_sr TEXT,
    parent_job TEXT, need TEXT, plan_ref TEXT, plan_code TEXT,
    legacy_no TEXT, submitted_at TEXT, reviewed_at TEXT, reject_reason TEXT,
    group_no TEXT, src_tir TEXT, src_jc TEXT,
    cost_center TEXT, materials TEXT, special_equip TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);

-- بطاقات العمل السريع وأوامر العمل في جدول واحد (التقرير اليومي يعرضهما معاً)
CREATE TABLE IF NOT EXISTS jobs(
    doc_no TEXT PRIMARY KEY,
    doc_type TEXT NOT NULL CHECK (doc_type IN ('JC','WO')),
    doc_date TEXT NOT NULL, wr_no TEXT,
    requesting_dept TEXT, exec_dept TEXT NOT NULL,
    cost_center TEXT, tag_no TEXT, equipment_name TEXT, location TEXT,
    description TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'CM' CHECK (category IN ('CM','PM','PD')),
    priority TEXT DEFAULT 'Normal', est_hours REAL, contact TEXT,
    planning_note TEXT,
    need_crane INTEGER DEFAULT 0, need_forklift INTEGER DEFAULT 0,
    need_scaffold INTEGER DEFAULT 0, need_transport INTEGER DEFAULT 0,
    stock_checked INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'P' CHECK (status IN ('P','O','C')),
    start_date TEXT, end_date TEXT, completed_at TEXT, progress INTEGER DEFAULT 0,
    remarks TEXT, legacy_no TEXT, accepted_date TEXT, accepted_by TEXT,
    auto_closed_at TEXT,
    group_no TEXT, cat_set INTEGER DEFAULT 1, plan_code TEXT,
    close_reason TEXT, close_note TEXT,   -- إغلاق بطاقة سريعة لحاجتها للتخطيط أو تلقائياً
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_jobs_dept_status ON jobs(exec_dept, status);
CREATE INDEX IF NOT EXISTS ix_jobs_group ON jobs(group_no);

-- العمل الرئيسي: يجمع طلبات وأوامر تخص نفس العمل (قد تنفذها عدة أقسام)
CREATE TABLE IF NOT EXISTS work_groups(
    group_no TEXT PRIMARY KEY, title TEXT NOT NULL, doc_date TEXT NOT NULL,
    requesting_dept TEXT, tag_no TEXT, equipment_name TEXT, location TEXT,
    status TEXT NOT NULL DEFAULT 'P' CHECK (status IN ('P','O','C')),
    start_date TEXT, end_date TEXT, remarks TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);

-- تسجيل ساعات العمل اليومية (أعداد وساعات بدون أسماء حالياً)
CREATE TABLE IF NOT EXISTS labor_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_no TEXT NOT NULL REFERENCES jobs(doc_no),
    work_date TEXT NOT NULL, workers INTEGER DEFAULT 0,
    spv_hours REAL DEFAULT 0, tech_hours REAL DEFAULT 0,
    ot_hours REAL DEFAULT 0, cont_hours REAL DEFAULT 0, note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_labor_job_date ON labor_log(job_no, work_date);

CREATE TABLE IF NOT EXISTS spare_parts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_no TEXT NOT NULL REFERENCES jobs(doc_no),
    stock_no TEXT, description TEXT, unit TEXT,
    qty REAL DEFAULT 0, returned REAL DEFAULT 0);

CREATE TABLE IF NOT EXISTS service_requests(
    doc_no TEXT PRIMARY KEY, doc_date TEXT NOT NULL,
    requesting_dept TEXT, job_no TEXT, tag_no TEXT,
    equipment_name TEXT, location TEXT,
    service_type TEXT NOT NULL, description TEXT NOT NULL,
    from_date TEXT, to_date TEXT, est_hours REAL, category TEXT,
    contact TEXT, planning_note TEXT,
    status TEXT NOT NULL DEFAULT 'P' CHECK (status IN ('P','O','C')),
    remarks TEXT, legacy_no TEXT, wr_no TEXT, plan_code TEXT, executor TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);

-- تقارير التفتيش الفني TIR: يصدرها التخطيط (PLN) بناءً على WR ويُكملها التفتيش (INSP)
CREATE TABLE IF NOT EXISTS inspection_reports(
    doc_no TEXT PRIMARY KEY, doc_date TEXT NOT NULL,
    wr_no TEXT,              -- طلب العمل المرتبط (اختياري)
    job_no TEXT,             -- أمر العمل المرتبط (اختياري)
    tag_no TEXT, equipment_name TEXT DEFAULT '', location TEXT DEFAULT '',
    scope TEXT,              -- نطاق التفتيش (VISUAL/NDT/HYDRO/THICKNESS/GENERAL/OTHER)
    requesting_note TEXT,    -- ملاحظة التخطيط عند الإصدار
    wps_spec TEXT,           -- المواصفات التقنية (يملؤها INSP)
    coating_system TEXT,     -- نظام الطلاء (يملؤها INSP)
    findings TEXT,           -- الملاحظات والنتائج (يملؤها INSP)
    recommendations TEXT,    -- التوصيات (يملؤها INSP)
    status TEXT NOT NULL DEFAULT 'P' CHECK(status IN ('P','O','C')),
    form_json TEXT,          -- النموذج الهندسي المنظم (JSON) - انظر tir_form.py
    reason TEXT, insp_types TEXT,   -- سبب التفتيش وأنواع الفحص المطلوبة (رموز مفصولة بفاصلة)
    priority TEXT, due_date TEXT, equip_state TEXT,
    reqs_json TEXT,          -- متطلبات التفتيش (سقالات، فتح وعاء ...) ومستنداتها
    studied_at TEXT,         -- وقت دراسة التفتيش للطلب وإرساله المتطلبات
    sent_at TEXT, sent_by TEXT, sent_with_recs INTEGER,   -- إرسال التخطيط للتقرير للجهة الطالبة
    submitted_at TEXT,       -- توقيت رفع التقرير من INSP
    plan_code TEXT,          -- كود بند الخطة السنوية (إن نشأ منها)
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_tir_wr ON inspection_reports(wr_no);

-- ساعات العمل اليومية لطلبات الخدمة (إجمالي ساعات اليوم لكل طلب)
CREATE TABLE IF NOT EXISTS sr_labor_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sr_no TEXT NOT NULL REFERENCES service_requests(doc_no),
    work_date TEXT NOT NULL, workers INTEGER DEFAULT 0,
    hours REAL DEFAULT 0, note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS ix_srlabor ON sr_labor_log(sr_no, work_date);

-- المخزون / المواد المستهلكة في طلبات الخدمة
CREATE TABLE IF NOT EXISTS sr_materials(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sr_no TEXT NOT NULL REFERENCES service_requests(doc_no),
    stock_no TEXT, description TEXT, unit TEXT,
    qty REAL DEFAULT 0, returned REAL DEFAULT 0);

-- الخطة السنوية للصيانة الشاملة / الوقائية: صف لكل معدة وشهر تنفيذها
CREATE TABLE IF NOT EXISTS pm_schedule(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL, dept TEXT NOT NULL, plan_type TEXT NOT NULL,
    eq_class TEXT, equipment_id INTEGER NOT NULL REFERENCES equipment(id),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    score REAL, reason TEXT, wr_no TEXT, note TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(year, plan_type, equipment_id));
CREATE INDEX IF NOT EXISTS ix_pm_plan ON pm_schedule(year, dept, plan_type);

-- الخطة السنوية المعتمدة (تُستورد من قالب Excel): رأس الخطة لكل (سنة، قسم إنتاج)
CREATE TABLE IF NOT EXISTS plan_headers(
    year INTEGER NOT NULL, prod_dept TEXT NOT NULL,
    approved_on TEXT, minutes_no TEXT, imported_at TEXT, imported_by TEXT,
    PRIMARY KEY (year, prod_dept));
-- بنود الخطة: صف لكل (معدة، شهر) ولكل قسم صيانة ونوع خطة
CREATE TABLE IF NOT EXISTS plan_items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year INTEGER NOT NULL, prod_dept TEXT NOT NULL, dept TEXT NOT NULL,
    plan_type TEXT NOT NULL, equipment_id INTEGER NOT NULL REFERENCES equipment(id),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    notes TEXT, wr_no TEXT, decision TEXT, decision_note TEXT, eq_type TEXT,
    item_key TEXT, plan_code TEXT,
    UNIQUE(year, prod_dept, dept, plan_type, equipment_id, month));
CREATE INDEX IF NOT EXISTS ix_plan_items ON plan_items(year, prod_dept, dept, plan_type);

-- الجهات المنفذة لطلبات الخدمة (شركات ومقاولون وورش): قائمة تتسع بالإضافة في آخرها
CREATE TABLE IF NOT EXISTS executors(
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP);

-- سجل زمني لكل إجراء على أي مستند (يظهر للجهة الطالبة)
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY AUTOINCREMENT, doc_no TEXT NOT NULL, ts TEXT NOT NULL,
    actor TEXT, event TEXT NOT NULL, note TEXT);
CREATE INDEX IF NOT EXISTS ix_events_doc ON events(doc_no, id);

-- إعدادات يضبطها المخطط (أهداف وحدود التقارير الشهرية ووقت الإغلاق التلقائي)
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- القوى العاملة اليومية لكل قسم (أعداد فقط)
CREATE TABLE IF NOT EXISTS manpower_daily(
    report_date TEXT NOT NULL, dept TEXT NOT NULL,
    approved INTEGER DEFAULT 0, supervisors INTEGER DEFAULT 0,
    technicians INTEGER DEFAULT 0, leave_n INTEGER DEFAULT 0,
    absent INTEGER DEFAULT 0, sick INTEGER DEFAULT 0,
    in_train INTEGER DEFAULT 0, ex_train INTEGER DEFAULT 0,
    shift_rest INTEGER DEFAULT 0, other INTEGER DEFAULT 0,
    contract INTEGER DEFAULT 0,
    PRIMARY KEY (report_date, dept));
"""


# ----------------------------------------------------------------------------
# الاتصال والمعاملات
# ----------------------------------------------------------------------------
INDEXES = """
CREATE INDEX IF NOT EXISTS ix_wr_status   ON work_requests(status, doc_date);
CREATE INDEX IF NOT EXISTS ix_wr_date     ON work_requests(doc_date);
CREATE INDEX IF NOT EXISTS ix_wr_req      ON work_requests(requesting_dept, doc_date);
CREATE INDEX IF NOT EXISTS ix_wr_group    ON work_requests(group_no);
CREATE INDEX IF NOT EXISTS ix_wr_parent   ON work_requests(parent_job);
CREATE INDEX IF NOT EXISTS ix_wr_plancode ON work_requests(plan_code);
CREATE INDEX IF NOT EXISTS ix_jobs_wr     ON jobs(wr_no);
CREATE INDEX IF NOT EXISTS ix_jobs_type   ON jobs(doc_type, status);
CREATE INDEX IF NOT EXISTS ix_jobs_req    ON jobs(doc_type, requesting_dept);
CREATE INDEX IF NOT EXISTS ix_jobs_dept_date ON jobs(exec_dept, doc_date);
CREATE INDEX IF NOT EXISTS ix_jobs_plan   ON jobs(plan_code);
CREATE INDEX IF NOT EXISTS ix_sr_wr       ON service_requests(wr_no);
CREATE INDEX IF NOT EXISTS ix_sr_job      ON service_requests(job_no, status);
CREATE INDEX IF NOT EXISTS ix_sr_status   ON service_requests(status);
CREATE INDEX IF NOT EXISTS ix_sr_plan     ON service_requests(plan_code);
CREATE INDEX IF NOT EXISTS ix_tir_status  ON inspection_reports(status);
CREATE INDEX IF NOT EXISTS ix_tir_plan    ON inspection_reports(plan_code);
CREATE INDEX IF NOT EXISTS ix_labor_date  ON labor_log(work_date);
CREATE INDEX IF NOT EXISTS ix_srlabor_dt  ON sr_labor_log(work_date);
CREATE INDEX IF NOT EXISTS ix_spare_job   ON spare_parts(job_no);
CREATE INDEX IF NOT EXISTS ix_srmat_sr    ON sr_materials(sr_no);
CREATE INDEX IF NOT EXISTS ix_pi_code     ON plan_items(plan_code);
CREATE INDEX IF NOT EXISTS ix_pi_wr       ON plan_items(wr_no);
CREATE INDEX IF NOT EXISTS ix_pi_eq       ON plan_items(equipment_id);
CREATE INDEX IF NOT EXISTS ix_events_ev   ON events(event, doc_no);
"""

PROFILE = os.environ.get("CMMS_PROFILE") == "1"   # CMMS_PROFILE=1 يطبع الاستعلامات الأبطأ من 50ms


def get_conn():
    # وضع WAL يُحفظ داخل ملف القاعدة، ويُفعَّل مرة واحدة في init_db()
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA cache_size=-65536")        # 64MB لكل اتصال
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=268435456")      # 256MB
    return conn


@contextmanager
def tx():
    """معاملة كتابة ذرّية (BEGIN IMMEDIATE) تمنع تكرار الأرقام التسلسلية."""
    conn = get_conn()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


_tl = threading.local()   # اتصال قراءة واحد لكل خيط بدل فتح اتصال جديد لكل استعلام


def query(sql, params=()):
    conn = getattr(_tl, "conn", None)
    if conn is None:
        conn = _tl.conn = get_conn()
    t0 = time.perf_counter() if PROFILE else 0
    try:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.Error:
        try:
            conn.close()
        except sqlite3.Error:
            pass
        _tl.conn = None
        raise
    if PROFILE:
        ms = (time.perf_counter() - t0) * 1000
        if ms > 50:
            print(f"[SLOW] {ms:.0f} ms  {' '.join(sql.split())[:110]}")
    return rows


_ver_lock = threading.Lock()
_ver_conn = None


def data_version():
    """رقم يتغير عند أي كتابة على القاعدة (من أي مستخدم). يُستخدم كمفتاح للتخزين المؤقت."""
    global _ver_conn
    with _ver_lock:
        if _ver_conn is None:
            _ver_conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False,
                                        isolation_level=None)
        return _ver_conn.execute("PRAGMA data_version").fetchone()[0]


def _cols(conn, table):
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def _migrate(conn):
    """ترقية قاعدة بيانات قديمة (قبل دعم المعدات بدون رقم) دون فقدان بيانات."""
    eq = _cols(conn, "equipment")
    if eq and "id" not in eq:
        conn.execute("ALTER TABLE equipment RENAME TO equipment_old")
        conn.execute("""CREATE TABLE equipment(
            id INTEGER PRIMARY KEY AUTOINCREMENT, tag_no TEXT UNIQUE,
            name TEXT DEFAULT '', location TEXT DEFAULT '',
            cost_center TEXT DEFAULT '', description TEXT DEFAULT '')""")
        conn.execute("""INSERT INTO equipment(tag_no,description,cost_center,location)
            SELECT NULLIF(TRIM(tag_no),''), COALESCE(description,''),
                   COALESCE(cost_center,''), COALESCE(location,'')
            FROM equipment_old""")
        conn.execute("DROP TABLE equipment_old")
    have = _cols(conn, "equipment")
    for c, t in (("eq_class", "TEXT"), ("maint_dept", "TEXT"), ("run_hours", "REAL"),
                 ("overhaul_interval", "REAL"), ("last_overhaul_hours", "REAL"),
                 ("last_overhaul_date", "TEXT"), ("vibration", "REAL"),
                 ("condition", "TEXT"), ("readings_date", "TEXT"),
                 ("op_dept", "TEXT")):
        if have and c not in have:
            conn.execute(f"ALTER TABLE equipment ADD COLUMN {c} {t}")
    for table, extra in (("jobs", ["equipment_name", "accepted_date", "accepted_by", "auto_closed_at", "completed_at", "group_no",
                                   "plan_code", "close_reason", "close_note"]),
                         ("work_requests", ["equipment_name", "location", "submitted_at",
                                            "reviewed_at", "reject_reason", "group_no", "linked_sr",
                                        "parent_job", "need", "plan_ref", "plan_code", "src_tir", "src_jc",
                                        "cost_center", "materials", "special_equip"]),
                         ("service_requests", ["equipment_name", "location", "wr_no", "plan_code",
                              "executor"])):
        have = _cols(conn, table)
        if have:
            for c in extra:
                if c not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} TEXT")
    have = _cols(conn, "jobs")
    if have and "cat_set" not in have:   # الموجود سابقاً يُعتبر مصنَّفاً
        conn.execute("ALTER TABLE jobs ADD COLUMN cat_set INTEGER DEFAULT 1")
    # ترقية جدول inspection_reports إلى الهيكل الجديد
    have = _cols(conn, "inspection_reports")
    if have:
        for c in ["wr_no", "equipment_name", "location", "recommendations",
                  "requesting_note", "submitted_at", "plan_code", "form_json", "reason",
                  "insp_types", "priority", "due_date", "equip_state", "reqs_json",
                  "studied_at", "sent_at", "sent_by", "sent_with_recs"]:
            if c not in have:
                conn.execute(f"ALTER TABLE inspection_reports ADD COLUMN {c} TEXT")
        if "status" not in have:
            conn.execute("ALTER TABLE inspection_reports ADD COLUMN status TEXT DEFAULT 'C'")
            conn.execute(
                "UPDATE inspection_reports SET status=CASE"
                " WHEN findings IS NOT NULL THEN 'C' ELSE 'P' END")


def _migrate_plans(conn):
    have = _cols(conn, "plan_items")
    if have and "eq_type" not in have:
        conn.execute("ALTER TABLE plan_items ADD COLUMN eq_type TEXT")
    if have and "item_key" not in have:
        conn.execute("ALTER TABLE plan_items ADD COLUMN item_key TEXT")
    if have and "plan_code" not in have:
        conn.execute("ALTER TABLE plan_items ADD COLUMN plan_code TEXT")


def _migrate_admin(conn):
    """Create isolated admin authentication, sessions, approvals and audit tables."""
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS admin_accounts(
        username TEXT PRIMARY KEY, salt BLOB NOT NULL, password_hash BLOB NOT NULL,
        iterations INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS admin_sessions(
        token_hash TEXT PRIMARY KEY, username TEXT NOT NULL REFERENCES admin_accounts(username),
        expires_at TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        revoked_at TEXT);
    CREATE TABLE IF NOT EXISTS department_approvals(
        dept TEXT PRIMARY KEY, approved INTEGER NOT NULL CHECK(approved >= 0),
        updated_by TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS admin_audit_log(
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
        action TEXT NOT NULL, dept TEXT, old_value TEXT, new_value TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS ix_admin_audit_created ON admin_audit_log(created_at);
    """)


def init_db():
    conn = get_conn()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _migrate(conn)
        _migrate_plans(conn)
        _migrate_admin(conn)
        conn.executescript(SCHEMA)
        # Preserve the latest legacy Approved value when migrating existing daily manpower records.
        if _cols(conn, "manpower_daily"):
            conn.execute(
                "INSERT OR IGNORE INTO department_approvals(dept,approved,updated_by) "
                "SELECT m.dept, m.approved, 'MIGRATION' FROM manpower_daily m WHERE m.report_date=(SELECT MAX(m2.report_date) FROM manpower_daily m2 WHERE m2.dept=m.dept)")
        conn.executescript(INDEXES)
        conn.execute("BEGIN IMMEDIATE")
        try:
            _backfill_plan_codes(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("UPDATE service_requests SET service_type='OTHER'"
                     " WHERE service_type='CONTRACTOR'")
        conn.executemany(
            "INSERT OR IGNORE INTO departments VALUES (?,?,?,?)", DEPARTMENTS)
        conn.execute("PRAGMA optimize")
    finally:
        conn.close()


# ساعة النظام (قابلة للاستبدال في سكربت البيانات التجريبية)
NOW_FN = datetime.now


def _stamp():
    return NOW_FN().strftime("%Y-%m-%d %H:%M")


# ----------------------------------------------------------------------------
# الأقسام
# ----------------------------------------------------------------------------
def dept_codes(kind=None):
    return [d[0] for d in DEPARTMENTS if kind is None or d[3] == kind]


def dept_label(code):
    for c, en, ar, _ in DEPARTMENTS:
        if c == code:
            return en
    return code or ""


def plan_dept_en(code):
    """اسم القسم المنفذ في الخطة: M/E/I أو OTH (جهات خارجية: شركات أخرى غير أقسام الصيانة)."""
    return "Other" if code == "OTH" else dept_en(code)


def plan_dept_label(code):
    return "Other - External Contractor" if code == "OTH" else dept_label(code)


def dept_en(code):
    for c, en, _, _ in DEPARTMENTS:
        if c == code:
            return en
    return code or ""


# ----------------------------------------------------------------------------
# الترقيم الذكي: [النوع]-[الجهة المنفذة]-[السنة]-[التسلسل]
# ----------------------------------------------------------------------------
def _next_number(conn, doc_type, dept, doc_date):
    yy = doc_date.year % 100
    conn.execute(
        "INSERT OR IGNORE INTO sequences(doc_type,dept,yy,last_no) VALUES(?,?,?,0)",
        (doc_type, dept, yy))
    conn.execute(
        "UPDATE sequences SET last_no=last_no+1 WHERE doc_type=? AND dept=? AND yy=?",
        (doc_type, dept, yy))
    n = conn.execute(
        "SELECT last_no FROM sequences WHERE doc_type=? AND dept=? AND yy=?",
        (doc_type, dept, yy)).fetchone()[0]
    return f"{doc_type}-{NUM_CODE.get(dept, dept)}-{yy:02d}-{n:03d}"


def _clean(x):
    return (x or "").strip()


def validate_equipment(tag, name, location):
    """المعدة تُحدَّد برقمها (Tag No.)، أو باسمها الهندسي/الصناعي مع موقعها."""
    tag, name, location = _clean(tag), _clean(name), _clean(location)
    if not tag and not (name and location):
        raise ValueError("Specify the equipment by Tag No. or by equipment name and location")
    return tag, name, location


def equipment_label(tag, name, location):
    tag, name, location = _clean(tag), _clean(name), _clean(location)
    if tag:
        return f"{tag} - {name}" if name else tag
    return f"{name} ({location})" if location else name


def _upsert_equipment(conn, tag, name="", location="", cost_center=""):
    tag, name, location, cost_center = map(_clean, (tag, name, location, cost_center))
    if tag:
        if conn.execute("SELECT 1 FROM equipment WHERE tag_no=?", (tag,)).fetchone():
            conn.execute(
                "UPDATE equipment SET name=COALESCE(NULLIF(?,''),name),"
                " location=COALESCE(NULLIF(?,''),location),"
                " cost_center=COALESCE(NULLIF(?,''),cost_center) WHERE tag_no=?",
                (name, location, cost_center, tag))
        else:
            conn.execute("INSERT INTO equipment(tag_no,name,location,cost_center)"
                         " VALUES(?,?,?,?)", (tag, name, location, cost_center))
    elif name and location:
        conn.execute("INSERT OR IGNORE INTO equipment(tag_no,name,location,cost_center)"
                     " VALUES(NULL,?,?,?)", (name, location, cost_center))


def save_equipment(tag, name, location, cost_center="", **extra):
    """يحفظ معدة؛ extra: eq_class, maint_dept, overhaul_interval, run_hours ..."""
    tag, name, location = validate_equipment(tag, name, location)
    with tx() as conn:
        _upsert_equipment(conn, tag, name, location, cost_center)
        row = (conn.execute("SELECT id FROM equipment WHERE tag_no=?", (tag,)).fetchone() if tag
               else conn.execute("SELECT id FROM equipment WHERE tag_no IS NULL AND name=?"
                                 " AND location=?", (name, location)).fetchone())
    if extra and row:
        update_equipment_data(row["id"], **extra)
    return row["id"] if row else None


def list_equipment():
    rows = query("SELECT * FROM equipment ORDER BY COALESCE(tag_no,''), name")
    for r in rows:
        r["label"] = equipment_label(r["tag_no"], r["name"], r["location"])
    return rows


def log_event(conn, doc_no, event, actor=None, note=None):
    conn.execute("INSERT INTO events(doc_no,ts,actor,event,note) VALUES(?,?,?,?,?)",
                 (doc_no, _stamp(), actor, event, note))


def _today():
    """تاريخ الإصدار: دائماً تاريخ النظام الحالي (لا يقبل أي تاريخ من المستخدم)."""
    return NOW_FN().date()


def _iso(d):
    return d.isoformat() if isinstance(d, date) else d


def _as_date(d):
    return d if isinstance(d, date) else date.fromisoformat(d)


# ----------------------------------------------------------------------------
# طلبات العمل WR
# ----------------------------------------------------------------------------
def create_wr(doc_date, requesting_dept, exec_dept, description, tag_no=None,
              priority="Normal", contact=None, legacy_no=None,
              equipment_name=None, location=None, actor=None, parent_job=None,
              need=None, plan_ref=None, plan_code=None, cost_center=None, materials=None,
              special_equip=None):
    """طلب عمل من أي قسم في الشركة. الجهة المنفذة اختيارية (فارغة = غير محددة)،
    و need = المطلوب (نص يصف ما هو مطلوب).
    طلب الخدمة (SR) نفسه يصدره التخطيط وحده لأن الخدمات تأتي من خارج الشركة."""
    doc_date = _today()   # تاريخ الإصدار مثبّت على تاريخ اليوم ولا يُعدَّل
    maint = dept_codes("MAINTENANCE")
    exec_dept = exec_dept or ""
    if exec_dept:
        if exec_dept not in maint:
            raise ValueError("Executing department must be M, E, or I")
        if requesting_dept in maint and requesting_dept == exec_dept:
            raise ValueError("A Work Request between Maintenance departments must be directed to another Maintenance department")
    need = (need or "").strip() or None
    if not (description or "").strip():
        if need:
            description = need
        else:
            raise ValueError("Description is required")
    group_no = None
    if parent_job:   # طلب ناشئ أثناء تنفيذ أمر قائم: يرث المعدة والعمل الرئيسي
        p = query("SELECT * FROM jobs WHERE doc_no=?", (parent_job,))
        if not p:
            raise ValueError("Parent job does not exist")
        p = p[0]
        if p.get("plan_code") and requesting_dept in maint:
            # العمل المخطط من الخطة السنوية: التخطيط مسؤول عن كل أوامره وخدماته وتفتيشه
            raise ValueError(
                f"This job is scheduled in the Annual Plan (item code {p['plan_code']}): Planning & Programming "
                "is responsible for all required Work Orders, Service Requests, and Inspections. Maintenance departments must not submit requests for this item "
                "— inform Planning of your requirements")
        tag_no = tag_no or p["tag_no"]
        equipment_name = equipment_name or p["equipment_name"]
        location = location or p["location"]
        group_no = p["group_no"]
    tag_no, equipment_name, location = validate_equipment(tag_no, equipment_name, location)
    with tx() as conn:
        no = _next_number(conn, "WR", requesting_dept, _as_date(doc_date))
        conn.execute(
            "INSERT INTO work_requests(doc_no,doc_date,requesting_dept,exec_dept,"
            "tag_no,equipment_name,location,description,priority,contact,legacy_no,"
            "parent_job,group_no,need,plan_ref,plan_code) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (no, _iso(doc_date), requesting_dept, exec_dept, tag_no or None,
             equipment_name, location, description.strip(), priority, contact, legacy_no,
             parent_job or None, group_no, need or None, plan_ref or None,
             plan_code or None))
        if not plan_ref:     # طلب الخطة قد يضم عدة معدات: لا نعدّل بيانات المعدة المسجلة
            _upsert_equipment(conn, tag_no, equipment_name, location)
        conn.execute("UPDATE work_requests SET submitted_at=?, cost_center=?, materials=?,"
                     " special_equip=? WHERE doc_no=?",
                     (_stamp(), _clean(cost_center) or None, _clean(materials) or None,
                      _clean(special_equip) or None, no))
        log_event(conn, no, "WR_CREATED", actor or requesting_dept,
                  f"Regarding {parent_job}" if parent_job else None)
    return no


# ----------------------------------------------------------------------------
# حالات الغلق والقفل النهائي للمستندات
# ----------------------------------------------------------------------------
def is_job_closed(doc_no):
    """أمر العمل يعتبر مقفلاً عند اكتماله وتأكيد استلامه من الجهة الطالبة."""
    j = query("SELECT status, accepted_date, auto_closed_at FROM jobs WHERE doc_no=?", (doc_no,))
    return bool(j and j[0]["status"] == "C"
                and (j[0].get("accepted_date") or j[0].get("auto_closed_at")))


def is_sr_closed(doc_no):
    """طلب الخدمة يعتبر مقفلاً عند اكتماله."""
    s = query("SELECT status FROM service_requests WHERE doc_no=?", (doc_no,))
    return bool(s and s[0]["status"] == "C")


def is_tir_closed(doc_no):
    """طلب التفتيش يعتبر مقفلاً عند صدور التقرير واكتماله."""
    t = query("SELECT status FROM inspection_reports WHERE doc_no=?", (doc_no,))
    return bool(t and t[0]["status"] == "C")


def is_wr_closed(wr_no):
    """يتحقق مما إذا كان طلب العمل مغلقاً (مرفوض أو جميع مستنداته الصادرة مقفلة)."""
    if not wr_no:
        return False
    w = query("SELECT status FROM work_requests WHERE doc_no=?", (wr_no,))
    if not w:
        return False
    st = w[0]["status"]
    if st == "REJECTED":
        return True
    if st in ("CONVERTED", "SERVICE"):
        jobs, srs = wr_children(wr_no)
        tirs = query("SELECT status FROM inspection_reports WHERE wr_no=?", (wr_no,))
        if not (jobs or srs or tirs):
            return False
        all_jobs_done = all(j["status"] == "C" and
                            (j.get("accepted_date") or j.get("auto_closed_at")) for j in jobs)
        all_srs_done = all(s["status"] == "C" for s in srs)
        all_tirs_done = all(t["status"] == "C" for t in tirs)
        return all_jobs_done and all_srs_done and all_tirs_done
    return False


def set_wr_status(doc_no, status):
    if is_wr_closed(doc_no):
        raise ValueError("Work Request is closed; its status cannot be changed")
    with tx() as conn:
        conn.execute("UPDATE work_requests SET status=? WHERE doc_no=?",
                     (status, doc_no))


# ----------------------------------------------------------------------------
# بطاقات العمل السريع JC وأوامر العمل WO
# ----------------------------------------------------------------------------
JOB_FIELDS = ["wr_no", "requesting_dept", "cost_center", "tag_no",
              "equipment_name", "location",
              "category", "priority", "est_hours", "contact", "planning_note",
              "need_crane", "need_forklift", "need_scaffold", "need_transport",
              "legacy_no", "group_no", "plan_code"]


def create_job(doc_type, doc_date, exec_dept, description, **data):
    doc_date = _today()   # تاريخ الإصدار مثبّت على تاريخ اليوم ولا يُعدَّل
    actor = data.pop("actor", None)
    if doc_type not in ("JC", "WO"):
        raise ValueError("Type must be JC or WO")
    if doc_type == "WO" and not data.get("wr_no"):
        raise ValueError("Cannot issue a Work Order without a Work Request")
    if exec_dept not in dept_codes("MAINTENANCE"):
        raise ValueError("Executing department must be M, E, or I")
    if not (description or "").strip():
        raise ValueError("Description is required")
    vals = {k: data.get(k) for k in JOB_FIELDS}
    if vals.get("wr_no") and not (vals.get("tag_no") or vals.get("equipment_name")):
        w = query("SELECT tag_no, equipment_name, location FROM work_requests WHERE doc_no=?", (vals["wr_no"],))
        if w:
            vals["tag_no"] = vals["tag_no"] or w[0]["tag_no"]
            vals["equipment_name"] = vals["equipment_name"] or w[0]["equipment_name"]
            vals["location"] = vals["location"] or w[0]["location"]
    tag, name, loc = validate_equipment(vals["tag_no"], vals["equipment_name"],
                                        vals["location"])
    vals["tag_no"], vals["equipment_name"], vals["location"] = tag or None, name, loc
    vals["category"] = vals["category"] or "CM"
    vals["priority"] = vals["priority"] or "Normal"
    for k in ("need_crane", "need_forklift", "need_scaffold", "need_transport"):
        vals[k] = 1 if vals[k] else 0
    # بطاقة العمل السريع لا تحتاج فحص مخزون (أعطال بسيطة بدون قطع غيار معقدة)
    stock_checked = 1 if doc_type == "JC" else 0
    with tx() as conn:
        if vals["wr_no"]:
            if is_wr_closed(vals["wr_no"]):
                raise ValueError("Work Request is fully accepted and closed; no additional Work Order can be issued")
            r = conn.execute("SELECT group_no, status, plan_code FROM work_requests"
                             " WHERE doc_no=?", (vals["wr_no"],)).fetchone()
            if r and r["status"] == "REJECTED":
                raise ValueError("Request is rejected; no Work Order can be issued")
            vals["plan_code"] = r["plan_code"] if r else None
            if not vals["group_no"]:   # يرث العمل الرئيسي من الطلب
                vals["group_no"] = r["group_no"] if r else None
        vals["group_no"] = vals["group_no"] or None
        no = _next_number(conn, doc_type, exec_dept, _as_date(doc_date))
        # نوع الصيانة يحدده التخطيط: البطاقة الصادرة من الإنتاج تبقى «غير مصنفة»
        cat_set = 1 if (doc_type == "WO" or actor == "PLN") else 0
        cols = ["doc_no", "doc_type", "doc_date", "exec_dept", "description",
                "stock_checked", "cat_set"] + JOB_FIELDS
        desc_final = description.strip()
        if vals.get("plan_code") and vals["plan_code"] not in desc_final:
            desc_final = f"{vals['plan_code']} — {desc_final}"
        row = [no, doc_type, _iso(doc_date), exec_dept, desc_final,
               stock_checked, cat_set] + [vals[k] for k in JOB_FIELDS]
        conn.execute(
            f"INSERT INTO jobs({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
            row)
        _upsert_equipment(conn, tag, name, loc, vals["cost_center"])
        if vals["wr_no"]:
            conn.execute(
                "UPDATE work_requests SET status=CASE WHEN status IN ('NEW','STUDY')"
                " THEN 'CONVERTED' ELSE status END, linked_wo=COALESCE(linked_wo,?),"
                " reviewed_at=COALESCE(reviewed_at,?),"
                " exec_dept=CASE WHEN exec_dept='' THEN ? ELSE exec_dept END WHERE doc_no=?",
                (no, _stamp(), exec_dept, vals["wr_no"]))
            log_event(conn, vals["wr_no"], "WR_CONVERTED", actor or "PLN", no)
        else:
            log_event(conn, no, f"{doc_type}_ISSUED",
                      actor or vals["requesting_dept"] or "PLN")
    return no


def set_job_category(doc_no, category, actor=None):
    """تصنيف نوع الصيانة (CM/PM/PD) — من صلاحية التخطيط والبرمجة فقط."""
    if category not in CATEGORY:
        raise ValueError("Invalid maintenance category")
    with tx() as conn:
        job = conn.execute("SELECT accepted_date, auto_closed_at FROM jobs WHERE doc_no=?", (doc_no,)).fetchone()
        if not job:
            raise ValueError("Document does not exist")
        if job["accepted_date"] or job["auto_closed_at"]:
            raise ValueError("Work Order is finally closed; its classification cannot be changed")
        conn.execute("UPDATE jobs SET category=?, cat_set=1 WHERE doc_no=?", (category, doc_no))
        log_event(conn, doc_no, "JOB_CLASSIFIED", actor or "PLN", CATEGORY[category])


def unclassified_jobs():
    rows = query("SELECT * FROM jobs WHERE cat_set=0 ORDER BY doc_date, doc_no")
    for r in rows:
        r["equip"] = equipment_label(r["tag_no"], r["equipment_name"], r["location"])
    return rows


def get_job(doc_no):
    r = query("SELECT * FROM jobs WHERE doc_no=?", (doc_no,))
    return r[0] if r else None


def set_stock_checked(doc_no, flag, actor=None):
    with tx() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE doc_no=?", (doc_no,)).fetchone()
        if not job:
            raise ValueError("Document does not exist")
        if job["accepted_date"] or job["auto_closed_at"]:
            raise ValueError("Work Order is finally closed; it cannot be modified")
        conn.execute("UPDATE jobs SET stock_checked=?, updated_at=datetime('now') "
                     "WHERE doc_no=?", (1 if flag else 0, doc_no))
        if flag and not job["stock_checked"]:
            log_event(conn, doc_no, "WO_INSPECTED", actor or job["exec_dept"])


def set_job_status(doc_no, new_status, progress=None, remarks=None, on_date=None,
                   actor=None):
    if new_status not in STATUS:
        raise ValueError("Invalid status")
    on_date = _iso(on_date or date.today())
    with tx() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE doc_no=?", (doc_no,)).fetchone()
        if not job:
            raise ValueError("Document does not exist")
        if job["status"] == "C" and (job["accepted_date"] or job["auto_closed_at"]):
            raise ValueError("Work Order is finally closed; its status cannot be changed or reopened")
        old = job["status"]
        if new_status == "O" and job["doc_type"] == "WO" and not job["stock_checked"]:
            raise ValueError("Execution cannot start before confirming the theoretical stock check")
        start = job["start_date"] or (on_date if new_status in ("O", "C") else None)
        end = on_date if new_status == "C" else None
        if new_status == "C":
            prog = 100
        elif progress is not None:
            prog = int(progress)
        else:
            prog = job["progress"] or 0
        reopen = 1 if (old == "C" and new_status != "C") else 0
        completed_at = (NOW_FN().isoformat(timespec="seconds")
                        if new_status == "C" and old != "C" else job["completed_at"])
        if reopen:
            completed_at = None
        conn.execute(
            "UPDATE jobs SET status=?, start_date=?, end_date=?, completed_at=?, progress=?,"
            " remarks=COALESCE(?,remarks),"
            " accepted_date=CASE WHEN ?=1 THEN NULL ELSE accepted_date END,"
            " accepted_by=CASE WHEN ?=1 THEN NULL ELSE accepted_by END,"
            " updated_at=datetime('now') WHERE doc_no=?",
            (new_status, start, end, completed_at, prog, remarks, reopen, reopen, doc_no))
        who = actor or job["exec_dept"]
        if new_status != old:
            if new_status == "C":
                log_event(conn, doc_no, "JOB_COMPLETED", who, remarks)
            elif old == "P" and new_status == "O":
                log_event(conn, doc_no, "JOB_STARTED", who, f"{prog}%")
            else:
                log_event(conn, doc_no, "JOB_REOPENED", who)
        elif prog != (job["progress"] or 0):
            log_event(conn, doc_no, "JOB_PROGRESS", who, f"{prog}%")


def accept_job(doc_no, actor=None, accept=True, note=None):
    """تأكيد الجهة الطالبة لاستلام العمل المكتمل (إغلاق) أو إعادته للصيانة."""
    with tx() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE doc_no=?", (doc_no,)).fetchone()
        if not job:
            raise ValueError("Document does not exist")
        if job["accepted_date"] or job["auto_closed_at"]:
            raise ValueError("This job is already finally closed; it cannot be modified")
        if job["status"] != "C":
            raise ValueError("The job is not yet complete")
        who = actor or job["requesting_dept"]
        if accept:
            conn.execute("UPDATE jobs SET accepted_date=?, accepted_by=?,"
                         " updated_at=datetime('now') WHERE doc_no=?",
                         (NOW_FN().date().isoformat(), who, doc_no))
            log_event(conn, doc_no, "JOB_ACCEPTED", who, note)
        else:
            if not (note or "").strip():
                raise ValueError("Enter a reason for returning the job to Maintenance")
            conn.execute("UPDATE jobs SET status='O', end_date=NULL, completed_at=NULL,"
                         " progress=MIN(COALESCE(progress,0),90), accepted_date=NULL,"
                         " accepted_by=NULL, updated_at=datetime('now') WHERE doc_no=?",
                         (doc_no,))
            log_event(conn, doc_no, "JOB_RETURNED", who, note.strip())



def auto_close_unaccepted_jobs(now=None):
    """إغلاق إداري بعد انقضاء المهلة بالساعات الفعلية منذ إكمال الصيانة.

    يقبل now وقتاً افتراضياً للاختبار دون تغيير ساعة الجهاز. للأوامر القديمة
    التي لا تملك completed_at، يُفترض اكتمالها في نهاية يوم end_date حتى لا
    تُغلق قبل مرور المهلة كاملة بسبب غياب وقت الإكمال التاريخي.
    """
    now = now or NOW_FN()
    target_days = float(get_setting("accept_target_days"))
    rows = query(
        "SELECT doc_no, end_date, completed_at FROM jobs WHERE status='C' "
        "AND accepted_date IS NULL AND auto_closed_at IS NULL "
        "AND close_reason IS NULL AND end_date IS NOT NULL")
    due = []
    for r in rows:
        if r.get("completed_at"):
            completed_at = datetime.fromisoformat(r["completed_at"])
        else:
            completed_at = datetime.combine(
                date.fromisoformat(r["end_date"]), datetime.max.time())
        if now >= completed_at + timedelta(days=target_days):
            due.append(r)
    if not due:
        return 0
    closed_at = now.isoformat(timespec="seconds")
    count = 0
    with tx() as conn:
        for r in due:
            note = (f"Administratively closed by SYSTEM after {target_days:g} days "
                    "without requester acceptance. This is not an actual acceptance.")
            changed = conn.execute(
                "UPDATE jobs SET auto_closed_at=?, updated_at=datetime('now') "
                "WHERE doc_no=? AND status='C' AND accepted_date IS NULL "
                "AND auto_closed_at IS NULL AND close_reason IS NULL",
                (closed_at, r["doc_no"])).rowcount
            if changed:
                log_event(conn, r["doc_no"], "JOB_AUTO_CLOSED", "SYSTEM", note)
                count += 1
    return count

def wr_review(doc_no, action, actor=None, reason=None):
    """مراجعة التخطيط لطلب العمل: STUDY (قيد الدراسة) أو REJECT (رفض بسبب)."""
    if is_wr_closed(doc_no):
        raise ValueError("Work Request is closed and cannot be reviewed or modified")
    with tx() as conn:
        w = conn.execute("SELECT * FROM work_requests WHERE doc_no=?", (doc_no,)).fetchone()
        if not w:
            raise ValueError("Request does not exist")
        if w["status"] not in ("NEW", "STUDY"):
            raise ValueError("This request has already been processed")
        if action == "STUDY":
            if w["status"] == "STUDY":
                raise ValueError("Request is already under review")
            conn.execute("UPDATE work_requests SET status='STUDY',"
                         " reviewed_at=COALESCE(reviewed_at,?) WHERE doc_no=?",
                         (_stamp(), doc_no))
            log_event(conn, doc_no, "WR_STUDY", actor or "PLN")
        elif action == "REJECT":
            if not (reason or "").strip():
                raise ValueError("Rejection reason is required")
            conn.execute("UPDATE work_requests SET status='REJECTED', reject_reason=?,"
                         " reviewed_at=COALESCE(reviewed_at,?) WHERE doc_no=?",
                         (reason.strip(), _stamp(), doc_no))
            log_event(conn, doc_no, "WR_REJECTED", actor or "PLN", reason.strip())
        else:
            raise ValueError("Unknown action")


# القيمة الافتراضية محفوظة للتوافق؛ القيمة الفعلية تُقرأ دائماً من Report Settings.
TECH_HOURS_PER_DAY = 5


def _shift_hours_per_day():
    """ساعات الفني اليومية من الإعدادات؛ نفس المصدر المستخدم في التقارير الشهرية."""
    return float(get_setting("shift_hours"))


def _validate_daily_tech_hours(conn, job_no, work_date, new_tech_hours,
                               replacing=False):
    """يتحقق من مجموع ساعات الفنيين العادية مقابل حضور فنيي القسم في اليوم."""
    job = conn.execute("SELECT exec_dept FROM jobs WHERE doc_no=?", (job_no,)).fetchone()
    if not job:
        raise ValueError("Document does not exist")
    dept = job["exec_dept"]
    day = _iso(work_date)

    totals = conn.execute(
        """SELECT COALESCE(SUM(l.tech_hours), 0) AS used,
                  COALESCE(SUM(CASE WHEN l.job_no=? THEN l.tech_hours ELSE 0 END), 0) AS current_job
           FROM labor_log l
           JOIN jobs j ON j.doc_no=l.job_no
           WHERE j.exec_dept=? AND l.work_date=?""",
        (job_no, dept, day)).fetchone()
    used = float(totals["used"] or 0)
    if replacing:
        used -= float(totals["current_job"] or 0)
    proposed = used + float(new_tech_hours or 0)

    manpower = conn.execute(
        "SELECT technicians FROM manpower_daily WHERE report_date=? AND dept=?",
        (day, dept)).fetchone()
    if not manpower:
        if proposed > 0:
            raise ValueError(
                f"Daily manpower has not been entered for {dept} on {day}. "
                "Save the attendance record before recording regular technician hours."
            )
        capacity = 0
    else:
        shift_hours = _shift_hours_per_day()
        capacity = int(manpower["technicians"] or 0) * shift_hours

    if proposed > capacity:
        raise ValueError(
            f"Regular technician hours exceed daily capacity for {dept} on {day}: "
            f"{proposed:g} h recorded / {capacity:g} h available "
            f"({int(manpower['technicians'] or 0) if manpower else 0} technicians × "
            f"{_shift_hours_per_day():g} h)."
        )


def get_daily_labor_capacity(report_date, dept):
    """ملخص طاقة الفنيين اليومية والساعات العادية المسجلة للقسم."""
    day = _iso(report_date)
    manpower = query(
        "SELECT technicians FROM manpower_daily WHERE report_date=? AND dept=?",
        (day, dept))
    technicians = int(manpower[0]["technicians"] or 0) if manpower else 0
    used = query(
        """SELECT COALESCE(SUM(l.tech_hours), 0) AS used
           FROM labor_log l JOIN jobs j ON j.doc_no=l.job_no
           WHERE j.exec_dept=? AND l.work_date=?""", (dept, day))
    recorded = float(used[0]["used"] or 0) if used else 0.0
    capacity = technicians * _shift_hours_per_day()
    return {"has_manpower": bool(manpower), "technicians": technicians,
            "capacity": capacity, "used": recorded,
            "remaining": capacity - recorded}


def add_labor(job_no, work_date, workers=0, spv_hours=0, tech_hours=0,
              ot_hours=0, cont_hours=0, note=None):
    for v in (workers, spv_hours, tech_hours, ot_hours, cont_hours):
        if v is None or v < 0:
            raise ValueError("Values cannot be negative")
    with tx() as conn:
        job = conn.execute("SELECT accepted_date, auto_closed_at FROM jobs WHERE doc_no=?", (job_no,)).fetchone()
        if not job:
            raise ValueError("Document does not exist")
        if job["accepted_date"] or job["auto_closed_at"]:
            raise ValueError("Work Order is finally closed; hours cannot be recorded")
        _validate_daily_tech_hours(conn, job_no, work_date, tech_hours)
        # The legacy database has a non-unique index on (job_no, work_date),
        # so replace matching daily rows explicitly instead of relying on ON CONFLICT.
        day = _iso(work_date)
        conn.execute("DELETE FROM labor_log WHERE job_no=? AND work_date=?",
                     (job_no, day))
        conn.execute(
            "INSERT INTO labor_log(job_no,work_date,workers,spv_hours,tech_hours,"
            "ot_hours,cont_hours,note) VALUES(?,?,?,?,?,?,?,?)",
            (job_no, day, int(workers), spv_hours, tech_hours,
             ot_hours, cont_hours, note))


def set_daily_labor(job_no, work_date, workers=0, spv_hours=0, tech_hours=0,
                    ot_hours=0, cont_hours=0):
    """يحفظ ساعات يوم واحد لعمل واحد: يستبدل ما سُجّل لنفس العمل والتاريخ
    (وإذا كانت كل القيم صفراً يحذف السجل)."""
    vals = (workers, spv_hours, tech_hours, ot_hours, cont_hours)
    if any(v is None or v < 0 for v in vals):
        raise ValueError("Values cannot be negative")
    with tx() as conn:
        job = conn.execute("SELECT accepted_date, auto_closed_at FROM jobs WHERE doc_no=?", (job_no,)).fetchone()
        if not job:
            raise ValueError("Document does not exist")
        if job["accepted_date"] or job["auto_closed_at"]:
            raise ValueError("Work Order is finally closed; its hours cannot be modified")
        _validate_daily_tech_hours(conn, job_no, work_date, tech_hours, replacing=True)
        conn.execute("DELETE FROM labor_log WHERE job_no=? AND work_date=?",
                     (job_no, _iso(work_date)))
        if any(v > 0 for v in vals):
            conn.execute(
                "INSERT INTO labor_log(job_no,work_date,workers,spv_hours,tech_hours,"
                "ot_hours,cont_hours) VALUES(?,?,?,?,?,?,?)",
                (job_no, _iso(work_date), int(workers), spv_hours, tech_hours,
                 ot_hours, cont_hours))


def add_spare(job_no, stock_no, description, unit, qty, returned=0):
    with tx() as conn:
        job = conn.execute("SELECT accepted_date, auto_closed_at FROM jobs WHERE doc_no=?", (job_no,)).fetchone()
        if not job:
            raise ValueError("Document does not exist")
        if job["accepted_date"] or job["auto_closed_at"]:
            raise ValueError("Work Order is finally closed; spare parts cannot be added")
        conn.execute(
            "INSERT INTO spare_parts(job_no,stock_no,description,unit,qty,returned)"
            " VALUES(?,?,?,?,?,?)",
            (job_no, stock_no, description, unit, qty, returned))


# ----------------------------------------------------------------------------
# طلبات الخدمة SR  (الرقم: SR-PLN-yy-nnn لأن التخطيط هو الجهة المصدرة/المنفذة)
# ----------------------------------------------------------------------------
def _executor_name(conn, name):
    """يُرجع الاسم المعتمد للجهة المنفذة؛ وإن لم تكن في القائمة تُضاف في آخرها."""
    name = " ".join((name or "").split())
    if not name:
        raise ValueError("Executing party is required — select one from the list or add a new party")
    if len(name) > 80:
        raise ValueError("Executing party name is too long (maximum 80 characters)")
    row = conn.execute("SELECT name FROM executors WHERE name=?", (name,)).fetchone()
    if row:
        return row["name"]
    conn.execute("INSERT INTO executors(name) VALUES(?)", (name,))
    return name


def list_executors():
    """الجهات المنفذة بترتيب إضافتها (الجديدة في آخر القائمة)."""
    return [r["name"] for r in query("SELECT name FROM executors ORDER BY id")]


def executor_usage():
    """كل جهة وعدد طلبات الخدمة المسندة إليها وما فُتح منها."""
    use = {r["executor"]: r for r in query(
        "SELECT executor, COUNT(*) n, SUM(status!='C') open_n FROM service_requests"
        " WHERE executor IS NOT NULL GROUP BY executor")}
    return [{"id": i, "name": n, "total": (use.get(n) or {}).get("n", 0),
             "open": (use.get(n) or {}).get("open_n", 0) or 0}
            for i, n in enumerate(list_executors(), 1)]


def create_sr(doc_date, service_type, description, requesting_dept=None,
              job_no=None, tag_no=None, from_date=None, to_date=None,
              est_hours=None, category=None, contact=None, planning_note=None,
              legacy_no=None, equipment_name=None, location=None, actor=None,
              wr_no=None, executor=None):
    doc_date = _today()   # تاريخ الإصدار مثبّت على تاريخ اليوم ولا يُعدَّل
    if actor not in (None, "PLN"):
        raise ValueError("Service Requests are issued by Planning only — submit a Work Request for Planning review")
    if not wr_no:
        raise ValueError("Cannot issue a Service Request without a Work Request")
    if service_type not in SERVICE_TYPES:
        raise ValueError("Invalid service type")
    if not (description or "").strip():
        raise ValueError("Description is required")
    if not _clean(executor):
        raise ValueError("Executing party is required — select one from the list or add a new party")
    with tx() as conn:
        executor = _executor_name(conn, executor)
        if wr_no:
            if is_wr_closed(wr_no):
                raise ValueError("Work Request is fully accepted and closed; no Service Request can be issued")
            w = conn.execute("SELECT status, plan_code FROM work_requests WHERE doc_no=?",
                             (wr_no,)).fetchone()
            if not w:
                raise ValueError("Work Request does not exist")
            if w["status"] == "REJECTED":
                raise ValueError("Request is rejected; no Service Request can be issued")
        code = w["plan_code"] if wr_no else None
        desc_final = description.strip()
        if code and code not in desc_final:
            desc_final = f"{code} — {desc_final}"
        no = _next_number(conn, "SR", "PLN", _as_date(doc_date))
        conn.execute(
            "INSERT INTO service_requests(doc_no,doc_date,requesting_dept,job_no,"
            "tag_no,equipment_name,location,service_type,description,from_date,to_date,"
            "est_hours,category,contact,planning_note,legacy_no,wr_no,plan_code,executor)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (no, _iso(doc_date), requesting_dept, job_no, _clean(tag_no) or None,
             _clean(equipment_name), _clean(location), service_type,
             desc_final, _iso(from_date), _iso(to_date), est_hours,
             category, contact, planning_note, legacy_no, wr_no or None, code, executor))
        if wr_no:
            conn.execute("UPDATE work_requests SET status=CASE WHEN status IN ('NEW','STUDY')"
                         " THEN 'SERVICE' ELSE status END, linked_sr=COALESCE(linked_sr,?),"
                         " reviewed_at=COALESCE(reviewed_at,?) WHERE doc_no=?",
                         (no, _stamp(), wr_no))
            log_event(conn, wr_no, "WR_TO_SR", actor or "PLN", no)
        if _clean(tag_no) or (_clean(equipment_name) and _clean(location)):
            _upsert_equipment(conn, tag_no, equipment_name, location)
        log_event(conn, no, "SR_ISSUED", actor or "PLN",
                  SERVICE_TYPES[service_type] + f" — {executor}"
                  + (f" — {job_no}" if job_no else ""))
    return no


def set_sr_status(doc_no, status, remarks=None, actor=None):
    if status not in STATUS:
        raise ValueError("Invalid status")
    with tx() as conn:
        sr = conn.execute("SELECT status FROM service_requests WHERE doc_no=?", (doc_no,)).fetchone()
        if not sr:
            raise ValueError("Service Request does not exist")
        if sr["status"] == "C":
            raise ValueError("Service Request is complete and closed; it cannot be modified")
        conn.execute("UPDATE service_requests SET status=?, remarks=COALESCE(?,remarks)"
                     " WHERE doc_no=?", (status, remarks, doc_no))
        log_event(conn, doc_no, "SR_STATUS", actor or "PLN", STATUS[status])


def set_sr_daily_hours(sr_no, work_date, workers=0, hours=0, note=None):
    """ساعات يوم واحد لطلب خدمة: تستبدل ما سُجّل لنفس الطلب والتاريخ
    (وإذا كانت القيم صفراً يُحذف السجل)."""
    if any(v is None or v < 0 for v in (workers, hours)):
        raise ValueError("Values cannot be negative")
    with tx() as conn:
        sr = conn.execute("SELECT status FROM service_requests WHERE doc_no=?",
                          (sr_no,)).fetchone()
        if not sr:
            raise ValueError("Service Request does not exist")
        if sr["status"] == "C":
            raise ValueError("Service Request is complete and closed; its hours cannot be modified")
        conn.execute("DELETE FROM sr_labor_log WHERE sr_no=? AND work_date=?",
                     (sr_no, _iso(work_date)))
        if workers > 0 or hours > 0:
            conn.execute("INSERT INTO sr_labor_log(sr_no,work_date,workers,hours,note)"
                         " VALUES(?,?,?,?,?)",
                         (sr_no, _iso(work_date), int(workers), hours, note))


def add_sr_material(sr_no, stock_no, description, unit, qty, returned=0):
    if not _clean(description):
        raise ValueError("Material description is required")
    if (qty or 0) < 0 or (returned or 0) < 0:
        raise ValueError("Values cannot be negative")
    with tx() as conn:
        sr = conn.execute("SELECT status FROM service_requests WHERE doc_no=?",
                          (sr_no,)).fetchone()
        if not sr:
            raise ValueError("Service Request does not exist")
        if sr["status"] == "C":
            raise ValueError("Service Request is complete and closed; materials cannot be added")
        conn.execute("INSERT INTO sr_materials(sr_no,stock_no,description,unit,qty,returned)"
                     " VALUES(?,?,?,?,?,?)",
                     (sr_no, _clean(stock_no), _clean(description), _clean(unit),
                      qty or 0, returned or 0))


# ----------------------------------------------------------------------------
# مسؤول النظام: حساب منفصل وجلسات موثقة وتدقيق للتغييرات الإدارية
# ----------------------------------------------------------------------------
ADMIN_PBKDF2_ITERATIONS = 310000
ADMIN_SESSION_HOURS = 8


def _admin_password_hash(password, salt, iterations=ADMIN_PBKDF2_ITERATIONS):
    import hashlib
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def create_admin_account(username, password):
    """Bootstrap the first administrator; never stores a plain-text password."""
    import secrets
    username = (username or "").strip()
    if not username or len(username) > 50:
        raise ValueError("Enter a valid administrator username")
    if not password or len(password) < 12:
        raise ValueError("Administrator password must contain at least 12 characters")
    salt = secrets.token_bytes(32)
    digest = _admin_password_hash(password, salt)
    with tx() as conn:
        if conn.execute("SELECT 1 FROM admin_accounts LIMIT 1").fetchone():
            raise ValueError("An administrator account already exists")
        conn.execute("INSERT INTO admin_accounts(username,salt,password_hash,iterations) VALUES(?,?,?,?)",
                     (username, salt, digest, ADMIN_PBKDF2_ITERATIONS))
    return username


def admin_login(username, password):
    """Verify credentials and return a one-time session token, or None."""
    import hashlib
    import hmac
    import secrets
    username = (username or "").strip()
    row = query("SELECT * FROM admin_accounts WHERE username=?", (username,))
    if not row:
        return None
    account = row[0]
    candidate = _admin_password_hash(password or "", account["salt"], account["iterations"])
    if not hmac.compare_digest(candidate, account["password_hash"]):
        return None
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = (NOW_FN() + timedelta(hours=ADMIN_SESSION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    with tx() as conn:
        conn.execute("INSERT INTO admin_sessions(token_hash,username,expires_at) VALUES(?,?,?)",
                     (token_hash, username, expires))
    return token


def _admin_session_row(conn, token):
    import hashlib
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    row = conn.execute(
        "SELECT username FROM admin_sessions WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?",
        (token_hash, NOW_FN().strftime("%Y-%m-%d %H:%M:%S"))).fetchone()
    return row


def admin_session_valid(token):
    with get_conn() as conn:
        return bool(_admin_session_row(conn, token))


def admin_logout(token):
    import hashlib
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with tx() as conn:
        conn.execute("UPDATE admin_sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                     (NOW_FN().strftime("%Y-%m-%d %H:%M:%S"), token_hash))


def get_department_approved(dept):
    rows = query("SELECT approved FROM department_approvals WHERE dept=?", (dept,))
    return int(rows[0]["approved"]) if rows else 0


def set_department_approved(dept, approved, admin_token):
    """Only a live database-verified admin session may change approved headcount."""
    approved = int(approved)
    if approved < 0:
        raise ValueError("Approved headcount cannot be negative")
    if dept not in [d[0] for d in DEPARTMENTS if d[3] == "MAINTENANCE"]:
        raise ValueError("Select a valid maintenance department")
    with tx() as conn:
        session = _admin_session_row(conn, admin_token)
        if not session:
            raise PermissionError("A valid administrator session is required")
        old = conn.execute("SELECT approved FROM department_approvals WHERE dept=?", (dept,)).fetchone()
        old_value = int(old["approved"]) if old else 0
        conn.execute(
            "INSERT INTO department_approvals(dept,approved,updated_by,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(dept) DO UPDATE SET approved=excluded.approved, "
            "updated_by=excluded.updated_by, updated_at=excluded.updated_at",
            (dept, approved, session["username"], NOW_FN().strftime("%Y-%m-%d %H:%M:%S")))
        if old_value != approved:
            conn.execute(
                "INSERT INTO admin_audit_log(username,action,dept,old_value,new_value) VALUES(?,?,?,?,?)",
                (session["username"], "APPROVED_HEADCOUNT_CHANGED", dept, str(old_value), str(approved)))


def admin_audit_log(limit=100):
    return query("SELECT created_at, username, action, dept, old_value, new_value "
                 "FROM admin_audit_log ORDER BY id DESC LIMIT ?", (int(limit),))


# ----------------------------------------------------------------------------
# القوى العاملة اليومية
# ----------------------------------------------------------------------------
MANPOWER_FIELDS = ["approved", "supervisors", "technicians", "leave_n", "absent",
                   "sick", "in_train", "ex_train", "shift_rest", "other", "contract"]


def save_manpower(report_date, dept, **vals):
    row = [int(vals.get(k, 0) or 0) for k in MANPOWER_FIELDS]
    if any(v < 0 for v in row):
        raise ValueError("Manpower values cannot be negative")
    day = _iso(report_date)
    technicians = row[MANPOWER_FIELDS.index("technicians")]
    with tx() as conn:
        # Approved is controlled by the administrator, never by daily department entry.
        approval = conn.execute("SELECT approved FROM department_approvals WHERE dept=?", (dept,)).fetchone()
        row[MANPOWER_FIELDS.index("approved")] = int(approval["approved"]) if approval else 0
        used = conn.execute(
            """SELECT COALESCE(SUM(l.tech_hours), 0) AS used
               FROM labor_log l JOIN jobs j ON j.doc_no=l.job_no
               WHERE j.exec_dept=? AND l.work_date=?""", (dept, day)).fetchone()
        recorded = float(used["used"] or 0)
        capacity = technicians * _shift_hours_per_day()
        if recorded > capacity:
            raise ValueError(
                f"Cannot save manpower: {recorded:g} regular technician hours are already "
                f"recorded, but {technicians} technicians provide only {capacity:g} hours "
                f"of daily capacity."
            )
        conn.execute(
            f"INSERT OR REPLACE INTO manpower_daily(report_date,dept,"
            f"{','.join(MANPOWER_FIELDS)}) VALUES(?,?,{','.join('?' * len(MANPOWER_FIELDS))})",
            [day, dept] + row)


def get_manpower(report_date, dept):
    r = query("SELECT * FROM manpower_daily WHERE report_date=? AND dept=?",
              (_iso(report_date), dept))
    values = dict(r[0]) if r else {k: 0 for k in MANPOWER_FIELDS}
    values["approved"] = get_department_approved(dept)
    return values


# ----------------------------------------------------------------------------
# التقرير اليومي لقسم صيانة واحد
# ----------------------------------------------------------------------------
def daily_report(dept, report_date):
    """الأعمال المفتوحة (معلق/مستمر) حتى التاريخ + المكتملة في نفس اليوم،
    مع ساعات اليوم المستهلكة من سجل العمالة."""
    d = _iso(report_date)
    rows = query(
        """
        SELECT j.doc_no, j.doc_type, j.tag_no, j.equipment_name, j.location,
               j.description, j.status,
               j.start_date, j.end_date, j.progress, j.category, j.cat_set, j.remarks,
               COALESCE(l.spv,0) AS spv, COALESCE(l.tech,0) AS tech,
               COALESCE(l.ot,0) AS ot, COALESCE(l.cont,0) AS cont,
               COALESCE(l.workers,0) AS workers
        FROM jobs j
        LEFT JOIN (SELECT job_no, MAX(workers) workers, SUM(spv_hours) spv, SUM(tech_hours) tech,
                          SUM(ot_hours) ot, SUM(cont_hours) cont
                   FROM labor_log WHERE work_date=? GROUP BY job_no) l
               ON l.job_no = j.doc_no
        WHERE j.exec_dept=? AND j.doc_date<=?
          AND (j.status!='C' OR j.end_date=?)
        ORDER BY j.doc_date, j.doc_no
        """, (d, dept, d, d))
    for r in rows:
        r["equip"] = equipment_label(r["tag_no"], r["equipment_name"], r["location"])
        if not r["cat_set"]:
            r["category"] = ""
    return rows


def stats(doc_type=None):
    """doc_type='WO' لتقارير التخطيط العامة (بطاقات العمل السريع لا تمر على التخطيط)."""
    if doc_type:
        return query("SELECT exec_dept, status, COUNT(*) AS n FROM jobs WHERE doc_type=? "
                     "GROUP BY exec_dept, status", (doc_type,))
    return query("SELECT exec_dept, status, COUNT(*) AS n FROM jobs "
                 "GROUP BY exec_dept, status")


# ----------------------------------------------------------------------------
# المراحل والملخصات حسب الدور
# ----------------------------------------------------------------------------
STEP_NAMES = ["Submitted", "Under Review", "Work Order", "Execution", "Completed", "Closed"]


def open_sr_count(job_no):
    return query("SELECT COUNT(*) n FROM service_requests WHERE job_no=? AND status!='C'",
                 (job_no,))[0]["n"]


def job_stage(j, open_sr=0):
    """(رقم المرحلة 1-6, وصفها) لبطاقة عمل أو أمر عمل."""
    if j["status"] == "C":
        if j.get("close_reason"):
            return 6, ("Closed automatically — not completed" if j["close_reason"] == "AUTO"
                       else "Closed — needs Planning (" + JC_CLOSE_REASONS.get(j["close_reason"], "") + ")")
        if j.get("auto_closed_at"):
            return 6, "Closed administratively — requester did not confirm acceptance"
        if j.get("accepted_date"):
            return 6, "Closed — work accepted"
        return 5, "Execution completed — awaiting requester acceptance"
    if j["status"] == "O":
        return 4, f"In Progress ({j.get('progress') or 0}%)"
    if open_sr:
        return 3, "Awaiting supporting service (SR)"
    if j["doc_type"] == "WO":
        return 3, ("Ready for execution (inspection completed)" if j.get("stock_checked")
                   else "Work Order issued — awaiting supervisor inspection")
    return 3, "Job Card issued — awaiting execution"


def wr_stage(w, job=None, open_sr=0, sr=None):
    s_ = w["status"]
    if s_ == "SERVICE":
        if sr and sr["status"] == "C":
            return 6, "Required service completed (" + sr["doc_no"] + ")"
        if sr and sr["status"] == "O":
            return 4, "Service in progress (" + sr["doc_no"] + ")"
        return 3, "Converted to Service Request" + (f" ({sr['doc_no']})" if sr else "")
    if s_ == "REJECTED":
        return 0, "Rejected — " + (w.get("reject_reason") or "")
    if s_ == "NEW":
        return 1, "Submitted — awaiting Planning review"
    if s_ == "STUDY":
        return 2, "Under review by Planning"
    if job:
        return job_stage(job, open_sr)
    return 3, "Converted to Work Order"


def _sr_step(sr):
    if sr["status"] == "C":
        return 6, f"Service completed ({sr['doc_no']})"
    if sr["status"] == "O":
        return 4, f"Service in progress ({sr['doc_no']})"
    return 3, f"Service Request awaiting execution ({sr['doc_no']})"


def wr_children(wr_no):
    """كل أوامر العمل وطلبات الخدمة الصادرة على طلب عمل واحد (قد تتعدد أثناء التنفيذ)."""
    return (query("SELECT * FROM jobs WHERE wr_no=? ORDER BY doc_no", (wr_no,)),
            query("SELECT * FROM service_requests WHERE wr_no=? ORDER BY doc_no", (wr_no,)))


def _open_sr_map():
    return {r["job_no"]: r["n"] for r in query(
        "SELECT job_no, COUNT(*) n FROM service_requests"
        " WHERE status!='C' AND job_no IS NOT NULL GROUP BY job_no")}


def my_items(dept, only=None):
    """ما أصدره القسم: طلبات العمل وبطاقات العمل السريع، مع مرحلة كل منها. للطلب أكثر من مستند → المرحلة هي الأقل تقدماً.
    only=رقم طلب عمل: يُرجع ذلك الطلب وحده (بدون بطاقات JC). كل البيانات تُجلب بعدد ثابت من الاستعلامات."""
    auto_close_unaccepted_jobs()
    par = [dept]
    cond = wcond = ""
    if only:
        cond, wcond = " AND doc_no=?", " AND w.doc_no=?"
        par.append(only)
    wrs = query("SELECT * FROM work_requests WHERE requesting_dept=?" + cond +
                " ORDER BY doc_date DESC, doc_no DESC", par)
    open_sr = _open_sr_map()
    jobs_by, srs_by = {}, {}
    if wrs:
        for j in query("SELECT j.* FROM jobs j JOIN work_requests w ON w.doc_no=j.wr_no"
                       " WHERE w.requesting_dept=?" + wcond + " ORDER BY j.doc_no", par):
            jobs_by.setdefault(j["wr_no"], []).append(j)
        for s_ in query("SELECT s.* FROM service_requests s JOIN work_requests w"
                        " ON w.doc_no=s.wr_no WHERE w.requesting_dept=?" + wcond +
                        " ORDER BY s.doc_no", par):
            srs_by.setdefault(s_["wr_no"], []).append(s_)
    out = []
    for w in wrs:
        jobs, srs = jobs_by.get(w["doc_no"], []), srs_by.get(w["doc_no"], [])
        if w["status"] in ("CONVERTED", "SERVICE") and (jobs or srs):
            cands = [job_stage(j, open_sr.get(j["doc_no"], 0)) for j in jobs] + \
                    [_sr_step(s_) for s_ in srs]
            step, label = min(cands, key=lambda c: c[0])
            if len(cands) > 1:
                label += f" — (+{len(cands) - 1} additional document(s))"
            parts = [100 if j["status"] == "C" else (j["progress"] or 0) for j in jobs] + \
                    [{"C": 100, "O": 50}.get(s_["status"], 0) for s_ in srs]
            progress = round(sum(parts) / len(parts))
            awaiting = [j["doc_no"] for j in jobs if job_stage(j)[0] == 5]
            docs = [j["doc_no"] for j in jobs] + [s_["doc_no"] for s_ in srs]
        else:
            step, label = wr_stage(w)
            progress, awaiting, docs = 0, [], []
        out.append({"kind": "WR", "doc_no": w["doc_no"], "doc_date": w["doc_date"],
                    "tag_no": w["tag_no"], "equipment_name": w["equipment_name"],
                    "location": w["location"], "description": w["description"],
                    "exec_dept": w["exec_dept"], "job_no": ", ".join(docs) or None,
                    "step": step, "stage": label, "progress": progress,
                    "awaiting": awaiting, "parent_job": w["parent_job"],
                    "need": w["need"], "plan_code": w["plan_code"]})
    if only:
        return out
    for j in query("SELECT * FROM jobs WHERE doc_type='JC' AND requesting_dept=?"
                   " ORDER BY doc_date DESC, doc_no DESC", (dept,)):
        step, label = job_stage(j, open_sr.get(j["doc_no"], 0))
        out.append({"kind": "JC", "doc_no": j["doc_no"], "doc_date": j["doc_date"],
                    "tag_no": j["tag_no"], "equipment_name": j["equipment_name"],
                    "location": j["location"], "description": j["description"],
                    "exec_dept": j["exec_dept"], "job_no": j["doc_no"],
                    "step": step, "stage": label, "progress": j["progress"] or 0,
                    "awaiting": [j["doc_no"]] if step == 5 else [], "parent_job": None,
                    "need": None, "plan_code": None})
    out.sort(key=lambda r: (r["doc_date"], r["doc_no"]), reverse=True)
    return out


def awaiting_count(dept):
    """عدد الأعمال المكتملة بانتظار استلام قسم الإنتاج/الصيانة الطالب (للشارة العلوية) باستعلام واحد."""
    auto_close_unaccepted_jobs()
    return query(
        "SELECT (SELECT COUNT(DISTINCT j.wr_no) FROM jobs j JOIN work_requests w"
        "  ON w.doc_no=j.wr_no WHERE w.requesting_dept=? AND w.status IN ('CONVERTED','SERVICE')"
        "  AND j.status='C' AND j.accepted_date IS NULL AND j.auto_closed_at IS NULL)"
        " + (SELECT COUNT(*) FROM jobs WHERE doc_type='JC' AND requesting_dept=?"
        "  AND status='C' AND accepted_date IS NULL AND auto_closed_at IS NULL) AS n", (dept, dept))[0]["n"]


def new_wo_count(dept):
    """عدد الأوامر الجديدة بانتظار الفحص لقسم صيانة (للشارة العلوية) باستعلام واحد."""
    return query(
        "SELECT COUNT(*) n FROM jobs j WHERE j.exec_dept=? AND j.status='P'"
        " AND j.doc_type='WO' AND j.stock_checked=0 AND NOT EXISTS"
        " (SELECT 1 FROM service_requests s WHERE s.job_no=j.doc_no AND s.status!='C')",
        (dept,))[0]["n"]


def related_docs(doc_no):
    docs = [doc_no]
    if doc_no.startswith("WR-"):
        jobs, srs = wr_children(doc_no)
        docs += [j["doc_no"] for j in jobs] + [s_["doc_no"] for s_ in srs]
        r = query("SELECT parent_job FROM work_requests WHERE doc_no=?", (doc_no,))
        if r and r[0]["parent_job"]:
            docs.append(r[0]["parent_job"])
    elif doc_no.startswith(("WO-", "JC-")):
        r = query("SELECT wr_no FROM jobs WHERE doc_no=?", (doc_no,))
        if r and r[0]["wr_no"]:
            docs.append(r[0]["wr_no"])
        docs += [x["doc_no"] for x in
                 query("SELECT doc_no FROM work_requests WHERE parent_job=?", (doc_no,))]
    for j in [d for d in docs if d.startswith(("WO-", "JC-"))]:
        docs += [x["doc_no"] for x in
                 query("SELECT doc_no FROM service_requests WHERE job_no=?", (j,))]
    return list(dict.fromkeys(docs))


def timeline(doc_no):
    docs = related_docs(doc_no)
    q = ",".join("?" * len(docs))
    return query(f"SELECT * FROM events WHERE doc_no IN ({q}) ORDER BY ts, id", docs)


def _parse(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return None


def planning_overview(today=None):
    today = today or NOW_FN().date()
    new_wr = query("SELECT * FROM work_requests WHERE status IN ('NEW','STUDY')"
                   " ORDER BY doc_date, doc_no")
    open_sr = query("SELECT * FROM service_requests WHERE status!='C'"
                    " ORDER BY doc_date, doc_no")
    sr_by_job = {}
    for r in open_sr:
        if r["job_no"]:
            sr_by_job[r["job_no"]] = sr_by_job.get(r["job_no"], 0) + 1
    open_wo = query("SELECT * FROM jobs WHERE doc_type='WO' AND status!='C'"
                    " ORDER BY doc_date, doc_no")
    for j in open_wo:
        j["step"], j["stage"] = job_stage(j, sr_by_job.get(j["doc_no"], 0))
        j["age"] = (today - date.fromisoformat(j["doc_date"])).days
        j["open_sr"] = sr_by_job.get(j["doc_no"], 0)
    resp = []
    for w in query("SELECT submitted_at, reviewed_at FROM work_requests"
                   " WHERE reviewed_at IS NOT NULL"):
        a, b = _parse(w["submitted_at"]), _parse(w["reviewed_at"])
        if a and b:
            resp.append((b - a).total_seconds() / 3600)
    all_wo = query("SELECT category FROM jobs WHERE doc_type='WO'")
    planned = sum(1 for j in all_wo if j["category"] in ("PM", "PD"))
    return {
        "new_wr": new_wr, "open_wo": open_wo, "open_sr": open_sr,
        "avg_response_h": round(sum(resp) / len(resp), 1) if resp else None,
        "planned_pct": round(100 * planned / len(all_wo)) if all_wo else None,
        "backlog_hours": sum(j["est_hours"] or 0 for j in open_wo),
        "aged": sum(1 for j in open_wo if j["age"] > 14),
    }


def maint_overview(dept, today=None):
    """أعمال قسم الصيانة: جديدة / جاهزة / بانتظار خدمة / مستمرة / بانتظار استلام."""
    today = today or NOW_FN().date()
    sr_by_job = {}
    for r in query("SELECT job_no FROM service_requests WHERE status!='C' AND job_no IS NOT NULL"):
        sr_by_job[r["job_no"]] = sr_by_job.get(r["job_no"], 0) + 1
    groups = {"new": [], "ready": [], "waiting_sr": [], "in_progress": [], "awaiting": []}
    for j in query("SELECT * FROM jobs WHERE exec_dept=? AND (status!='C' OR "
                   "(accepted_date IS NULL AND auto_closed_at IS NULL))"
                   " ORDER BY doc_date, doc_no", (dept,)):
        n_sr = sr_by_job.get(j["doc_no"], 0)
        j["step"], j["stage"] = job_stage(j, n_sr)
        j["age"] = (today - date.fromisoformat(j["doc_date"])).days
        if j["status"] == "C":
            groups["awaiting"].append(j)
        elif j["status"] == "O":
            groups["in_progress"].append(j)
        elif n_sr:
            groups["waiting_sr"].append(j)
        elif j["doc_type"] == "WO" and not j["stock_checked"]:
            groups["new"].append(j)
        else:
            groups["ready"].append(j)
    return groups


# ----------------------------------------------------------------------------
# الأعمال الرئيسية (Work Groups): ربط الطلبات والأوامر وحساب النسبة العامة
# الترقيم: WG-PLN-yy-nnn  (التخطيط هو الجهة المصدرة)
# ----------------------------------------------------------------------------
def _link_wr(conn, wr_no, group_no, actor=None):
    """يربط طلب عمل بعمل رئيسي (group_no=None لفك الربط). الربط يكون بطلب العمل فقط؛
    أوامر العمل وطلبات الخدمة والتفتيش تتبع العمل الرئيسي من خلال طلبها."""
    if not (wr_no or "").startswith("WR-"):
        raise ValueError("Only the Work Request (WR) can be linked to a Work Group")
    w = conn.execute("SELECT status, group_no FROM work_requests WHERE doc_no=?",
                     (wr_no,)).fetchone()
    if not w:
        raise ValueError("Work Request does not exist")
    if group_no:
        g = conn.execute("SELECT status FROM work_groups WHERE group_no=?",
                         (group_no,)).fetchone()
        if not g:
            raise ValueError("Work Group does not exist")
        if g["status"] == "C":
            raise ValueError("Work Group is complete and closed; no further links can be added")
        if w["status"] == "REJECTED":
            raise ValueError("Rejected Work Requests cannot be linked to a Work Group")
    conn.execute("UPDATE work_requests SET group_no=? WHERE doc_no=?", (group_no, wr_no))
    conn.execute("UPDATE jobs SET group_no=? WHERE wr_no=?", (group_no, wr_no))
    log_event(conn, group_no or w["group_no"] or wr_no,
              "WG_LINKED" if group_no else "WG_UNLINKED", actor or "PLN", wr_no)


def create_group(doc_date, title, requesting_dept=None, tag_no=None,
                 equipment_name=None, location=None, remarks=None, actor=None,
                 wr_nos=()):
    """عمل رئيسي جديد؛ يُربط عند الإنشاء بطلبات عمل (WR) فقط."""
    doc_date = _today()   # تاريخ الإصدار مثبّت على تاريخ اليوم ولا يُعدَّل
    if not _clean(title):
        raise ValueError("Work title is required")
    with tx() as conn:
        no = _next_number(conn, "WG", "PLN", _as_date(doc_date))
        conn.execute(
            "INSERT INTO work_groups(group_no,title,doc_date,requesting_dept,tag_no,"
            "equipment_name,location,remarks) VALUES(?,?,?,?,?,?,?,?)",
            (no, _clean(title), _iso(doc_date), requesting_dept, _clean(tag_no) or None,
             _clean(equipment_name), _clean(location), remarks))
        log_event(conn, no, "WG_CREATED", actor or "PLN", _clean(title))
        for wr_no in wr_nos or ():
            _link_wr(conn, wr_no, no, actor)
    return no


def link_to_group(wr_no, group_no, actor=None):
    """يربط طلب عمل بعمل رئيسي (group_no=None لفك الربط)."""
    with tx() as conn:
        _link_wr(conn, wr_no, group_no, actor)


def calc_progress(jobs):
    """النسبة العامة = متوسط مرجّح بالزمن التقديري: Σ(w·p)/Σw
    المستند المكتمل p=100. المستند بلا تقدير يأخذ متوسط أوزان المستندات الأخرى (أو 1)."""
    if not jobs:
        return 0
    known = [j["est_hours"] for j in jobs if (j.get("est_hours") or 0) > 0]
    fallback = sum(known) / len(known) if known else 1
    ws = [j["est_hours"] if (j.get("est_hours") or 0) > 0 else fallback for j in jobs]
    ps = [100 if j["status"] == "C" else (j.get("progress") or 0) for j in jobs]
    return int(round(sum(w * p for w, p in zip(ws, ps)) / sum(ws)))


_STEP_PCT = {"C": 100, "O": 50}


def _wr_docs(upto=None, today=None):
    """كل المستندات الصادرة على طلبات العمل (أمر عمل WO / طلب خدمة SR / طلب تفتيش TIR)
    مجمّعة برقم طلب العمل وبصيغة موحّدة. today يضيف ساعات ذلك اليوم وعلامات الإغلاق."""
    d = _iso(today) if today else None
    cond, par = (" AND doc_date<=?", (_iso(upto),)) if upto else ("", ())
    job_hrs, sr_hrs = {}, {}
    if d:
        job_hrs = {r["job_no"]: r["mh"] for r in query(
            "SELECT job_no, SUM(spv_hours+tech_hours+ot_hours+cont_hours) mh"
            " FROM labor_log WHERE work_date=? GROUP BY job_no", (d,))}
        sr_hrs = {r["sr_no"]: r["h"] for r in query(
            "SELECT sr_no, SUM(hours) h FROM sr_labor_log WHERE work_date=? GROUP BY sr_no", (d,))}
    # تاريخ إكمال طلب الخدمة الفعلي (من السجل الزمني)، وليس تاريخ «إلى» المخطط
    sr_done = {e["doc_no"]: e["day"] for e in query(
        "SELECT doc_no, MAX(substr(ts,1,10)) day FROM events"
        " WHERE event='SR_STATUS' AND note=? GROUP BY doc_no", (STATUS["C"],))}
    out = {}

    def add(wr_no, row):
        row["wr_no"] = wr_no
        row["closed_today"] = bool(d) and row.get("done_date") == d
        out.setdefault(wr_no, []).append(row)

    for j in query("SELECT * FROM jobs WHERE wr_no IS NOT NULL AND doc_type='WO'"
                   + cond + " ORDER BY doc_no", par):
        add(j["wr_no"], {
            "kind": "WO", "doc_no": j["doc_no"], "description": j["description"],
            "dept": j["exec_dept"], "party": dept_en(j["exec_dept"]), "status": j["status"],
            "progress": 100 if j["status"] == "C" else (j["progress"] or 0),
            "est_hours": j["est_hours"], "start_date": j["start_date"],
            "end_date": j["end_date"], "mh_today": job_hrs.get(j["doc_no"], 0) or 0,
            "remarks": j["remarks"] or "",
            "closed": j["status"] == "C" and bool(j["accepted_date"]),
            "done_date": j["end_date"] if j["status"] == "C" else None})
    for s in query("SELECT * FROM service_requests WHERE wr_no IS NOT NULL"
                   + cond + " ORDER BY doc_no", par):
        add(s["wr_no"], {
            "kind": "SR", "doc_no": s["doc_no"], "description": s["description"],
            "dept": None, "party": SERVICE_TYPES.get(s["service_type"], s["service_type"])
                     + (f" — {s['executor']}" if s.get("executor") else ""),
            "status": s["status"], "progress": _STEP_PCT.get(s["status"], 0),
            "est_hours": s["est_hours"], "start_date": s["from_date"],
            "end_date": s["to_date"] if s["status"] == "C" else None,
            "mh_today": sr_hrs.get(s["doc_no"], 0) or 0, "remarks": s["remarks"] or "",
            "closed": s["status"] == "C",
            "done_date": sr_done.get(s["doc_no"]) if s["status"] == "C" else None})
    for t in query("SELECT * FROM inspection_reports WHERE wr_no IS NOT NULL"
                   + cond + " ORDER BY doc_no", par):
        sub = (t["submitted_at"] or "")[:10]
        add(t["wr_no"], {
            "kind": "TIR", "doc_no": t["doc_no"],
            "description": TIR_SCOPE.get(t["scope"], t["scope"] or "")
                           + (f" — {' '.join(t['requesting_note'].split())}" if t["requesting_note"] else ""),
            "dept": "INSP", "party": dept_en("INSP"), "status": t["status"],
            "progress": _STEP_PCT.get(t["status"], 0), "est_hours": None,
            "start_date": t["doc_date"], "end_date": sub or None, "mh_today": 0,
            "remarks": t["recommendations"] or "",
            "closed": t["status"] == "C", "done_date": sub or None})
    return out


def _wr_entry(w, ch):
    """صف طلب عمل مع المستندات الصادرة عليه (children)."""
    sts = [c["status"] for c in ch]
    if not ch:
        overall = "P"
    elif all(s_ == "C" for s_ in sts):
        overall = "C"
    elif any(s_ in ("O", "C") for s_ in sts):
        overall = "O"
    else:
        overall = "P"
    starts = [c["start_date"] for c in ch if c["start_date"]]
    ends = [c["end_date"] for c in ch if c["end_date"]]
    return {
        "kind": "WR", "key": w["doc_no"], "title": w["description"],
        "doc_date": w["doc_date"],
        "equip": equipment_label(w["tag_no"], w["equipment_name"], w["location"]),
        "requesting_dept": w["requesting_dept"], "exec_dept": w["exec_dept"],
        "wr_status": w["status"], "status": overall, "group_no": w["group_no"],
        "reject_reason": w["reject_reason"], "children": ch,
        "progress": calc_progress(ch) if ch else 0,
        "mh_today": sum(c["mh_today"] for c in ch),
        "start_date": min(starts) if starts else None,
        "end_date": max(ends) if overall == "C" and ends else None}


def suggest_group_status(docs, wrs=()):
    """حالة العمل الرئيسي تُؤخذ من الأعمال المرتبطة به (لا تُدخل يدوياً): لا يكتمل إلا إذا
    اكتملت كل المستندات الصادرة على كل طلباته ولم يبقَ طلب لم يُعالَج. الطلبات المرفوضة
    لا تُحتسب."""
    pending = any(w["wr_status"] in ("NEW", "STUDY") for w in wrs)
    if docs and not pending and all(x["status"] == "C" for x in docs):
        return "C"
    if any(x["status"] in ("O", "C") or (x.get("progress") or 0) > 0 for x in docs):
        return "O"
    return "P"


def _group_summary(ents):
    """الحالة والنسبة والتواريخ المشتقة لعمل رئيسي من كل طلباته (قبل أي إخفاء للعرض)."""
    docs = [c for e in ents for c in e["children"]]
    status = suggest_group_status(docs, ents)
    starts = [c["start_date"] for c in docs if c["start_date"]]
    dones = [c["done_date"] for c in docs if c.get("done_date")]
    return {"docs": docs, "status": status, "progress": calc_progress(docs),
            "start_date": min(starts) if starts else None,
            "end_date": max(dones) if status == "C" and dones else None}


def group_members(group_no):
    """طلبات العمل المرتبطة بالعمل الرئيسي، لكلٍّ منها مستنداته (WO/SR/TIR) في children."""
    wrs = query("SELECT * FROM work_requests WHERE group_no=? ORDER BY doc_date, doc_no",
                (group_no,))
    docs = _wr_docs()
    return [_wr_entry(w, docs.get(w["doc_no"], [])) for w in wrs]


def list_groups(include_closed=False):
    out = []
    docs_all = _wr_docs()
    for g in query("SELECT * FROM work_groups ORDER BY doc_date DESC, group_no DESC"):
        wrs = [_wr_entry(w, docs_all.get(w["doc_no"], [])) for w in query(
            "SELECT * FROM work_requests WHERE group_no=? ORDER BY doc_date, doc_no",
            (g["group_no"],))]
        sm = _group_summary(wrs)
        g["wrs"], g["docs"] = wrs, sm["docs"]
        g["status"] = g["suggested"] = sm["status"]          # مشتقة من الأعمال المرتبطة
        g["progress"] = sm["progress"]
        g["start_date"], g["end_date"] = sm["start_date"], sm["end_date"]
        g["depts"] = sorted({c["dept"] for c in sm["docs"] if c["dept"]})
        if g["status"] == "C" and not include_closed:
            continue
        out.append(g)
    return out


# ----------------------------------------------------------------------------
# التقرير اليومي للتخطيط: العمل الرئيسي (إن وُجد) ← طلب العمل ← أوامر العمل
# وطلبات الخدمة وطلبات التفتيش. حالة العمل الرئيسي ونسبته له وحده: اكتمال أمر
# لا يعني اكتمال العمل ما دامت فيه مستندات أخرى لم تكتمل.
# ----------------------------------------------------------------------------
def planning_daily_report(report_date):
    """الصف الرئيسي: عمل رئيسي (إن وُجد) ← طلب العمل ← أوامر العمل وطلبات الخدمة والتفتيش.
    المستندات التي اكتملت قبل تاريخ التقرير لا تظهر (وتُحتسب في النسب والحالة)."""
    d = _iso(report_date)
    docs = _wr_docs(upto=d, today=d)
    wrs = query("SELECT * FROM work_requests WHERE doc_date<=? ORDER BY doc_date, doc_no", (d,))
    entries = {w["doc_no"]: _wr_entry(w, docs.get(w["doc_no"], [])) for w in wrs}
    gmap = {g["group_no"]: g for g in query("SELECT * FROM work_groups")}

    def done_before(c):
        return c["status"] == "C" and (c.get("done_date") or "") < d

    def wr_hidden(e):
        """طلب عمل كل مستنداته اكتملت قبل اليوم، أو مرفوض في يوم سابق."""
        if e["wr_status"] == "REJECTED":
            return True
        return (e["wr_status"] in ("CONVERTED", "SERVICE") and bool(e["children"])
                and all(done_before(c) for c in e["children"]))

    def show(e):
        """نسخة للعرض: تُخفى المستندات المكتملة سابقاً مع بقاء النسبة والحالة محسوبتين بالكل."""
        v = dict(e)
        v["children"] = [c for c in e["children"] if not done_before(c)]
        return v

    works = []
    for w in wrs:                         # طلبات عمل مستقلة (غير مرتبطة بعمل رئيسي)
        g = gmap.get(w["group_no"]) if w["group_no"] else None
        if g and g["doc_date"] <= d:
            continue                      # يظهر تحت عمله الرئيسي
        e = entries[w["doc_no"]]
        if e["wr_status"] == "REJECTED":
            if (w["reviewed_at"] or "")[:10] == d:
                works.append(show(e))
        elif not wr_hidden(e):
            works.append(show(e))
    for g in gmap.values():               # الأعمال الرئيسية
        if g["doc_date"] > d:
            continue
        ents = [entries[w["doc_no"]] for w in wrs if w["group_no"] == g["group_no"]]
        sm = _group_summary(ents)
        if sm["status"] == "C" and not any(c.get("closed_today") for c in sm["docs"]):
            continue                      # اكتمل العمل الرئيسي قبل اليوم
        equip = equipment_label(g["tag_no"], g["equipment_name"], g["location"]) \
            or next((e["equip"] for e in ents if e["equip"]), "")
        works.append({
            "kind": "WG", "key": g["group_no"], "title": g["title"], "doc_date": g["doc_date"],
            "equip": equip, "requesting_dept": g["requesting_dept"], "exec_dept": None,
            "status": sm["status"], "progress": sm["progress"], "suggested": sm["status"],
            "mh_today": sum(e["mh_today"] for e in ents),
            "start_date": sm["start_date"], "end_date": sm["end_date"],
            "remarks": g["remarks"] or "",
            "wr_list": [e["key"] for e in ents if e["wr_status"] != "REJECTED"],
            "wrs": [show(e) for e in ents if not wr_hidden(e)], "children": []})
    works.sort(key=lambda x: (x["doc_date"], x["key"]))
    return works


# ----------------------------------------------------------------------------
# تقارير التفتيش الفني TIR
# الترقيم: TIR-INSP-yy-nnn  (التفتيش هو الجهة المنفذة)
# الدورة: التخطيط يصدر الطلب (create_tir) → التفتيش يرفع التقرير (submit_tir)
# ----------------------------------------------------------------------------
def create_tir(doc_date, wr_no, scope, tag_no=None, equipment_name=None,
               location=None, requesting_note=None, job_no=None, actor=None,
               types=None, reason=None, priority=None, due_date=None, equip_state=None):
    """يصدر التخطيط (PLN) طلب تفتيش فني مرتبطاً بطلب عمل WR
    ويُوجَّه إلى قسم التفتيش (INSP) لإكماله بالنتائج والتوصيات."""
    doc_date = _today()   # تاريخ الإصدار مثبّت على تاريخ اليوم ولا يُعدَّل
    if actor not in (None, "PLN"):
        raise ValueError("Inspection Requests are issued by Planning only")
    if not wr_no:
        raise ValueError("Cannot issue an Inspection Request without a Work Request")
    types = [t for t in (types or []) if t in INSP_TYPES]
    if types:
        scope = scope or types[0]
    if not (scope or "").strip():
        raise ValueError("Select at least one inspection type")
    with tx() as conn:
        if wr_no:
            if is_wr_closed(wr_no):
                raise ValueError("Work Request is fully accepted and closed; no Inspection Request can be issued")
            w = conn.execute("SELECT * FROM work_requests WHERE doc_no=?",
                             (wr_no,)).fetchone()
            if not w:
                raise ValueError("Work Request does not exist")
            if w["status"] == "REJECTED":
                raise ValueError("Request is rejected; no Inspection Request can be issued")
            tag_no = tag_no or w["tag_no"]
            equipment_name = equipment_name or w["equipment_name"]
            location = location or w["location"]
        no = _next_number(conn, "TIR", "INSP", _as_date(doc_date))
        conn.execute(
            "INSERT INTO inspection_reports(doc_no,doc_date,wr_no,job_no,tag_no,"
            "equipment_name,location,scope,requesting_note,status,plan_code,"
            "reason,insp_types,priority,due_date,equip_state)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (no, _iso(doc_date), wr_no or None, job_no or None,
             _clean(tag_no) or None, _clean(equipment_name), _clean(location),
             scope.strip(), _clean(requesting_note), "P",
             w["plan_code"] if wr_no else None,
             reason or None, ",".join(types) or None, priority or None,
             _iso(due_date) if due_date else None, equip_state or None))
        log_event(conn, no, "TIR_ISSUED", actor or "PLN",
                  f"Regarding {wr_no}" if wr_no else scope)
        if wr_no:
            log_event(conn, wr_no, "TIR_ISSUED", actor or "PLN", no)
    return no


def submit_tir(doc_no, findings, recommendations=None, wps_spec=None,
               coating_system=None, actor=None, condition=None, vibration=None,
               form_data=None):
    """قسم التفتيش (INSP) يُكمل التقرير بالملاحظات والتوصيات."""
    if not (findings or "").strip():
        raise ValueError("Findings are required")
    with tx() as conn:
        t = conn.execute("SELECT * FROM inspection_reports WHERE doc_no=?",
                         (doc_no,)).fetchone()
        if not t:
            raise ValueError("Report does not exist")
        if t["status"] == "C":
            raise ValueError("Inspection report has already been submitted; the request is closed and cannot be modified")
        conn.execute(
            "UPDATE inspection_reports SET findings=?, recommendations=?,"
            " wps_spec=?, coating_system=?, status='C', submitted_at=?,"
            " form_json=COALESCE(?,form_json) WHERE doc_no=?",
            (findings.strip(), (_clean(recommendations) or None),
             _clean(wps_spec) or None, _clean(coating_system) or None,
             _stamp(), json.dumps(form_data, ensure_ascii=False, default=str)
             if form_data else None, doc_no))
        snippet = findings[:80] + ("…" if len(findings) > 80 else "")
        log_event(conn, doc_no, "TIR_SUBMITTED", actor or "INSP", snippet)
        if t["wr_no"]:
            log_event(conn, t["wr_no"], "TIR_SUBMITTED", actor or "INSP", doc_no)
        # نتيجة التفتيش تحدّث حالة المعدة وقراءة الاهتزاز (تُستخدم في اختيار معدات الخطة)
        eq_vals = {}
        if condition:
            eq_vals["condition"] = condition
        if vibration:
            eq_vals["vibration"] = vibration
        if eq_vals:
            if t["tag_no"]:
                e = conn.execute("SELECT id FROM equipment WHERE tag_no=?",
                                 (t["tag_no"],)).fetchone()
            else:
                e = conn.execute("SELECT id FROM equipment WHERE tag_no IS NULL AND name=?"
                                 " AND location=?", (t["equipment_name"], t["location"])).fetchone()
            tir_equipment = e["id"] if e else None
        else:
            tir_equipment = None
    if tir_equipment:
        update_equipment_data(tir_equipment, **eq_vals)


def save_tir_form(doc_no, form_data, actor=None):
    """Saves the INSP form as a draft (no closing). A pending request moves to 'in progress'."""
    with tx() as conn:
        t = conn.execute("SELECT status FROM inspection_reports WHERE doc_no=?",
                         (doc_no,)).fetchone()
        if not t:
            raise ValueError("Report does not exist")
        if t["status"] == "C":
            raise ValueError("Inspection report has already been submitted; it cannot be modified")
        conn.execute("UPDATE inspection_reports SET form_json=?, status='O' WHERE doc_no=?",
                     (json.dumps(form_data or {}, ensure_ascii=False, default=str), doc_no))
        log_event(conn, doc_no, "TIR_FORM_SAVED", actor or "INSP")


def set_tir_status(doc_no, status, actor=None):
    """تحديث حالة طلب التفتيش (P معلق / O قيد التفتيش / C مكتمل)."""
    if status not in STATUS:
        raise ValueError("Invalid status")
    with tx() as conn:
        t = conn.execute("SELECT status FROM inspection_reports WHERE doc_no=?",
                         (doc_no,)).fetchone()
        if not t:
            raise ValueError("Report does not exist")
        if t["status"] == "C":
            raise ValueError("Inspection Request is closed and its report has been issued; its status cannot be changed")
        conn.execute("UPDATE inspection_reports SET status=? WHERE doc_no=?",
                     (status, doc_no))
        log_event(conn, doc_no, "TIR_STATUS", actor or "INSP", TIR_STATUS.get(status, status))


def _parse_reqs(t):
    try:
        r = json.loads((t or {}).get("reqs_json") or "[]")
    except (TypeError, ValueError):
        return []
    return r if isinstance(r, list) else []


def _doc_status_map(doc_nos):
    """حالة المستندات (أوامر عمل / طلبات خدمة) المرتبطة بمتطلبات التفتيش، باستعلامين."""
    nos = sorted({d for d in doc_nos if d})
    if not nos:
        return {}
    q = ",".join("?" * len(nos))
    out = {r["doc_no"]: r["status"] for r in query(
        f"SELECT doc_no, status FROM jobs WHERE doc_no IN ({q})", nos)}
    out.update({r["doc_no"]: r["status"] for r in query(
        f"SELECT doc_no, status FROM service_requests WHERE doc_no IN ({q})", nos)})
    return out


def tir_reqs(t, smap=None):
    """متطلبات التفتيش مع حالة مستند كل متطلب (مكتمل = تحضير جاهز)."""
    reqs = _parse_reqs(t)
    if smap is None:
        smap = _doc_status_map([r.get("doc_no") for r in reqs])
    for r in reqs:
        r["kind_ar"] = REQ_KINDS.get(r.get("kind"), r.get("kind") or "")
        st_ = smap.get(r.get("doc_no"))
        r["doc_status"], r["done"] = st_, st_ == "C"
        r["state_ar"] = ("Awaiting Planning document" if not r.get("doc_no")
                         else f"{r['doc_no']} — {STATUS.get(st_, '?')}")
    return reqs


def tir_stage(t, reqs):
    """المرحلة المشتقة لطلب التفتيش (لا تغيّر الحالات الأساسية P/O/C)."""
    if t["status"] == "C":
        if t.get("sent_at"):
            return "SENT", TIR_STAGES["SENT"]
        return "SEND", TIR_STAGES["SEND"]
    if t["status"] == "O":
        return "INSP", TIR_STAGES["INSP"]
    if not t.get("studied_at"):
        return "STUDY", TIR_STAGES["STUDY"]
    if any(not r.get("doc_no") for r in reqs):
        return "ISSUE", TIR_STAGES["ISSUE"]
    if any(not r["done"] for r in reqs):
        return "PREP", TIR_STAGES["PREP"]
    return "READY", TIR_STAGES["READY"]


def _tir_decorate(rows):
    smap = _doc_status_map([x.get("doc_no") for r in rows for x in _parse_reqs(r)])
    for r in rows:
        r["equip"] = equipment_label(r.get("tag_no"), r.get("equipment_name"), r.get("location"))
        r["status_ar"] = TIR_STATUS.get(r["status"], r["status"])
        r["scope_ar"] = TIR_SCOPE.get(r.get("scope", ""), r.get("scope") or "—")
        r["types"] = [x for x in (r.get("insp_types") or "").split(",") if x]
        r["types_ar"] = ", ".join(INSP_TYPES.get(x, x) for x in r["types"]) or r["scope_ar"]
        r["reqs"] = tir_reqs(r, smap)
        r["stage"], r["stage_ar"] = tir_stage(r, r["reqs"])
    return rows


def get_tir(doc_no):
    r = query("SELECT * FROM inspection_reports WHERE doc_no=?", (doc_no,))
    return _tir_decorate(r)[0] if r else None


def list_tirs(status=None, stage=None):
    """قائمة طلبات/تقارير التفتيش مع المرحلة المشتقة (stage) وتسمية المعدة."""
    params, sql = [], "SELECT * FROM inspection_reports"
    if status:
        sql += " WHERE status=?"
        params.append(status)
    rows = _tir_decorate(query(sql + " ORDER BY doc_date DESC, doc_no DESC", params))
    return [r for r in rows if not stage or r["stage"] == stage]


def tir_children(wr_no):
    """طلبات التفتيش المرتبطة بطلب عمل واحد."""
    return _tir_decorate(query("SELECT * FROM inspection_reports WHERE wr_no=? ORDER BY doc_no",
                               (wr_no,)))


def tir_for_job(job):
    """تقارير التفتيش ذات الصلة بأمر عمل: على نفس طلب العمل، أو التي نشأ منها طلبه (src_tir)."""
    out = list(tir_children(job["wr_no"])) if job.get("wr_no") else []
    if job.get("wr_no"):
        w = query("SELECT src_tir FROM work_requests WHERE doc_no=?", (job["wr_no"],))
        if w and w[0]["src_tir"] and w[0]["src_tir"] not in {t["doc_no"] for t in out}:
            t = get_tir(w[0]["src_tir"])
            if t:
                out.append(t)
    return out


def tir_study(doc_no, reqs, actor=None):
    """التفتيش يدرس الطلب ويرسل متطلباته للتخطيط (قد تكون فارغة = لا تحضيرات).
    reqs: [{id?, kind, detail}]. المتطلب الذي صدر له مستند لا يُعدَّل ولا يُحذف."""
    if actor not in (None, "INSP"):
        raise ValueError("Only Inspection records inspection requirements")
    with tx() as conn:
        t = conn.execute("SELECT * FROM inspection_reports WHERE doc_no=?", (doc_no,)).fetchone()
        if not t:
            raise ValueError("Request does not exist")
        if t["status"] == "C":
            raise ValueError("Inspection report is closed; requirements cannot be changed")
        old = {r["id"]: r for r in _parse_reqs(dict(t))}
        n = max([int(i[1:]) for i in old if i[1:].isdigit()] + [0])
        new, keep = [], set()
        for r in reqs or []:
            kind = (r.get("kind") or "").strip()
            detail = " ".join((r.get("detail") or "").split())
            if kind not in REQ_KINDS:
                raise ValueError("Select the requirement type in every row")
            rid = r.get("id")
            if rid in old:
                o = old[rid]
                if o.get("doc_no") and (o["kind"] != kind or (o.get("detail") or "") != detail):
                    raise ValueError(f"Requirement {rid} already has document {o['doc_no']}; it cannot be modified")
                keep.add(rid)
                new.append(dict(o, kind=kind, detail=detail))
            else:
                n += 1
                new.append({"id": f"R{n}", "kind": kind, "detail": detail, "doc_no": None})
        for rid, o in old.items():
            if rid not in keep and o.get("doc_no"):
                raise ValueError(f"Requirement {rid} already has document {o['doc_no']}; it cannot be removed")
        conn.execute("UPDATE inspection_reports SET reqs_json=?, studied_at=COALESCE(studied_at,?)"
                     " WHERE doc_no=?", (json.dumps(new, ensure_ascii=False), _stamp(), doc_no))
        note = f"{len(new)} requirement(s)" if new else "No preparations required"
        log_event(conn, doc_no, "TIR_STUDIED", actor or "INSP", note)
        if t["wr_no"]:
            log_event(conn, t["wr_no"], "TIR_STUDIED", actor or "INSP", doc_no)


def tir_open_reqs(wr_no):
    """متطلبات التفتيش على طلب عمل لم يصدر لها مستند بعد (لنوافذ إصدار WO / SR)."""
    out = []
    for t in query("SELECT * FROM inspection_reports WHERE wr_no=? AND status!='C' ORDER BY doc_no",
                   (wr_no,)):
        for r in _parse_reqs(t):
            if not r.get("doc_no"):
                out.append({"tir_no": t["doc_no"], "id": r["id"],
                            "label": f"{t['doc_no']} {r['id']} — {REQ_KINDS.get(r['kind'], r['kind'])}"
                                     + (f": {r['detail']}" if r.get("detail") else "")})
    return out


def tir_link_req(tir_no, req_id, doc_no, actor=None):
    """التخطيط يربط المستند الصادر (WO / SR على نفس طلب العمل) بمتطلب التفتيش."""
    if actor not in (None, "PLN"):
        raise ValueError("Only Planning links documents to inspection requirements")
    with tx() as conn:
        t = conn.execute("SELECT * FROM inspection_reports WHERE doc_no=?", (tir_no,)).fetchone()
        if not t:
            raise ValueError("Inspection request does not exist")
        reqs = _parse_reqs(dict(t))
        r = next((x for x in reqs if x["id"] == req_id), None)
        if not r:
            raise ValueError("Requirement does not exist")
        if r.get("doc_no"):
            raise ValueError(f"Requirement already linked to {r['doc_no']}")
        d = (conn.execute("SELECT wr_no FROM jobs WHERE doc_no=?", (doc_no,)).fetchone()
             or conn.execute("SELECT wr_no FROM service_requests WHERE doc_no=?", (doc_no,)).fetchone())
        if not d or d["wr_no"] != t["wr_no"]:
            raise ValueError("The document must be issued on the same Work Request")
        r["doc_no"] = doc_no
        conn.execute("UPDATE inspection_reports SET reqs_json=? WHERE doc_no=?",
                     (json.dumps(reqs, ensure_ascii=False), tir_no))
        log_event(conn, tir_no, "TIR_REQ_LINKED", actor or "PLN", f"{req_id} → {doc_no}")


def tir_send(doc_no, actor=None, note=None, has_recs=False):
    """التخطيط يرسل تقرير التفتيش المكتمل للجهة الطالبة (إرسال واحد).
    has_recs: يحدده التطبيق من التقرير ليظهر للطالب زر طلب العمل عند وجود توصيات."""
    if actor != "PLN":
        raise ValueError("Only Planning & Programming sends inspection reports to the requesting department")
    with tx() as conn:
        t = conn.execute("SELECT * FROM inspection_reports WHERE doc_no=?", (doc_no,)).fetchone()
        if not t:
            raise ValueError("Report does not exist")
        if t["status"] != "C":
            raise ValueError("Inspection has not submitted the report yet")
        if t["sent_at"]:
            raise ValueError("This report has already been sent to the requesting department")
        if not t["wr_no"]:
            raise ValueError("The report is not linked to a Work Request")
        conn.execute("UPDATE inspection_reports SET sent_at=?, sent_by=?, sent_with_recs=? WHERE doc_no=?",
                     (_stamp(), actor, 1 if has_recs else 0, doc_no))
        txt = "Sent to the requesting department" + (f" — {_clean(note)}" if _clean(note) else "")
        log_event(conn, doc_no, "TIR_SENT", actor, txt)
        log_event(conn, t["wr_no"], "TIR_SENT", actor, f"{doc_no} — {txt}")


def tir_sent_for_wr(wr_no):
    """تقارير التفتيش التي أرسلها التخطيط للجهة الطالبة على طلب عمل."""
    return [t for t in tir_children(wr_no) if t.get("sent_at")]


def link_wr_to_tir(wr_no, tir_no, actor=None):
    """طلب عمل جديد من الجهة الطالبة لتنفيذ توصيات تقرير تفتيش أُرسل إليها."""
    with tx() as conn:
        t = conn.execute("SELECT sent_at, sent_with_recs FROM inspection_reports WHERE doc_no=?",
                         (tir_no,)).fetchone()
        if not t or not t["sent_at"] or not t["sent_with_recs"]:
            raise ValueError("The inspection report was not sent with recommendations")
        conn.execute("UPDATE work_requests SET src_tir=? WHERE doc_no=?", (tir_no, wr_no))
        log_event(conn, wr_no, "WR_FROM_TIR", actor, tir_no)
        log_event(conn, tir_no, "WR_FROM_TIR", actor, wr_no)


TIR_VIEWS = {"ACT": "Needs my action", "WAIT": "In progress", "DONE": "Closed"}
_TIR_ACT = {"PLN": {"ISSUE", "SEND"}, "INSP": {"STUDY", "READY", "INSP"}}


def tir_view(stage, role):
    """العرض الذي يقع فيه طلب تفتيش بحسب الدور: يحتاج إجراءً مني / قيد المتابعة / مغلق."""
    if stage == "SENT":
        return "DONE"
    return "ACT" if stage in _TIR_ACT.get(role, set()) else "WAIT"


def tir_badge(role):
    """عدد البنود التي تنتظر إجراء الدور (للشارة العلوية)."""
    return sum(1 for r in list_tirs() if tir_view(r["stage"], role) == "ACT")


# ----------------------------------------------------------------------------
# خطط الصيانة الشاملة والوقائية السنوية
# الأعمدة: شهور السنة — الصفوف: المعدات. تُختار المجموعة حسب ساعات التشغيل منذ آخر
# عمرة وقراءات الاهتزاز وحالة المعدة (من تقارير التفتيش) وتُوزَّع على الأشهر.
# ----------------------------------------------------------------------------
_EQ_FIELDS = {"eq_class", "maint_dept", "run_hours", "overhaul_interval",
              "last_overhaul_hours", "last_overhaul_date", "vibration", "condition",
              "cost_center", "name", "location"}


def update_equipment_data(equipment_id, **fields):
    """تحديث تصنيف المعدة وقراءاتها (ساعات التشغيل، الاهتزاز، الحالة...)."""
    bad = set(fields) - _EQ_FIELDS
    if bad:
        raise ValueError(f"Unsupported fields: {', '.join(sorted(bad))}")
    vals = {}
    for k, v in fields.items():
        if k in ("run_hours", "overhaul_interval", "last_overhaul_hours", "vibration"):
            v = None if v in (None, "") else float(v)
            if v is not None and v < 0:
                raise ValueError("Numeric values cannot be negative")
        elif k == "eq_class":
            v = (v or "").strip() or None
            v = " ".join(v.split()) if v else None   # نوع المعدة نص حر يكتبه المخطط
        elif k == "maint_dept":
            v = (v or "").strip() or None
            if v and v not in dept_codes("MAINTENANCE"):
                raise ValueError("Maintenance department must be M, E, or I")
        elif k == "condition":
            v = (v or "").strip() or None
            if v and v not in CONDITION:
                raise ValueError("Invalid equipment condition")
        elif isinstance(v, str):
            v = v.strip()
        vals[k] = v
    if not vals:
        return
    if {"run_hours", "vibration", "condition", "last_overhaul_hours"} & set(vals):
        vals["readings_date"] = _today().isoformat()
    sets = ",".join(f"{k}=?" for k in vals)
    with tx() as conn:
        if not conn.execute("SELECT 1 FROM equipment WHERE id=?", (equipment_id,)).fetchone():
            raise ValueError("Equipment does not exist")
        conn.execute(f"UPDATE equipment SET {sets} WHERE id=?", [*vals.values(), equipment_id])


def pm_score(e):
    """درجة أولوية الصيانة وسبب الاختيار. الوزن: ساعات التشغيل 50% + الاهتزاز 30% + الحالة 20%."""
    interval = e.get("overhaul_interval") or 0
    run = e.get("run_hours") or 0
    since = max(run - (e.get("last_overhaul_hours") or 0), 0)
    h = min(since / interval, 1.5) if interval > 0 else 0.0
    vib = e.get("vibration") or 0
    v = min(vib / VIB_ALARM, 1.5)
    c = {"GOOD": 0.0, "FAIR": 0.5, "POOR": 1.0}.get(e.get("condition"), 0.0)
    w = PM_WEIGHTS
    score = round(100 * (w["hours"] * h + w["vib"] * v + w["cond"] * c), 1)
    parts = [f"{since:g} hours since last overhaul"
             + (f" ({h * 100:.0f}% of {interval:g})" if interval > 0 else " (interval not specified)"),
             f"Vibration {vib:g} mm/s" if e.get("vibration") is not None else "Vibration not entered",
             f"Condition {CONDITION[e['condition']]}" if e.get("condition") else "Condition not entered"]
    return score, " · ".join(parts)


def pm_classes(dept):
    return [r["eq_class"] for r in query(
        "SELECT DISTINCT eq_class FROM equipment WHERE maint_dept=? AND eq_class IS NOT NULL"
        " AND eq_class!='' ORDER BY eq_class", (dept,))]


def pm_equipment(dept):
    """معدات القسم + المعدات غير المسندة لأي قسم (لتصنيفها)."""
    rows = query("SELECT * FROM equipment WHERE maint_dept=? OR maint_dept IS NULL"
                 " OR maint_dept='' ORDER BY maint_dept DESC, COALESCE(tag_no,''), name", (dept,))
    for r in rows:
        r["equip"] = equipment_label(r["tag_no"], r["name"], r["location"])
    return rows


def pm_candidates(eq_class, dept):
    rows = query("SELECT * FROM equipment WHERE eq_class=? AND maint_dept=?", (eq_class, dept))
    for r in rows:
        r["equip"] = equipment_label(r["tag_no"], r["name"], r["location"])
        r["score"], r["reason"] = pm_score(r)
    rows.sort(key=lambda r: (-r["score"], r["equip"]))
    return rows


def pm_distribute(n, months):
    """يوزّع n معدة (مرتبة من الأعلى أولوية) بالتساوي على الأشهر المتاحة."""
    months = sorted(set(months))
    if not months or n <= 0:
        return []
    return [months[min(len(months) - 1, i * len(months) // n)] for i in range(n)]


def pm_proposal(year, dept, plan_type, eq_class, count, months, replace=False):
    """اقتراح الخطة دون حفظها: أعلى `count` معدة أولوية موزعة على `months`."""
    if plan_type not in PLAN_TYPES:
        raise ValueError("Invalid plan type")
    cands = pm_candidates(eq_class, dept)
    locked = {r["equipment_id"] for r in query(
        "SELECT equipment_id FROM pm_schedule WHERE year=? AND plan_type=?"
        + (" AND wr_no IS NOT NULL" if replace else ""), (year, plan_type))}
    cands = [c for c in cands if c["id"] not in locked]
    chosen = cands[:max(int(count), 0)]
    for c, m in zip(chosen, pm_distribute(len(chosen), months)):
        c["month"] = m
    return chosen


def pm_generate(year, dept, plan_type, eq_class, count, months, replace=False, actor=None):
    if actor not in (None, "PLN"):
        raise ValueError("Annual plans can only be created by Planning & Programming")
    if dept not in dept_codes("MAINTENANCE"):
        raise ValueError("Department must be M, E, or I")
    if not months:
        raise ValueError("Select at least one month")
    if int(count) < 1:
        raise ValueError("Equipment count must be at least 1")
    if replace:   # يُعاد توزيع غير المنفَّذ فقط (ما صدر عليه طلب عمل يبقى)
        with tx() as conn:
            conn.execute("DELETE FROM pm_schedule WHERE year=? AND dept=? AND plan_type=?"
                         " AND eq_class=? AND wr_no IS NULL",
                         (year, dept, plan_type, eq_class))
    prop = pm_proposal(year, dept, plan_type, eq_class, count, months, replace)
    if not prop:
        raise ValueError("No equipment is available for this plan (all equipment is already scheduled or no equipment is classified)")
    with tx() as conn:
        for c in prop:
            conn.execute(
                "INSERT INTO pm_schedule(year,dept,plan_type,eq_class,equipment_id,month,"
                "score,reason) VALUES(?,?,?,?,?,?,?,?)",
                (year, dept, plan_type, eq_class, c["id"], c["month"], c["score"], c["reason"]))
    return len(prop)


def pm_status_cache():
    """حالات كل طلبات العمل ومستنداتها بثلاثة استعلامات فقط (بدل ثلاثة لكل بند)."""
    wr = {r["doc_no"]: r["status"] for r in query("SELECT doc_no, status FROM work_requests")}
    docs = {}
    for j in query("SELECT wr_no, status, progress FROM jobs WHERE wr_no IS NOT NULL"):
        docs.setdefault(j["wr_no"], []).append((j["status"], j["progress"] or 0))
    for s_ in query("SELECT wr_no, status FROM service_requests WHERE wr_no IS NOT NULL"):
        docs.setdefault(s_["wr_no"], []).append((s_["status"], 0))
    return wr, docs


def pm_status(wr_no, cache=None):
    """حالة البند من طلب العمل ومستنداته: مخطط / طلب / أمر / قيد التنفيذ / مكتمل.
    مرّر cache=pm_status_cache() عند الحساب لعدة بنود."""
    if not wr_no or wr_no == "PENDING":
        return "PLANNED"
    wr, docs = cache or pm_status_cache()
    if wr_no not in wr:
        return "PLANNED"
    if wr[wr_no] == "REJECTED":
        return "REJ"
    d = docs.get(wr_no, [])
    if not d:
        return "REQ"
    if all(s_ == "C" for s_, _ in d):
        return "C"
    if any(s_ == "O" or p > 0 for s_, p in d):
        return "O"
    return "WO"


def pm_grid(year, dept, plan_type, eq_class=None):
    sql = ("SELECT s.*, e.tag_no, e.name, e.location FROM pm_schedule s"
           " JOIN equipment e ON e.id=s.equipment_id"
           " WHERE s.year=? AND s.dept=? AND s.plan_type=?")
    par = [year, dept, plan_type]
    if eq_class:
        sql += " AND s.eq_class=?"
        par.append(eq_class)
    rows = query(sql + " ORDER BY s.eq_class, s.month, COALESCE(e.tag_no,''), e.name", par)
    pc = pm_status_cache()
    for r in rows:
        r["equip"] = equipment_label(r["tag_no"], r["name"], r["location"])
        r["status"] = pm_status(r["wr_no"], pc)
        r["symbol"] = PM_SYMBOL[r["status"]]
        r["status_ar"] = PM_STATUS_AR[r["status"]]
    return rows


def _pm_item(conn, item_id):
    it = conn.execute("SELECT * FROM pm_schedule WHERE id=?", (item_id,)).fetchone()
    if not it:
        raise ValueError("Plan item does not exist")
    return it


def pm_move(item_id, month, actor=None):
    if not 1 <= int(month) <= 12:
        raise ValueError("Invalid month")
    with tx() as conn:
        it = _pm_item(conn, item_id)
        if it["wr_no"]:
            raise ValueError("A Work Request has been issued for this item; it cannot be moved")
        conn.execute("UPDATE pm_schedule SET month=? WHERE id=?", (int(month), item_id))


def pm_remove(item_id, actor=None):
    with tx() as conn:
        it = _pm_item(conn, item_id)
        if it["wr_no"]:
            raise ValueError("A Work Request has been issued for this item; it cannot be deleted")
        conn.execute("DELETE FROM pm_schedule WHERE id=?", (item_id,))


def pm_issue_wr(item_id, actor=None):
    """لم يعد مسموحاً: طلب العمل من الخطة يصدره قسم الإنتاج المشغّل فقط (plan_send_wr)."""
    raise ValueError("Annual Plan Work Requests can only be submitted by the operating Production department for the equipment")


def _norm_tag(t):
    return re.sub(r"[^A-Z0-9]", "", (t or "").upper())


def _norm_text(s):
    return " ".join((s or "").lower().split())


def plan_known_types():
    """أنواع المعدات المستخدمة سابقاً (اقتراحات عند تنزيل القالب)."""
    got = [r["t"] for r in query(
        "SELECT eq_type AS t FROM plan_items WHERE eq_type IS NOT NULL AND eq_type!=''"
        " GROUP BY eq_type ORDER BY MIN(id)")]
    for r in query("SELECT DISTINCT eq_class AS t FROM equipment"
                   " WHERE eq_class IS NOT NULL AND eq_class!='' ORDER BY eq_class"):
        if _norm_text(r["t"]) not in {_norm_text(x) for x in got}:
            got.append(r["t"])
    return got


def plan_preview(year, prod_dept, sheets):
    """معاينة الاستيراد دون حفظ. sheets = {نوع_معدة: [ {row, tags, location, ptype, depts,
    months, notes} ]}. الصف الواحد بند واحد قد يضم عدة معدات (tags). لكل معدة سطر معاينة:
    MATCH (Tag مسجل) / SIMILAR (مشابه لمسجل) / NEW (معدة جديدة)."""
    equips = query("SELECT id, tag_no, location, op_dept, eq_class FROM equipment"
                   " WHERE tag_no IS NOT NULL")
    by_tag = {e["tag_no"].strip().upper(): e for e in equips}
    by_norm = {}
    for e in equips:
        by_norm.setdefault(_norm_tag(e["tag_no"]), e)
    out = []
    for etype, rows in sheets.items():
        for r in rows:
            for tag in r["tags"]:
                e = by_tag.get(tag.strip().upper())
                status, match = ("MATCH", e) if e else ("NEW", None)
                if not e:
                    s = by_norm.get(_norm_tag(tag))
                    if s:
                        status, match = "SIMILAR", s
                warns = []
                if status == "MATCH":
                    if match.get("op_dept") and match["op_dept"] != prod_dept:
                        warns.append(f"Registered under Production department {dept_en(match['op_dept'])}")
                    if match.get("eq_class") and _norm_text(match["eq_class"]) != _norm_text(etype):
                        warns.append("Registered as equipment type "
                                     + EQ_CLASSES.get(match["eq_class"], match["eq_class"]))
                out.append({"key": f"{etype}|{r['row']}|{tag}", "item_key": f"{etype}|{r['row']}",
                            "eq_type": etype, "row": r["row"], "tag": tag,
                            "n_tags": len(r["tags"]), "location": r["location"],
                            "ptype": r["ptype"], "depts": r["depts"], "months": r["months"],
                            "notes": r["notes"], "status": status,
                            "match_id": match["id"] if match else None,
                            "match_tag": match["tag_no"] if match else None,
                            "warn": "; ".join(warns)})
    return out


def plan_import(year, prod_dept, preview, links=None, approved_on=None, minutes_no=None,
                actor=None):
    """يحفظ الخطة المعتمدة. links: {key: 'LINK'|'NEW'} للصفوف SIMILAR (الافتراضي ربط).
    الصف المشترك بين أقسام الصيانة (والجهات الخارجية) يُنشئ بنداً لكل قسم؛ والصف الذي يضم
    عدة معدات بند واحد (طلب عمل واحد). كل بند (صف × شهر) له كود ثابت AP-REF-27-001 لا يُعاد
    استخدامه، ويبقى كما هو عند إعادة الاستيراد إذا لم تتغير معداته ونوعه وشهره.
    يستبدل الاستيراد بنود أنواع المعدات الموجودة في الملف فقط (التي لم يصدر لها طلب عمل)."""
    if actor not in (None, "PLN"):
        raise ValueError("Annual Plan can only be uploaded by Planning & Programming")
    if prod_dept not in dept_codes("PRODUCTION"):
        raise ValueError("The Production department in the template is invalid")
    if not preview:
        raise ValueError("The file contains no items to import")
    links = links or {}
    year = int(year)
    types = sorted({p["eq_type"] for p in preview})
    marks = ",".join("?" * len(types))
    added = created = 0
    with tx() as conn:
        # أكواد البنود الحالية بتوقيع (نوع المعدة، نوع الخطة، الشهر، مجموعة المعدات)
        grp = {}
        for r in conn.execute(
                "SELECT plan_code, eq_type, plan_type, month, equipment_id FROM plan_items"
                f" WHERE year=? AND prod_dept=? AND plan_code IS NOT NULL AND eq_type IN ({marks})",
                [year, prod_dept] + types):
            g = grp.setdefault(r["plan_code"], {"b": (r["eq_type"], r["plan_type"], r["month"]),
                                                "e": set()})
            g["e"].add(r["equipment_id"])
        old_codes = {(g["b"], frozenset(g["e"])): code for code, g in grp.items()}
        conn.execute(
            "INSERT INTO plan_headers(year,prod_dept,approved_on,minutes_no,imported_at,"
            "imported_by) VALUES(?,?,?,?,?,?) ON CONFLICT(year,prod_dept) DO UPDATE SET "
            "approved_on=COALESCE(excluded.approved_on, plan_headers.approved_on),"
            " minutes_no=COALESCE(excluded.minutes_no, plan_headers.minutes_no),"
            " imported_at=excluded.imported_at, imported_by=excluded.imported_by",
            (year, prod_dept, _iso(approved_on) if approved_on else None,
             _clean(minutes_no) or None, _stamp(), actor or "PLN"))
        conn.execute("DELETE FROM plan_items WHERE year=? AND prod_dept=? AND wr_no IS NULL"
                     f" AND eq_type IN ({marks})", [year, prod_dept] + types)
        # 1) تحديد معدات كل صف (وإنشاء المعدات الجديدة)
        eids, item_eq = {}, {}
        for p in preview:
            eid = p["match_id"]
            if p["status"] == "SIMILAR" and links.get(p["key"], "LINK") == "NEW":
                eid = None
            if eid is None:
                tag = p["tag"].strip()
                ex = conn.execute("SELECT id FROM equipment WHERE tag_no=?", (tag,)).fetchone()
                if ex:
                    eid = ex["id"]
                else:
                    cur = conn.execute(
                        "INSERT INTO equipment(tag_no,name,location,op_dept,maint_dept,eq_class)"
                        " VALUES(?,?,?,?,?,?)",
                        (tag, "", _clean(p["location"]), prod_dept,
                         next((x for x in p["depts"] if x in ("M", "E", "I")), None),
                         p["eq_type"]))
                    eid, created = cur.lastrowid, created + 1
            conn.execute("UPDATE equipment SET op_dept=? WHERE id=? AND"
                         " (op_dept IS NULL OR op_dept='')", (prod_dept, eid))
            conn.execute("UPDATE equipment SET eq_class=? WHERE id=? AND"
                         " (eq_class IS NULL OR eq_class='')", (p["eq_type"], eid))
            if _clean(p["location"]):
                conn.execute("UPDATE equipment SET location=? WHERE id=? AND"
                             " (location IS NULL OR location='')", (_clean(p["location"]), eid))
            eids[p["key"]] = eid
            item_eq.setdefault(p["item_key"], set()).add(eid)
        # 2) كود لكل (صف × شهر): القديم إن تطابق التوقيع، وإلا كود جديد
        codes = {}
        for p in preview:
            for m in p["months"]:
                k = (p["item_key"], int(m))
                if k in codes:
                    continue
                sig = ((p["eq_type"], p["ptype"], int(m)), frozenset(item_eq[p["item_key"]]))
                codes[k] = old_codes.get(sig) or _next_number(
                    conn, "AP", prod_dept, date(year, 1, 1))
        # 3) إدراج البنود
        for p in preview:
            for dept in p["depts"]:
                for m in p["months"]:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO plan_items(year,prod_dept,dept,plan_type,"
                        "equipment_id,month,notes,eq_type,item_key,plan_code)"
                        " VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (year, prod_dept, dept, p["ptype"], eids[p["key"]], int(m),
                         p["notes"] or None, p["eq_type"], p["item_key"],
                         codes[(p["item_key"], int(m))]))
                    added += cur.rowcount
        log_event(conn, f"PLAN-{prod_dept}-{year}", "PLAN_IMPORTED", actor or "PLN",
                  f"{added} item(s) — {', '.join(types)}")
    return {"items": added, "new_equipment": created, "types": types}


def plan_header(year, prod_dept):
    r = query("SELECT * FROM plan_headers WHERE year=? AND prod_dept=?", (year, prod_dept))
    return r[0] if r else None


def plan_types_present(year, prod_dept, dept, plan_type):
    """أنواع المعدات التي لها بنود في هذه الخطة (بترتيب ورودها في الاستيراد)."""
    return [r["t"] for r in query(
        "SELECT COALESCE(eq_type,'') AS t FROM plan_items"
        " WHERE year=? AND prod_dept=? AND dept=? AND plan_type=?"
        " GROUP BY COALESCE(eq_type,'') ORDER BY MIN(id)", (year, prod_dept, dept, plan_type))]


def plan_grid(year, prod_dept, dept, plan_type, eq_type=None):
    """صف لكل معدة: months = {شهر: بند} + shared = أقسام صيانة أخرى تشاركها العمل."""
    sql = ("SELECT i.*, e.tag_no, e.location FROM plan_items i"
           " JOIN equipment e ON e.id=i.equipment_id"
           " WHERE i.year=? AND i.prod_dept=? AND i.dept=? AND i.plan_type=?")
    par = [year, prod_dept, dept, plan_type]
    if eq_type is not None:
        sql += " AND COALESCE(i.eq_type,'')=?"
        par.append(eq_type)
    rows = query(sql + " ORDER BY COALESCE(e.tag_no,''), i.month", par)
    others = {}
    for r in query("SELECT DISTINCT equipment_id, plan_type, dept FROM plan_items"
                   " WHERE year=? AND prod_dept=?", (year, prod_dept)):
        others.setdefault((r["equipment_id"], r["plan_type"]), set()).add(r["dept"])
    grid, cache, pc = {}, {}, pm_status_cache()
    cur = _today().year * 12 + _today().month
    for r in rows:
        g = grid.setdefault(r["equipment_id"], {
            "equipment_id": r["equipment_id"], "tag": r["tag_no"] or "",
            "location": r["location"] or "", "notes": "", "months": {},
            "shared": sorted(others.get((r["equipment_id"], plan_type), set()) - {dept})})
        if r["wr_no"] not in cache:
            cache[r["wr_no"]] = pm_status(r["wr_no"], pc)
        st_ = cache[r["wr_no"]]
        unexec = (r["year"] * 12 + r["month"]) < cur and st_ != "C"
        sym = PM_SYMBOL[st_]
        if unexec:        # شهره مضى ولم يكتمل: 🟧 بقي بقرار التخطيط مع سببه، 🟥 بانتظار القرار
            sym = "🟧" if r["decision"] == "KEPT" else "🟥"
        g["months"][r["month"]] = {"id": r["id"], "wr_no": r["wr_no"], "status": st_,
                                   "symbol": sym, "decision": r["decision"], "unexec": unexec,
                                   "reason": r["decision_note"] or ""}
        if r["notes"] and not g["notes"]:
            g["notes"] = r["notes"]
    return list(grid.values())


# ----------------------------------------------------------------------------
# تنفيذ الخطة: الاستحقاق من أول كل شهر؛ يرسل قسم الإنتاج المشغّل طلب عمل واحداً لكل بند،
# ثم يصدر التخطيط أوامر العمل وطلبات الخدمة المطلوبة (ولا يصدر شيئاً قبل وصول الطلب).
# البند = صف في القالب × شهر، وقد يضم عدة معدات. جهة Other: يصدر التخطيط طلب خدمة.
# ----------------------------------------------------------------------------
def _plan_groups(extra_sql="", params=()):
    rows = query(
        "SELECT i.*, e.tag_no, e.location FROM plan_items i"
        " JOIN equipment e ON e.id=i.equipment_id WHERE 1=1 " + extra_sql +
        " ORDER BY i.year, i.month, i.id", params)
    items = {}
    for r in rows:
        key = r["item_key"] or f"E{r['equipment_id']}|{r['plan_type']}"
        ref = f"{r['year']}|{r['prod_dept']}|{key}|{r['month']}"
        it = items.setdefault(ref, {
            "ref": ref, "year": r["year"], "month": r["month"], "prod_dept": r["prod_dept"],
            "eq_type": r["eq_type"] or "", "plan_type": r["plan_type"], "tags": [],
            "locations": [], "depts": [], "notes": "", "wr_no": None, "code": None,
            "decision": None, "reason": ""})
        if r["decision"] and not it["decision"]:
            it["decision"], it["reason"] = r["decision"], r["decision_note"] or ""
        if r["plan_code"] and not it["code"]:
            it["code"] = r["plan_code"]
        if r["tag_no"] and r["tag_no"] not in it["tags"]:
            it["tags"].append(r["tag_no"])
        if r["location"] and r["location"] not in it["locations"]:
            it["locations"].append(r["location"])
        if r["dept"] not in it["depts"]:
            it["depts"].append(r["dept"])
        if r["notes"] and not it["notes"]:
            it["notes"] = r["notes"]
        if r["wr_no"] and r["wr_no"] != "PENDING":
            it["wr_no"] = r["wr_no"]
    return items


def plan_due_items(prod_dept=None, on_date=None):
    """بنود الخطة التي حلّ شهرها (من أول الشهر) لقسم إنتاج: DUE مستحق، OVERDUE متأخر
    (شهره مضى ولم يُرسل طلبه)، SENT أُرسل طلب العمل."""
    today = on_date or _today()
    cur = today.year * 12 + today.month
    sql, par = " AND (i.year*12+i.month) <= ? AND COALESCE(i.decision,'')!='CANCELLED'", [cur]
    if prod_dept:
        sql += " AND i.prod_dept=?"
        par.append(prod_dept)
    out, pc = [], pm_status_cache()
    rejected = {r["doc_no"] for r in query("SELECT doc_no FROM work_requests WHERE status='REJECTED'")}
    for it in _plan_groups(sql, par).values():
        it["rejected_wr"] = None
        if it["wr_no"] in rejected:        # رفض التخطيط الطلب: يُعاد إرساله من الإنتاج
            it["rejected_wr"], it["wr_no"] = it["wr_no"], None
        it["late"] = cur - (it["year"] * 12 + it["month"])
        it["state"] = "SENT" if it["wr_no"] else ("OVERDUE" if it["late"] > 0 else "DUE")
        it["status"] = pm_status(it["wr_no"], pc) if it["wr_no"] else "PLANNED"
        it["unexec"] = it["late"] > 0 and it["status"] != "C"
        out.append(it)
    out.sort(key=lambda x: (x["year"], x["month"], x["eq_type"], x["tags"][:1]))
    return out


def plan_send_wr(ref, actor, note=None):
    """يرسل قسم الإنتاج المشغّل طلب عمل واحداً للبند (حتى لو ضم عدة معدات)."""
    parts = (ref or "").split("|")
    if len(parts) < 4:
        raise ValueError("Invalid plan item")
    prod = parts[1]
    if prod not in dept_codes("PRODUCTION") or actor != prod:
        raise ValueError("Annual Plan Work Requests can only be submitted by the operating Production department for equipment")
    it = next((x for x in plan_due_items(prod) if x["ref"] == ref), None)
    if not it:
        raise ValueError("This item is not yet due (due date is the first day of the month), or the item does not exist")
    if it["wr_no"]:
        raise ValueError(f"A Work Request has already been submitted for this item ({it['wr_no']})")
    year, month, key = int(parts[0]), int(parts[-1]), "|".join(parts[2:-1])
    where = (" WHERE year=? AND prod_dept=? AND month=? AND"
             " COALESCE(item_key, 'E'||equipment_id||'|'||plan_type)=?")
    par = (year, prod, month, key)
    with tx() as conn:       # حجز البند حتى لا يصدر له طلبان
        if conn.execute("UPDATE plan_items SET wr_no='PENDING'" + where +
                        " AND (wr_no IS NULL OR wr_no IN (SELECT doc_no FROM work_requests"
                        " WHERE status='REJECTED'))", par).rowcount == 0:
            raise ValueError("A Work Request has already been submitted for this item")
    try:
        internal = [x for x in it["depts"] if x in ("M", "E", "I")]
        has_ext = "OTH" in it["depts"]
        need = "BOTH" if internal and has_ext else ("WO" if internal else "SR")
        exec_dept = internal[0] if len(internal) == 1 else ""
        desc = (f"{PLAN_TYPES[it['plan_type']].split(' (')[0]} Planned ({MONTHS_EN[month - 1]} "
                f"{year}) — {it['eq_type'] or 'Equipment'}: {', '.join(it['tags'])}")
        if it["code"]:
            desc = f"{it['code']} — {desc}"
        if it["notes"]:
            desc += f" — Plan Notes: {it['notes']}"
        if _clean(note):
            desc += f" — Production Note: {_clean(note)}"
        no = create_wr(_today(), prod, exec_dept, desc, tag_no=it["tags"][0],
                       location=(it["locations"] or [""])[0], actor=prod, need=need,
                       plan_ref=ref, plan_code=it["code"])
        with tx() as conn:
            conn.execute("UPDATE plan_items SET wr_no=?" + where + " AND wr_no='PENDING'",
                         (no,) + par)
    except Exception:
        with tx() as conn:
            conn.execute("UPDATE plan_items SET wr_no=?" + where + " AND wr_no='PENDING'",
                         (it["rejected_wr"],) + par)
        raise
    return no


def plan_wr_info(wr_no):
    """بيانات بند الخطة الذي نشأ منه طلب العمل (للتخطيط عند الإصدار) أو None."""
    w = query("SELECT plan_ref FROM work_requests WHERE doc_no=?", (wr_no,))
    if not w or not w[0]["plan_ref"]:
        return None
    parts = w[0]["plan_ref"].split("|")
    key = "|".join(parts[2:-1])
    got = _plan_groups(" AND i.year=? AND i.prod_dept=? AND i.month=? AND"
                       " COALESCE(i.item_key, 'E'||i.equipment_id||'|'||i.plan_type)=?",
                       (int(parts[0]), parts[1], int(parts[-1]), key))
    return next(iter(got.values()), None)


def plan_items_list(year, prod_dept, dept=None, plan_type=None):
    """بنود الخطة (صف × شهر) بكودها وحالتها؛ dept/plan_type للتصفية (اختياريان)."""
    sql, par = " AND i.year=? AND i.prod_dept=?", [int(year), prod_dept]
    if plan_type:
        sql += " AND i.plan_type=?"
        par.append(plan_type)
    out, cache, pc = [], {}, pm_status_cache()
    for it in _plan_groups(sql, par).values():
        if dept and dept not in it["depts"]:
            continue
        w = it["wr_no"]
        if w not in cache:
            cache[w] = pm_status(w, pc)
        it["status"] = cache[w]
        it["unexec"] = (it["year"] * 12 + it["month"]) < (_today().year * 12 + _today().month) \
            and it["status"] != "C"
        out.append(it)
    out.sort(key=lambda x: (x["month"], x["eq_type"], x["code"] or ""))
    return out


def plan_code_docs(code):
    """كل مستندات بند الخطة: طلب العمل وأوامر العمل وطلبات الخدمة والتفتيش."""
    if not code:
        return []
    out = []
    for r in query("SELECT doc_no, doc_date, description, status FROM work_requests"
                   " WHERE plan_code=? ORDER BY doc_no", (code,)):
        out.append(dict(r, kind="Work Request", status_ar=WR_STATUS.get(r["status"], r["status"])))
    for r in query("SELECT doc_no, doc_date, description, status, progress FROM jobs"
                   " WHERE plan_code=? ORDER BY doc_no", (code,)):
        out.append(dict(r, kind="Work Order", status_ar=STATUS.get(r["status"], r["status"])))
    for r in query("SELECT doc_no, doc_date, description, status FROM service_requests"
                   " WHERE plan_code=? ORDER BY doc_no", (code,)):
        out.append(dict(r, kind="Service Request", status_ar=STATUS.get(r["status"], r["status"])))
    for r in query("SELECT doc_no, doc_date, scope AS description, status FROM"
                   " inspection_reports WHERE plan_code=? ORDER BY doc_no", (code,)):
        out.append(dict(r, kind="Inspection Request", status_ar=TIR_STATUS.get(r["status"], r["status"]),
                        description=TIR_SCOPE.get(r["description"], r["description"])))
    return out


def equipment_history(tag_no, limit=None):
    """سجل أعمال معدة: ما سُجّل باسمها مباشرة + ما صدر على بنود الخطة التي تضمها
    (حتى لو كانت ثاني معدة في بند متعدد المعدات)."""
    if not tag_no:
        return []
    codes = [r["plan_code"] for r in query(
        "SELECT DISTINCT i.plan_code FROM plan_items i JOIN equipment e ON e.id=i.equipment_id"
        " WHERE e.tag_no=? AND i.plan_code IS NOT NULL", (tag_no,))]
    cond, par = "tag_no=?", [tag_no]
    if codes:
        cond += f" OR plan_code IN ({','.join('?' * len(codes))})"
        par += codes
    out = []
    for kind, table, stat in (("Work Request", "work_requests", WR_STATUS),
                              ("Work Order / Job Card", "jobs", STATUS),
                              ("Service Request", "service_requests", STATUS),
                              ("Inspection Request", "inspection_reports", TIR_STATUS)):
        for r in query(f"SELECT doc_no, doc_date, status, plan_code FROM {table} WHERE {cond}", par):
            out.append(dict(r, kind=kind, status_ar=stat.get(r["status"], r["status"]),
                            via_plan=bool(r["plan_code"] and r["plan_code"] in codes)))
    out.sort(key=lambda r: (r["doc_date"], r["doc_no"]), reverse=True)
    return out[:limit] if limit else out


def _backfill_plan_codes(conn):
    """يعطي كوداً لبنود خطط قديمة بلا كود، ويعبّئه في طلباتها ومستنداتها."""
    todo = conn.execute("SELECT COUNT(*) n FROM plan_items WHERE plan_code IS NULL").fetchone()["n"]
    if not todo:
        return
    groups = {}
    for r in conn.execute("SELECT id, year, prod_dept, month, item_key, plan_type, equipment_id"
                          " FROM plan_items WHERE plan_code IS NULL ORDER BY year, month, id"):
        key = (r["year"], r["prod_dept"], r["item_key"] or f"E{r['equipment_id']}|{r['plan_type']}",
               r["month"])
        groups.setdefault(key, []).append(r["id"])
    for (year, prod, _k, _m), ids in groups.items():
        code = _next_number(conn, "AP", prod, date(year, 1, 1))
        conn.execute(f"UPDATE plan_items SET plan_code=? WHERE id IN ({','.join('?' * len(ids))})",
                     [code] + ids)
    # الطلبات التي نشأت من الخطة قبل وجود الكود
    for w in conn.execute("SELECT doc_no, plan_ref FROM work_requests"
                          " WHERE plan_ref IS NOT NULL AND plan_code IS NULL").fetchall():
        p = w["plan_ref"].split("|")
        row = conn.execute(
            "SELECT plan_code FROM plan_items WHERE year=? AND prod_dept=? AND month=? AND"
            " COALESCE(item_key, 'E'||equipment_id||'|'||plan_type)=? LIMIT 1",
            (int(p[0]), p[1], int(p[-1]), "|".join(p[2:-1]))).fetchone()
        if row:
            for t in ("work_requests", "jobs", "service_requests", "inspection_reports"):
                col = "doc_no" if t == "work_requests" else "wr_no"
                conn.execute(f"UPDATE {t} SET plan_code=? WHERE {col}=?", (row["plan_code"], w["doc_no"]))


# ----------------------------------------------------------------------------
# البند غير المنفذ: مضى شهره ولم يكتمل. قرار التخطيط: البقاء في الخطة مع تسجيل السبب
# (لا ترحيل ولا إلغاء). يبقى البند في مكانه ويظهر بسببه في الجدول وقوائم المتابعة.
# ----------------------------------------------------------------------------
def plan_unexecuted(prod_dept=None):
    """البنود التي مضى شهرها ولم تكتمل، ومعها قرار التخطيط وسببه إن وُجد."""
    return [i for i in plan_due_items(prod_dept) if i["unexec"]]


def plan_keep(code, reason, actor):
    """يسجل قرار التخطيط: البند غير المنفذ يبقى في خطته مع السبب (يمكن تحديث السبب لاحقاً)."""
    if actor != "PLN":
        raise ValueError("Only Planning & Programming can decide on overdue plan items")
    reason = " ".join((reason or "").split())
    if not reason:
        raise ValueError("Enter a retention reason — required")
    if not any(i["code"] == code for i in plan_unexecuted()):
        raise ValueError("This item is not overdue (its month has not elapsed or it is already complete)")
    with tx() as conn:
        conn.execute("UPDATE plan_items SET decision='KEPT', decision_note=? WHERE plan_code=?",
                     (reason, code))
        log_event(conn, code, "PLAN_KEPT", actor, reason)


def plan_events(code):
    """سجل قرارات البند (من قرر ومتى ولماذا)."""
    return query("SELECT ts, actor, note FROM events WHERE doc_no=? AND event='PLAN_KEPT'"
                 " ORDER BY id", (code,))


# ----------------------------------------------------------------------------
# إعدادات التقارير الشهرية
# ----------------------------------------------------------------------------
def get_setting(key):
    """قيمة الإعداد (رقم إن أمكن)، وإلا القيمة الافتراضية."""
    r = query("SELECT value FROM settings WHERE key=?", (key,))
    v = r[0]["value"] if r else SETTINGS_DEFAULT[key][0]
    try:
        return float(v)
    except ValueError:
        return v


def settings_all():
    saved = {r["key"]: r["value"] for r in query("SELECT key, value FROM settings")}
    return [{"key": k, "label": lab, "value": saved.get(k, d), "default": d}
            for k, (d, lab) in SETTINGS_DEFAULT.items()]


def set_setting(key, value, actor=None):
    if actor != "PLN":
        raise ValueError("Only Planning & Programming changes settings")
    if key not in SETTINGS_DEFAULT:
        raise ValueError("Unknown setting")
    value = str(value).strip()
    if key == "jc_cutoff":
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            raise ValueError("Closure time must be HH:MM (e.g. 23:59)")
    else:
        try:
            if float(value) <= 0:
                raise ValueError
        except ValueError:
            raise ValueError("Enter a positive number")
    with tx() as conn:
        conn.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key)"
                     " DO UPDATE SET value=excluded.value", (key, value))


# ----------------------------------------------------------------------------
# بطاقة العمل السريع: عمرها يوم واحد. تُنجز، أو تُغلق لحاجتها إلى التخطيط
# (قطع غيار، لحام...) فيبدأ الطالب إجراءات طلب العمل، أو تُغلق تلقائياً آخر اليوم.
# ----------------------------------------------------------------------------
def close_job_planning(doc_no, reason, note, actor=None):
    """قسم الصيانة يغلق بطاقة لم يكتمل عملها لأنها تحتاج التخطيط. السبب والملاحظات إلزاميان."""
    if reason not in JC_CLOSE_REASONS or reason == "AUTO":
        raise ValueError("Select the reason this Job Card needs Planning")
    note = " ".join((note or "").split())
    if not note:
        raise ValueError("Notes are required (what was done and what remains)")
    with tx() as conn:
        j = conn.execute("SELECT * FROM jobs WHERE doc_no=?", (doc_no,)).fetchone()
        if not j:
            raise ValueError("Document does not exist")
        if j["doc_type"] != "JC":
            raise ValueError("Only a Job Card can be closed this way — a Work Order is completed or reopened")
        if j["status"] == "C":
            raise ValueError("The Job Card is already closed")
        today = NOW_FN().date().isoformat()
        conn.execute("UPDATE jobs SET status='C', end_date=?, start_date=COALESCE(start_date,?),"
                     " close_reason=?, close_note=?, accepted_date=?, accepted_by=?,"
                     " updated_at=datetime('now') WHERE doc_no=?",
                     (today, j["doc_date"], reason, note, today, actor or j["exec_dept"], doc_no))
        log_event(conn, doc_no, "JC_CLOSED_PLN", actor or j["exec_dept"],
                  f"{JC_CLOSE_REASONS[reason]} — {note}")


def jc_auto_close(now=None):
    """احتياطاً: تغلق كل بطاقة سريعة لم تُغلق عند وقت الإغلاق (إعداد jc_cutoff) في يوم إصدارها،
    أو في اليوم التالي إن لم يُفتح النظام. يُستدعى عند كل تشغيل للتطبيق."""
    now = now or NOW_FN()
    today = now.date().isoformat()
    rows = query("SELECT doc_no, doc_date FROM jobs WHERE doc_type='JC' AND status!='C'"
                 " AND doc_date<=?", (today,))
    if not rows:
        return 0
    cutoff = str(get_setting("jc_cutoff"))
    due = [r for r in rows if r["doc_date"] < today or now.strftime("%H:%M") >= cutoff]
    n = 0
    if due:
        with tx() as conn:
            for r in due:
                if conn.execute("UPDATE jobs SET status='C', end_date=?, start_date=COALESCE(start_date,?),"
                                " close_reason='AUTO', close_note=?, accepted_date=?, accepted_by='SYSTEM',"
                                " updated_at=datetime('now') WHERE doc_no=? AND status!='C'",
                                (r["doc_date"], r["doc_date"],
                                 "Closed automatically — work not completed by end of day",
                                 today, r["doc_no"])).rowcount:
                    log_event(conn, r["doc_no"], "JC_AUTO_CLOSED", "SYSTEM",
                              JC_CLOSE_REASONS["AUTO"])
                    n += 1
    return n


def link_wr_to_jc(wr_no, jc_no, actor=None):
    """طلب عمل جديد من الجهة الطالبة لإكمال عمل بطاقة سريعة أُغلقت لحاجتها إلى التخطيط."""
    with tx() as conn:
        j = conn.execute("SELECT doc_type, close_reason FROM jobs WHERE doc_no=?", (jc_no,)).fetchone()
        if not j or j["doc_type"] != "JC" or not j["close_reason"]:
            raise ValueError("The Job Card was not closed before completion")
        conn.execute("UPDATE work_requests SET src_jc=? WHERE doc_no=?", (jc_no, wr_no))
        log_event(conn, wr_no, "WR_FROM_JC", actor, jc_no)
        log_event(conn, jc_no, "WR_FROM_JC", actor, wr_no)

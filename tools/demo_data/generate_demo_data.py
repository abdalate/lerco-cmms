# -*- coding: utf-8 -*-
"""Generate isolated, deterministic LERCO CMMS demo data (2025-01-01 through today).

Run from the repository root:
    python tools/demo_data/generate_demo_data.py
    python tools/demo_data/generate_demo_data.py --output cmms_demo_2025_2026.db
    python tools/demo_data/generate_demo_data.py --force

The script never targets cmms.db. Existing output is only replaced when it carries
this generator's ownership marker and --force is explicitly supplied.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "cmms_demo_2025_2026.db"
MARKER_TABLE = "_lerco_demo_metadata"
MARKER_VALUE = "lerco-cmms-demo-data-v1"
SEED = 20261127
START_DATE = date(2025, 1, 1)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", default=str(DEFAULT_DB), help="Output SQLite path (must not be cmms.db)")
    p.add_argument("--force", action="store_true", help="Replace only a database previously created by this generator")
    p.add_argument("--seed", type=int, default=SEED, help="Deterministic random seed")
    return p.parse_args()


def prepare_output(path: Path, force: bool):
    path = path.expanduser().resolve()
    if path.name.lower() == "cmms.db":
        raise SystemExit("Refusing to use cmms.db. Choose a separate demo database filename.")
    if path.exists():
        if not force:
            raise SystemExit(f"Output already exists: {path}\nUse --force only if it is a demo database created by this generator.")
        try:
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as con:
                row = con.execute(
                    f"SELECT value FROM {MARKER_TABLE} WHERE key='owner'"
                ).fetchone()
        except sqlite3.Error:
            row = None
        if not row or row[0] != MARKER_VALUE:
            raise SystemExit("Refusing to replace this file: generator ownership marker was not found.")
        path.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                sidecar.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def main():
    args = parse_args()
    output = prepare_output(Path(args.output), args.force)
    os.environ["CMMS_DB_PATH"] = str(output)
    sys.path.insert(0, str(ROOT))

    import database as db

    rng = random.Random(args.seed)
    today = date.today()
    db.NOW_FN = lambda: datetime.combine(today, time(12, 0))
    db.init_db()

    with sqlite3.connect(output) as con:
        con.execute(f"CREATE TABLE {MARKER_TABLE}(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute(f"INSERT INTO {MARKER_TABLE}(key,value) VALUES('owner',?)", (MARKER_VALUE,))
        con.execute("INSERT INTO _lerco_demo_metadata(key,value) VALUES('seed',?)", (str(args.seed),))
        con.execute("INSERT INTO _lerco_demo_metadata(key,value) VALUES('period_start',?)", (START_DATE.isoformat(),))
        con.execute("INSERT INTO _lerco_demo_metadata(key,value) VALUES('period_end',?)", (today.isoformat(),))

    # Keep the test assumptions aligned with the agreed operating rules.
    db.set_setting("shift_hours", 5, "PLN")
    db.set_setting("accept_target_days", 3, "PLN")

    equipment = [
        ("P-101", "Process Pump", "Crude Unit"),
        ("M-201", "Electric Motor", "Utilities"),
        ("V-301", "Pressure Vessel", "Process Area"),
        ("C-401", "Air Compressor", "Offsites"),
        ("XV-501", "Control Valve", "Tank Farm"),
        ("E-601", "Heat Exchanger", "Crude Unit"),
        ("PSV-701", "Safety Valve", "Utilities"),
        ("T-801", "Storage Tank", "Oil Movement"),
    ]
    requesters = ("REF", "OM")
    maintainers = ("M", "E", "I")
    contractors = ("Coastal Industrial Services", "Libya Mechanical Works", "North Africa Scaffolding")
    service_types = ("SCAFFOLD", "CRANE", "PAINT", "WELD", "TRANSPORT")
    priorities = ("Normal", "Normal", "Normal", "Urgent", "Emergency")
    counts = {"WR": 0, "WO": 0, "JC": 0, "SR": 0, "TIR": 0, "manpower_days": 0, "labor_rows": 0}
    daily_used = {}

    def at(day, hour=10):
        db.NOW_FN = lambda d=day, h=hour: datetime.combine(d, time(h, 0))

    def create_wr(day, requester, maint, eq, text):
        at(day)
        tag, name, location = eq
        no = db.create_wr(day, requester, maint, text, tag_no=tag,
                          equipment_name=name, location=location,
                          priority=rng.choice(priorities), actor=requester)
        counts["WR"] += 1
        return no

    # Daily attendance: weekdays only, all three maintenance departments.
    day = START_DATE
    while day <= today:
        if day.weekday() < 5:
            for dept in maintainers:
                approved = {"M": 18, "E": 10, "I": 8}[dept]
                technicians = max(3, approved - rng.randint(1, 5))
                absent = approved - technicians
                db.save_manpower(day, dept, approved=approved, supervisors={"M": 3, "E": 2, "I": 2}[dept],
                                 technicians=technicians, absent=absent,
                                 leave_n=rng.randint(0, 2), sick=rng.randint(0, 1),
                                 in_train=rng.choice((0, 0, 0, 1)), ex_train=0,
                                 shift_rest=rng.randint(0, 1), other=0, contract=rng.randint(0, 2))
                counts["manpower_days"] += 1
        day += timedelta(days=1)

    # Work requests and work orders: up to one order per department/day so
    # regular technician hours stay comfortably below daily capacity.
    day = START_DATE
    while day <= today:
        if day.weekday() < 5:
            for dept in maintainers:
                if rng.random() > 0.30:
                    continue
                eq = rng.choice(equipment)
                requester = rng.choice(requesters)
                wr = create_wr(day, requester, dept, eq, f"Routine corrective maintenance - {eq[1]}")
                at(day)
                wo = db.create_job("WO", day, dept, f"Inspect and maintain {eq[1]}",
                                   wr_no=wr, actor="PLN", requesting_dept=requester,
                                   tag_no=eq[0], equipment_name=eq[1], location=eq[2],
                                   category=rng.choices(("CM", "PM", "PD"), (0.70, 0.20, 0.10))[0],
                                   priority=rng.choice(priorities), est_hours=rng.choice((5, 8, 10, 12)))
                counts["WO"] += 1
                db.set_stock_checked(wo, True, dept)
                db.set_job_status(wo, "O", rng.choice((10, 20, 30, 40)), actor=dept)
                tech_hours = float(rng.choice((5, 6, 7, 8, 9, 10)))
                # One work order per department/day; the attendance record
                # provides at least 15 regular technician hours of capacity.
                db.add_labor(wo, day, workers=rng.randint(2, 4),
                             spv_hours=rng.choice((0, 0, 1, 2)),
                             tech_hours=tech_hours, ot_hours=rng.choice((0, 0, 2, 3)),
                             cont_hours=0, note="Demo daily execution entry")
                counts["labor_rows"] += 1

                age = rng.randint(1, 12)
                finish = day + timedelta(days=age)
                if finish <= today and rng.random() < 0.78:
                    at(finish, rng.choice((14, 15, 16)))
                    db.set_job_status(wo, "C", 100, actor=dept,
                                      remarks="Work completed - generated demo record")
                    if finish + timedelta(days=3) <= today and rng.random() < 0.70:
                        at(finish + timedelta(days=3))
                        db.accept_job(wo, requester, True, "Accepted - demo record")
        day += timedelta(days=1)

    # Add a controlled sample of rejected/unconverted requests, SRs and TIRs.
    month = date(2025, 1, 1)
    while month <= today:
        # One rejected request and one request awaiting planning per month.
        for status in ("REJECT", "STUDY"):
            d = min(month + timedelta(days=10 if status == "REJECT" else 18), today)
            if d < START_DATE:
                continue
            eq = rng.choice(equipment)
            wr = create_wr(d, rng.choice(requesters), rng.choice(maintainers), eq,
                           "Demo request for planning workflow review")
            at(d)
            if status == "REJECT":
                db.wr_review(wr, "REJECT", actor="PLN", reason="Insufficient technical justification - demo")
            else:
                db.wr_review(wr, "STUDY", actor="PLN")
        # Periodic external service request.
        d = min(month + timedelta(days=22), today)
        eq = rng.choice(equipment)
        wr = create_wr(d, rng.choice(requesters), rng.choice(maintainers), eq,
                       "External specialist service required - demo")
        at(d)
        sr = db.create_sr(d, rng.choice(service_types), "External maintenance support - demo",
                          requesting_dept="REF", wr_no=wr, actor="PLN",
                          executor=rng.choice(contractors), tag_no=eq[0],
                          equipment_name=eq[1], location=eq[2])
        counts["SR"] += 1
        if rng.random() < 0.75:
            db.set_sr_status(sr, "C", "Service completed - demo", actor="PLN")

        # One inspection request per month, completed and sent by the correct roles.
        d = min(month + timedelta(days=25), today)
        eq = rng.choice(equipment)
        wr = create_wr(d, rng.choice(requesters), rng.choice(maintainers), eq,
                       "Periodic inspection request - demo")
        at(d)
        tir = db.create_tir(d, wr, "VISUAL", tag_no=eq[0],
                            equipment_name=eq[1], location=eq[2],
                            actor="PLN", types=["VISUAL"], reason="PERIODIC")
        counts["TIR"] += 1
        db.tir_study(tir, [], actor="INSP")
        db.set_tir_status(tir, "O", actor="INSP")
        db.submit_tir(tir, "Visual inspection completed - demo findings",
                      recommendations="Continue routine monitoring" if rng.random() < 0.3 else "",
                      actor="INSP")
        db.tir_send(tir, "PLN", "Inspection report issued - demo", has_recs=False)

        month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)

    # A small, realistic sample of one-day job cards.
    day = START_DATE
    while day <= today:
        if day.weekday() < 5 and rng.random() < 0.025:
            dept = rng.choice(maintainers)
            requester = rng.choice(requesters)
            eq = rng.choice(equipment)
            at(day)
            jc = db.create_job("JC", day, dept, f"Minor corrective task - {eq[1]}",
                               requesting_dept=requester, actor=requester,
                               tag_no=eq[0], equipment_name=eq[1], location=eq[2])
            counts["JC"] += 1
            if day < today:
                db.close_job_planning(jc, "OTHER", "Minor task reviewed; further planning not required - demo", dept)
        day += timedelta(days=1)

    # Restore normal clock and verify integrity, foreign keys and capacity.
    db.NOW_FN = datetime.now
    with sqlite3.connect(output) as con:
        con.row_factory = sqlite3.Row
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
        over_capacity = con.execute("""
            SELECT m.report_date, m.dept,
                   COALESCE(SUM(l.tech_hours),0) AS used,
                   m.technicians * CAST((SELECT value FROM settings WHERE key='shift_hours') AS REAL) AS capacity
            FROM manpower_daily m
            LEFT JOIN jobs j ON j.exec_dept=m.dept
            LEFT JOIN labor_log l ON l.job_no=j.doc_no AND l.work_date=m.report_date
            GROUP BY m.report_date,m.dept
            HAVING used > capacity + 0.000001
        """).fetchall()
        summary = {}
        for table in ("work_requests", "jobs", "service_requests", "inspection_reports",
                      "manpower_daily", "labor_log"):
            summary[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        by_year = {}
        for year in (2025, 2026):
            by_year[str(year)] = {
                "work_requests": con.execute("SELECT COUNT(*) FROM work_requests WHERE substr(doc_date,1,4)=?", (str(year),)).fetchone()[0],
                "work_orders": con.execute("SELECT COUNT(*) FROM jobs WHERE doc_type='WO' AND substr(doc_date,1,4)=?", (str(year),)).fetchone()[0],
                "service_requests": con.execute("SELECT COUNT(*) FROM service_requests WHERE substr(doc_date,1,4)=?", (str(year),)).fetchone()[0],
                "inspection_requests": con.execute("SELECT COUNT(*) FROM inspection_reports WHERE substr(doc_date,1,4)=?", (str(year),)).fetchone()[0],
            }
    if integrity != "ok" or fk_errors or over_capacity:
        raise RuntimeError(f"Demo database validation failed: integrity={integrity}, foreign_keys={len(fk_errors)}, over_capacity={len(over_capacity)}")

    report = {
        "database": str(output),
        "period": {"from": START_DATE.isoformat(), "to": today.isoformat()},
        "seed": args.seed,
        "generated": counts,
        "table_counts": summary,
        "by_year": by_year,
        "validation": {"integrity_check": integrity, "foreign_key_violations": len(fk_errors),
                       "daily_capacity_violations": len(over_capacity)},
    }
    report_path = output.with_suffix(".summary.json")
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nSummary saved to: {report_path}")


if __name__ == "__main__":
    main()

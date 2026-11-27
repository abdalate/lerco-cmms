# LERCO CMMS demo data generator

This folder contains an isolated data generator for testing the CMMS reporting and workflow features.

## Output and date range

By default, the script creates `cmms_demo_2025_2026.db` in the repository root, covering **1 January 2025 through the day the script is run**. It does not copy the old demo database and does not write to `cmms.db`.

## Run

From the repository root on the same Python environment used by the CMMS:

```bash
python tools/demo_data/generate_demo_data.py
```

Optional output path:

```bash
python tools/demo_data/generate_demo_data.py --output cmms_demo_2025_2026.db
```

To recreate an existing database, use `--force`. The script checks an internal ownership marker before replacing a file; it refuses to replace any database not created by this generator.

```bash
python tools/demo_data/generate_demo_data.py --force
```

The random seed is fixed for repeatable output. Change it with `--seed 1234`.

## Included records

- Daily manpower for maintenance departments M, E and I on weekdays.
- Work Requests from REF and OM, with a mix of planning and rejection states.
- Work Orders with pending, in-progress and completed states, labor entries, supervisor hours and separate overtime.
- External Service Requests with multiple contractor names and service types.
- Inspection requests that pass through study, inspection, report submission and Planning dispatch.
- A sample of one-day Job Cards.

Technician regular hours are recorded separately and checked against the daily capacity rule: technicians present × 5 hours. Supervisor, overtime and contractor hours do not increase that capacity.

## Safety

- Never point the script at `cmms.db`.
- The script refuses the filename `cmms.db`.
- Existing output is not overwritten unless `--force` is supplied and the ownership marker matches.
- Run this only against a demo output path, not a production database.
- The generated 2026 records end on the actual run date; no future records are created.

The script prints a JSON summary and writes a companion `.summary.json` report with row counts and database validation results.

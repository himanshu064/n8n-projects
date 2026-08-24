# Scripts — what they do and in what order

One-time setup helpers for the database this pipeline reads from. None of these need to run again once `linkedin_running_order` is seeded — the n8n workflows in `../workflows/` take over from there. None of them contain secrets; they read the Supabase URL and key from environment variables you set yourself.

## Files in this folder

| File | Type | What it does |
|---|---|---|
| `create-tables.sql` | SQL | Creates the 2 Supabase tables (`linkedin_running_order`, `linkedin_posts`) |
| `generate-running-order.py` | Python | Makes a FAKE 90-day schedule Excel, for building/testing before real data exists |
| `seed-supabase.py` | Python | Loads an Excel file (fake or real) into the `linkedin_running_order` table |

The real 90-day plan lives at `../documents/pipeline.xlsx` — use that instead of the generated sample once it's available.

## Run order (do these top to bottom, once)

```text
STEP 1  create-tables.sql          → run ONCE in Supabase (SQL Editor → paste → Run)
                                     Creates the empty tables. Everything else depends on this.

STEP 2  generate-running-order.py  → only if you don't have real data yet:
                                       python generate-running-order.py
                                     Makes running-order-sample.xlsx (fake data) next to this script.
                                     SKIP this step if ../documents/pipeline.xlsx already has the real plan.

STEP 3  seed-supabase.py           → set 2 environment variables, then run:
                                       export SUPABASE_URL="https://<project>.supabase.co"
                                       export SUPABASE_SERVICE_ROLE_KEY="<key>"
                                       python seed-supabase.py ../documents/pipeline.xlsx

                                     (or, for fake test data instead:
                                       python seed-supabase.py)

                                     Validates 90 rows / no duplicate dates / no blanks,
                                     then inserts. Safe to re-run — existing dates are skipped,
                                     never duplicated.
```

After step 3, check Supabase's Table Editor: `linkedin_running_order` should have 90 rows. From here, the n8n workflows in `../workflows/` do everything else — see the root [`README.md`](../README.md) for how the pipeline runs and how to test it.

## How this fits the overall flow

```text
 [scripts, once]                         [n8n workflows, ongoing]

 create-tables.sql
        ↓
 pipeline.xlsx (or generate-running-order.py for test data)
        ↓
 seed-supabase.py
        ↓
 linkedin_running_order  ──►  Weekly Generator (Sun 6 PM IST)
 (90 days of topics)          reads next 7 days → Coordinator → Writer → QA
                                     ↓
                              linkedin_posts table + Slack thread (7 posts)
                                     ↓
                              Approval Listener
                              founder reacts ✅ → row becomes "approved"
                                     ↓
                              Daily Publisher (9 AM IST)
                              approved post for today? → Google Sheet publish queue
                              no approval → does nothing
```

## Requirements

- Python 3.10+ with `openpyxl` (`pip install openpyxl`)
- A Supabase project URL and its **service_role** key (Dashboard → Project Settings → API) — the anon key won't work, since row-level security is on with no read policies
- Nothing else: seeding talks to Supabase's plain REST API directly, no `supabase` package needed

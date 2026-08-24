# scripts — one-time setup

Unlike the database-backed projects in this repo, this pipeline has **no database to seed** — it reads live from the YouTube API on every run and holds no state of its own. So there are no seed scripts here, just two one-time setup steps you do by hand.

## Files in this folder

| File | What it does |
|---|---|
| `.env.example` | The shape of the reference IDs and keys the workflow needs. Copy to `.env`, fill in your own values, keep `.env` out of git. |
| `README.md` | This file. |

## Setup step 1 — the Google Sheet header row

Create a Google Sheet (its ID goes in `.env` as `GOOGLE_SHEET_ID`) and put this **exact header row in row 1** of the first tab (`Sheet1`). The "Append Log Row" node maps to these column names one-for-one, so the spelling and order matter:

```
run_at | requested_by | video_url | video_title | channel | views | likes | comments_total | comments_analysed | positive_pct | neutral_pct | negative_pct | top_theme | doc_link | status
```

Each run appends one row, so this tab becomes a growing history of every video analysed.

## Setup step 2 — the Google Drive folder

Create (or pick) a Drive folder for the generated report Docs and put its ID in `.env` as `GOOGLE_DRIVE_FOLDER_ID`. The "Create Doc" node drops each run's Google Doc into that folder.

## Everything else is credentials, not scripts

The four service connections (YouTube, Anthropic, Google, Slack) are set up as **n8n Credentials**, not from here — see `../README.md` section 4. Once the two steps above are done and the credentials are connected, the workflow is ready to run.

## Requirements

- Nothing to install. There are no scripts to execute — both setup steps are done in the Google Sheets and Google Drive web UIs.

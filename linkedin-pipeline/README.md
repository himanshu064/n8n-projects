# FullSpec LinkedIn Content Pipeline

Plain-language write-up of the automation in this repo: what it does, what it's made of, how to connect it to the services it needs, and how to check that it's working. Written so anyone landing on this repo can understand it, not just the person who built it.

**Quick check — is it working right now?** Skip to [section 5](#5-step-by-step-flow--what-was-built-and-how-to-test-it) and follow "To test it yourself, in short."

This repo holds the **n8n workflow files** (`workflows/*.json`), the **real content plan** (`documents/pipeline.xlsx`), and the **one-time setup scripts** (`scripts/`) that seed it into Supabase. Credentials, `.env` values, and internal build notes are kept out of it on purpose — see [section 4](#4-how-to-set-up-and-connect-the-resources-it-needs) for what to create yourself before importing these.

---

## 1. What this project is

FullSpec wants one LinkedIn post published every day, for 90 days, without a person having to sit down and write it each morning. They already have a 90-day content plan (a spreadsheet: which topic goes on which day, and in what style).

So the ask was: build a system that

1. writes each day's post automatically,
2. checks its own work before showing it to anyone,
3. lets the founder approve or reject each post with a single click,
4. and then queues the approved post so it can be posted to LinkedIn.

**One important substitution:** the original plan was for the system to publish straight to LinkedIn using a paid service called Blotato. Blotato turned out to have no free tier (plans start at $29/month, and generating an API key ends the free trial). So instead, approved posts are added as a row to a **Google Sheet** — a free "publish queue" that proves the entire pipeline works end to end. Swapping the Google Sheet for a real LinkedIn-posting service later only means changing one step; everything else (the AI writing, the QA checks, the approval flow) stays exactly the same.

---

## 2. What's in this repo

```
workflows/
  LinkedIn - 2 Weekly Generator.json     Sunday 6 PM IST (+ demo button) — writes, checks, posts the week's drafts to Slack
  LinkedIn - 3 Approval Listener.json    Fires on a Slack reaction — turns a founder ✅ into an approval
  LinkedIn - 4 Daily Publisher.json      9 AM IST (+ demo button) — adds today's approved post to the Google Sheet
  LinkedIn - 5 Error Handler.json        Fires when any of the above fails — posts the error to Slack

scripts/
  create-tables.sql                      Creates the 2 Supabase tables this pipeline needs
  generate-running-order.py              Makes a fake 90-day plan, for testing before real data exists
  seed-supabase.py                       Loads an Excel plan (fake or real) into Supabase
  README.md                              Run order for the 3 files above

documents/
  pipeline.xlsx                          The 90-day content plan: date, topic, style, sequence number
```

A fifth workflow, a **Seeder**, would load the 90-day content plan (from Excel) into the database from inside n8n. It isn't included here as a JSON file because that step was done once with the `scripts/` files instead — see [section 4.1](#41-supabase-the-database) and `scripts/README.md`. It can be rebuilt as an n8n workflow later if the client wants to re-upload their own plan through the UI rather than a script.

Import order matters: **Error Handler first**, then Weekly Generator, then Approval Listener, then Daily Publisher. Only Weekly Generator ships with its **Settings → Error Workflow** already pointed at Error Handler — Approval Listener and Daily Publisher need that same setting picked by hand after import (see [section 4](#4-how-to-set-up-and-connect-the-resources-it-needs)), which is also why Error Handler has to exist first. Each workflow is imported in n8n via **Overview → Workflows → ⋯ → Import from File**.

---

## 3. Technologies used

| Piece | Tool | Why |
|---|---|---|
| Automation / orchestration | **n8n** (Cloud) | Runs every workflow on a schedule or on demand, and moves data between all the other tools |
| Writing the posts | **Claude (Anthropic)** — three separate AI calls per post | Splits the job into a cheap "planner", a strong "writer", and a cheap "checker", instead of one AI doing everything at once |
| Database | **Supabase** (Postgres) | Stores the 90-day content plan and every post the system generates, with its score, approval status, and publish status |
| Approvals | **Slack** | Where the founder sees the week's drafts and approves them with a ✅ reaction — no separate app needed |
| Publish queue | **Google Sheets** | Stand-in for "posting to LinkedIn" — free, and proves the pipeline works; swappable for a real publisher later |

**The three AI steps, specifically (all inside the Weekly Generator workflow):**

1. **Coordinator** (`claude-haiku-4-5` — fast, cheap) — reads the day's topic and style from the plan, writes a short brief: the angle, the audience, three key points, and a call to action.
2. **Writer** (`claude-sonnet-5`) — turns that brief into the actual LinkedIn post text, in the assigned style (How-to, Story, Checklist, Contrarian, etc.).
3. **QA reviewer** (`claude-haiku-4-5`) — scores the post 0–100 against a rubric. If it fails, the feedback goes back to the Writer for another attempt — at most 2 rewrites, so the process can never loop forever. If it still fails after that, the post is marked "needs manual review" and sent on anyway.

**Running cost:** roughly 2–3 cents per post, about 20 cents for a full week of 7 posts, under $1/month.

---

## 4. How to set up and connect the resources it needs

None of these credentials are in this repo. Four services need to be connected in n8n before importing the workflows — set each one up as an n8n **Credential**, then pick it from the dropdown in the relevant node after import (the JSON files carry no credential IDs).

### 4.1 Supabase (the database)

1. Run `scripts/create-tables.sql` once in the Supabase SQL Editor — creates `linkedin_running_order` (the 90-day plan: `scheduled_date`, `process_name`, `style`, `sequence_number`) and `linkedin_posts` (every generated post with its brief, QA score, approval state, Slack message, and publish status).
2. Load the 90-day plan into `linkedin_running_order` by running `scripts/seed-supabase.py` against `documents/pipeline.xlsx` (or a fake plan from `scripts/generate-running-order.py` for testing). Full steps in `scripts/README.md`.
3. Supabase dashboard → **Project Settings → API** → copy the project URL and the **service role key** (not the anon key — row-level security needs the service role key to read/write; the seed script needs the same two values as `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` environment variables).
4. In n8n: **Credentials → Create → Supabase API** → **Host** = the project URL, **Secret Key** = the service role key.

### 4.2 Anthropic (the AI models)

1. `console.anthropic.com` → **API Keys → Create Key**.
2. In n8n: **Credentials → Create → Anthropic** → paste the key. n8n reads the available model list straight from the key.

### 4.3 Slack (approvals + weekly posts)

One Slack app does two jobs: posts the week's drafts into a channel, and tells n8n whenever the founder reacts with a ✅.

1. `api.slack.com/apps` → **Create New App** → **Blank app** → pick the workspace.
2. **OAuth & Permissions → Bot Token Scopes** — add `chat:write`, `channels:read`, `channels:history`, `groups:read`, `groups:history` (the last two only if the approval channel is private), `reactions:read`, `users:read`.
3. **Install to Workspace** → copy the Bot User OAuth Token (`xoxb-...`).
4. Create the approval channel (private is recommended, since it holds unpublished drafts) and invite the bot: `/invite @your-app-name`.
5. **Basic Information → App Credentials → Signing Secret** → copy it.
6. In n8n: **Credentials → Create → Slack API** → **Access Token** = the bot token, **Signature Secret** = the signing secret.
7. **Event Subscriptions** (delivers the ✅ reaction to n8n) can only be finished after the Approval Listener workflow is imported, because Slack needs a real webhook URL to send events to:
   - Make sure **Socket Mode** is off first (it hides the Request URL field).
   - Open the imported **Approval Listener** workflow → its Slack Trigger node → copy the **Production URL**.
   - Slack app → **Event Subscriptions** → Enable → paste the URL as the **Request URL** → wait for the green "Verified" tick (the workflow must be Active for this to succeed) → **Subscribe to bot events** → add `reaction_added` → **Save Changes** (reinstall if prompted).

### 4.4 Google Sheets (the publish queue)

1. Create a Google Sheet with two tabs, `test` and `publish_queue`, each with this header row: `publish_date | post_text | style | process | qa_result | qa_notes | approved_by | approved_at | queued_at | status`.
2. In n8n: **Credentials → Create → Google Sheets OAuth2 API**. n8n Cloud offers a one-click **Sign in with Google**; a self-hosted instance needs a Google Cloud OAuth client instead (enable the Sheets and Drive APIs, create an OAuth client, add the redirect URL n8n's credential form shows, add the sheet-owner account as a test user).
3. Sign in as the account that owns (or has Editor access to) the sheet.

**All four credentials done = the system can talk to every service it needs.** Import the workflows, open each node listed above, pick the matching credential, and set each workflow's **Settings → Error Workflow** to the Error Handler.

---

## 5. Step-by-step flow — what was built and how to test it

### The full picture, in order

```
Excel (90-day plan) → Supabase: linkedin_running_order

Every Sunday 6:00 PM IST (or a "Run Now" button):
  Fetch next 7 days from the plan
    → for each day:
        Coordinator (AI) → brief (topic + style)
          → Writer (AI) → drafts the post
            → QA reviewer (AI) → pass / fail
                fail → sent back to Writer (max 2 retries) → still failing → flagged "needs manual review"
                pass → saved to Supabase, status = awaiting_approval
    → Slack: one message for the week, 7 replies underneath (one per post)

Whenever the founder reacts with ✅ on a reply:
    → Slack tells n8n → the matching post's status flips to "approved" in Supabase
    → any other emoji, or anyone else's reaction, is ignored
    → no reaction at all = that post is never published

Every morning 9:00 AM IST (or a "Run Now" button):
    → is there an approved post for today?
        no  → nothing happens
        yes → add a row to the Google Sheet, mark that post "published" in Supabase
              (or, if the sheet can't be written to, mark it "publish failed" and alert Slack)

If anything fails anywhere above → the Error Handler workflow posts it into Slack automatically
```

### What the founder actually experiences

Every Sunday evening, one message shows up in the approval channel: *"LinkedIn posts for the week starting [date]. React ✅ to approve."* Underneath it are 7 replies — the full text of each day's post, already written, with its QA score shown. The founder reads each one and, for the ones they like, clicks the ✅ reaction. That's the entire approval action — no typing, no replying, no editing. Posts with no ✅ simply never get published. The next morning, if that day's post was approved, it shows up as a new row in the Google Sheet, ready to be copied onto LinkedIn.

### To test it yourself, in short

1. Open **Weekly Generator** in n8n and click **Run Now** → check the Slack channel for one message with 7 replies.
2. React ✅ on one of those replies (as the founder's account) → check the Supabase `linkedin_posts` table — that row's status should become `approved`.
3. Open **Daily Publisher**, set its `override_date` field to the date of the post you just approved, click **Run Now** → check the Google Sheet's `test` tab for a new row.
4. Run step 3 a second time with the same date — it should do nothing (double-publish is blocked automatically), confirming the guard works.

### Known bugs to watch for if the workflows are modified

- **Supabase filter nodes that process multiple items at once must use "Build Manually" filters, not a plain text filter string** — a text filter concatenates one condition per item into the same URL and silently breaks. Only affects nodes filtering on more than one row per execution (e.g. writing back Slack message IDs after posting all 7 replies).
- **Slack node output shape**: the message timestamp comes back as `message_timestamp` (not `ts`) on current Slack node versions — anything reading it should check both, e.g. `{{ $json.message_timestamp ?? $json.ts }}`.
- **`$execution.mode`** is `'test'` in the editor and `'production'` on a real scheduled run — never `'manual'`. Any expression that should behave differently for a manual demo run vs. the real schedule needs to check for `'production'` specifically.
- **Google Sheets `403 Forbidden`** almost always means either the Sheets/Drive APIs aren't enabled on the Google Cloud project, or the signed-in account doesn't have edit access to the sheet — not a quota issue.

### Current status

- All four workflows here have been imported, connected to live credentials, and run successfully end to end (generate → approve → publish), verified against a live Supabase database and a live Google Sheet.
- The three AI prompts (Coordinator / Writer / QA) still use placeholder wording — the client's real voice guide and QA rubric need to be dropped into each node's **Options → System** field before this goes live for real posts.
- Before going live: switch the Daily Publisher's sheet tab from `test` to `publish_queue`, and turn the Sunday/9 AM schedules on (they already run correctly — the demo buttons stay usable alongside the schedule).

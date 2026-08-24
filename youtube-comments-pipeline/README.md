# YouTube Audience Report Pipeline

Plain-language write-up of the automation in this repo: what it does, what it's made of, how to connect it to the services it needs, and how to check that it's working. Written so anyone landing on this repo can understand it, not just the person who built it.

**Quick check — is it working right now?** Skip to [section 5](#5-step-by-step-flow--what-was-built-and-how-to-test-it) and follow "To test it yourself, in short."

This repo holds the **node-by-node build guide** (`BUILD_GUIDE.md`), the **full specification and scaling reference** (`RUNBOOK.md`), the **original client brief** (`documents/`), and a place for the **exported n8n workflow** (`workflows/`). Credentials, `.env` values, and live API keys are kept out of it on purpose — see [section 4](#4-how-to-set-up-and-connect-the-resources-it-needs) for what to create yourself before rebuilding it.

---

## 1. What this project is

A creator or marketer pastes a **YouTube video link and an email address** into a form. About a minute later, a full **audience report** lands in four places at once — a Google Doc, an email, a row in a Google Sheet, and a Slack message with a sentiment chart. The report says how viewers feel about the video, what they praised, what they complained about, which questions they left unanswered, and five content ideas drawn straight from the comments.

The point is to replace the impossible job of reading every comment by hand. Skimming a handful gives a biased, incomplete picture; this reads them systematically and turns them into something you can act on.

**One thing that makes it trustworthy:** AI can invent quotes or numbers, so the pipeline **fact-checks its own report** before delivering it. A separate reviewer step verifies that every quoted comment is word-for-word real and every percentage matches the underlying counts. If something doesn't check out, the report is still delivered but clearly tagged as unreviewed, so you always know whether to trust it.

**One important substitution:** the build was originally wired to n8n's bundled "free OpenAI API credits." Those credits ran out mid-build, so the three AI steps were moved to the client's own **Anthropic (Claude) key**. Everything else — the YouTube fetching, the aggregation, the QA check, and all four delivery channels — stayed exactly the same. Swapping the AI provider back, or to a stronger model, only means editing the three HTTP nodes; the rest of the pipeline is untouched.

---

## 2. What's in this repo

```
README.md          This file — the plain-language overview
BUILD_GUIDE.md     The node-by-node build: every node's exact settings, code, and AI request bodies
RUNBOOK.md         The full specification: 500-comment batching, the reviewer retry loop, error handler, scaling

workflows/
  README.md        How to export the workflow from n8n and drop the JSON here (and how to import it back)

documents/
  FullSpec_YouTubeAudience_Demo_Brief.pdf   The original client brief this was built from

scripts/
  .env.example     The shape of the reference IDs and keys the workflow needs (values blank on purpose)
  README.md        One-time setup: the Google Sheet header row and the four credentials
```

Unlike a multi-workflow project, this is **one single n8n workflow** — 19 nodes from the form to the four deliverables. There is no database to seed and no schedule to run; it fires whenever someone submits the form. Because the workflow was built directly in the n8n Cloud editor and not yet exported, `workflows/` currently holds instructions rather than a JSON file — `BUILD_GUIDE.md` reconstructs the workflow node-for-node in the meantime. See [section 4](#4-how-to-set-up-and-connect-the-resources-it-needs).

---

## 3. Technologies used

| Piece | Tool | Why |
|---|---|---|
| Automation / orchestration | **n8n** (Cloud) | Runs the whole flow on form submission and moves data between every other service |
| Fetching video + comments | **YouTube Data API v3** | Gets the video's stats and its comments (via an API key on the query string) |
| Reading the comments | **Claude (Anthropic)** — three separate AI calls | A cheap "scorer", a "report writer", and a cheap "fact-checker", instead of one AI doing everything at once |
| The report document | **Google Docs** | The full write-up, created fresh per run in a shared Drive folder |
| Emailing the report | **Gmail** | Sends an HTML version to the address from the form |
| Running history | **Google Sheets** | One row appended per run — a growing log of every video analysed |
| Summary + chart | **Slack** (+ QuickChart) | A headline, a link to the Doc, and a sentiment doughnut posted to a channel |

**The three AI steps, specifically (all Claude Haiku, `claude-haiku-4-5`, called as raw HTTP requests because n8n has no native Anthropic node):**

1. **Score** — reads every comment and labels each one: positive / neutral / negative, its topic in a few words, and whether it contains a genuine unanswered question.
2. **Analyse** — turns the scored comments and their tallies into the actual report: an overall verdict, verbatim quotes, ranked themes, unanswered questions, and five content ideas.
3. **Check** — the fact-checker: confirms every quote is word-for-word real and every number matches the counts, returning either a pass or a list of problems.

**Demo scope:** the pipeline is capped at **10 comments** per run so each AI step is a single fast call and the whole thing finishes in about a minute. `RUNBOOK.md` documents how to scale this to hundreds of comments (pagination + batching) and how to re-attach the reviewer's "retry once, then send" loop, both of which are parked for the demo.

**Running cost:** a fraction of a cent per run at 10 comments on Claude Haiku.

---

## 4. How to set up and connect the resources it needs

None of these credentials are in this repo. Four services need to be connected in n8n — set each one up as an n8n **Credential**, then pick it from the dropdown in the relevant node. `scripts/.env.example` shows the reference IDs to keep handy; `scripts/README.md` has the one-time Google Sheet setup.

### 4.1 YouTube Data API v3 (fetching the video + comments)

1. Google Cloud Console → enable **YouTube Data API v3** → **Create credentials → API key**.
2. Restrict the key to YouTube Data API v3 (recommended).
3. In the two YouTube HTTP nodes, the key is passed as the `key` query parameter. Keep it in an n8n credential or a variable, not typed inline.

### 4.2 Anthropic (the AI models)

1. `console.anthropic.com` → **API Keys → Create Key**.
2. In n8n: **Credentials → Create → Header Auth** → **Name** = `x-api-key`, **Value** = your Anthropic key. (n8n has no native Anthropic action, so the three AI steps are HTTP Request nodes; the non-secret `anthropic-version: 2023-06-01` header is added per node.)

### 4.3 Google (Docs, Sheets, Gmail — one account)

1. In n8n: **Credentials → Create → Google OAuth2**. n8n Cloud offers a one-click **Sign in with Google**; a self-hosted instance needs a Google Cloud OAuth client with the Docs, Sheets, Drive, and Gmail APIs enabled.
2. Sign in as the account that owns the Drive folder and the Sheet.
3. Create a **Drive folder** for the report Docs and a **Google Sheet** for the log; copy both IDs into `scripts/.env` (see `.env.example`). Add the exact header row to the sheet — see `scripts/README.md`.

### 4.4 Slack (the summary post)

1. `api.slack.com/apps` → **Create New App → Blank app** → pick the workspace.
2. **OAuth & Permissions → Bot Token Scopes** — add `chat:write` and `chat:write.public`.
3. **Install to Workspace** → copy the Bot User OAuth Token (`xoxb-...`).
4. In n8n: **Credentials → Create → Slack API** → **Access Token** = the bot token. Select the target channel in the Slack node.

> **Note from the build:** if Slack posts under an unexpected display name, the cleanest fix is a fresh workspace + a fresh Blank app named exactly how you want it to appear, then use that app's bot token. See the troubleshooting table in `BUILD_GUIDE.md`.

**All four credentials done = the system can talk to every service it needs.** Rebuild the 19 nodes from `BUILD_GUIDE.md` (or import the workflow JSON once exported into `workflows/`), pick the matching credential in each node, and you're ready to run.

---

## 5. Step-by-step flow — what was built and how to test it

### The full picture, in order

```
Someone submits the form (YouTube URL + email)
  → Extract Video ID          pull the 11-char ID out of any YouTube link shape
  → Get Video Details         YouTube API: title, views, likes, comment count
  → Enough Comments?          at least 20 comments? no → stop cleanly (skip branch)
  → Get Comments              YouTube API: fetch the comments (capped at 10 for the demo)
  → Flatten                   tidy them into a clean list + attach the video stats
  → Score        (AI)         label each comment: sentiment, topic, unanswered?
  → Aggregate                 tally sentiment %, rank themes, collect questions
  → Analyse      (AI)         write the report: verdict, quotes, themes, ideas
  → Parse Report              pull the report JSON out of the AI response
  → Check        (AI)         fact-check every quote and number → pass / issues
  → Parse Review              read the verdict
  → Review Passed?            true → tag "sent"   ·   false → tag "sent_unreviewed"
  → Prepare Delivery          decode text, build the Doc / email / Slack / chart payloads
  → Create Doc → Insert Text  a fresh Google Doc with the full report
  → Send Report Email         HTML email to the address from the form
  → Append Log Row            one new row in the Google Sheet history
  → Post to Slack             headline + Doc link + sentiment doughnut chart

If a video has fewer than 20 comments (or comments disabled) → the "Enough Comments?" gate
stops it before any AI runs, so no empty report is ever produced.
```

### What the user actually experiences

They fill in two fields and submit. Roughly a minute later: a **Google Doc** appears with the verdict, real viewer quotes grouped by sentiment, the top themes, the unanswered questions, and five content ideas; the same report arrives as an **email**; a **row is added to a Google Sheet** so a history builds up over time; and a **Slack message** posts a one-line summary with a link to the Doc and a doughnut chart of the positive / neutral / negative split. Every quote and number in that report has already been fact-checked against the source comments before they see it.

### To test it yourself, in short

1. Rebuild the workflow from `BUILD_GUIDE.md` (or import the JSON into n8n once it's in `workflows/`), and connect the four credentials from section 4.
2. Open the **Form Trigger**, copy its **Test URL** (or activate the workflow for the **Production URL**), paste a YouTube URL + your email, and submit.
3. Watch the run: collect → score → analyse → check → deliver (about a minute).
4. Confirm all four deliverables: the **Google Doc** opens with grounded quotes and five ideas; the **email** arrives; a new **row** appears in the Google Sheet; the **Slack** message shows the summary and doughnut chart.
5. Optional: submit a video with **comments disabled or fewer than 20 comments** — it should hit the skip branch and produce no report.

### Known bugs to watch for if the workflow is modified

- **A stray leading `=` in a query-param value** makes the YouTube API return an empty `items` list with a 200 OK. In the value box type only `{{ $json.videoId }}` — the field's own `=` toggle adds the leading `=`.
- **`commentCount` comes back as a string** and is omitted entirely when comments are disabled. The gate uses `Number($json.items[0].statistics.commentCount ?? 0)` so a missing count is treated as `0` and correctly fails.
- **Google Docs returns the new doc's ID as `id`, not `documentId`** — downstream nodes must read `{{ $json.id }}`.
- **Google Sheets "Not a valid Sheet ID"** almost always means the spreadsheet ID was pasted into the **Sheet** field. The spreadsheet ID goes in **Document (By ID)**; the tab is picked in **Sheet (From list)**.
- **Analyse returning truncated JSON** means `max_tokens` is too low for the report — set it to `8000` on that node.

The full symptom → cause → fix table is in `BUILD_GUIDE.md`.

### Current status

- The single 19-node workflow has been built and run end to end on the n8n Cloud instance, delivering all four outputs (Doc + email + Sheet row + Slack post with chart) correctly, on Claude Haiku with the 10-comment demo cap.
- **Parked for the demo (documented in `RUNBOOK.md`):** scaling past 10 comments (pagination + batching), and the reviewer's "on fail, re-analyse once, then send as unreviewed" retry loop — for now a failed check simply tags the report `sent_unreviewed`.
- **Before sharing or recording:** rotate any API key that appeared on screen (the YouTube key, the Anthropic key, and the Slack `xoxb-…` token), and keep every secret in n8n credentials rather than inline in nodes.
- **To publish `workflows/*.json`:** open the workflow in n8n → **⋯ → Download** → drop the exported JSON into `workflows/`. See `workflows/README.md`.

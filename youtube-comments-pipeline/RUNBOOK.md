# Runbook - YouTube Audience Intelligence Report (n8n)

Build guide for the pipeline described in `FullSpec_YouTubeAudience_Demo_Brief.pdf`.
Follow the phases in order. Each phase ends in something you can test on its own before moving on.

---

## 0. What you are building (one-line summary)

A single n8n workflow: **paste a YouTube URL + email into a form → ~1 minute later a Google Doc, a Gmail, a Slack message with a sentiment chart, and a Google Sheet row appear**, describing what viewers liked, complained about, asked (unanswered), and five content ideas drawn from the comments. Plus a small **error-handler workflow**.

### Pipeline stages

```
Form Trigger (URL + email)
      │
   ┌──▼─────────────────────────────────────────────┐
   │ COLLECT                                          │
   │  Code: extract videoId from URL                  │
   │  HTTP: YouTube videos.list  (details/stats)      │
   │  Gate: comments disabled? / < 20 comments? ──────┼──► short "no report" reply → END
   │  HTTP: YouTube commentThreads.list (paginate 500)│
   └──┬──────────────────────────────────────────────┘
      │
   ┌──▼──────────────────────────────────────────────┐
   │ SCORE  (cheap fast model, batches of 50, parallel)│
   │  each comment → {sentiment, topic, unanswered?}  │
   └──┬──────────────────────────────────────────────┘
      │  (aggregate all batches + compute counts/%)
   ┌──▼──────────────────────────────────────────────┐
   │ ANALYSE (strong model, 1 call)                   │
   │  → structured report (verdict, sentiment, themes,│
   │    unanswered Qs, 5 content ideas, method note)  │
   └──┬──────────────────────────────────────────────┘
      │
   ┌──▼──────────────────────────────────────────────┐
   │ CHECK (mid model) - verify quotes + numbers      │
   │  fail → re-run ANALYSE with notes (max 1 retry)  │
   │  2nd fail → send anyway, status=sent_unreviewed  │
   └──┬──────────────────────────────────────────────┘
      │
   ┌──▼──────────────────────────────────────────────┐
   │ DELIVER (in parallel where possible)             │
   │  Google Doc (shared folder) → doc_link           │
   │  Gmail (HTML) │ QuickChart doughnut │ Slack post  │
   │  Google Sheet: append log row                    │
   └─────────────────────────────────────────────────┘
```

---

## 1. Prerequisites & credentials (do this first - Day 0)

Set up every credential before building nodes; a missing credential is the #1 time-sink mid-build.

| #   | Credential in n8n                               | What you need                                                            | Notes                                                                                                      |
| --- | ----------------------------------------------- | ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| 1   | **YouTube Data API v3** (generic HTTP, API key) | API key from Google Cloud Console                                        | Enable "YouTube Data API v3". 10,000 units/day ≈ ~100 reports/day. Use as a query param `key=`, not OAuth. |
| 2   | **OpenAI** (n8n free credits credential)        | The pre-configured "free OpenAI API credits" credential on your instance | See §2 about model names.                                                                                  |
| 3   | **Google Docs** (OAuth2)                        | Google account                                                           | Same account for Docs/Sheets/Gmail is fine.                                                                |
| 4   | **Google Sheets** (OAuth2)                      | Same Google account                                                      | Create the log sheet ahead of time (§7).                                                                   |
| 5   | **Gmail** (OAuth2)                              | Same Google account                                                      | Sends the HTML report.                                                                                     |
| 6   | **Slack** (OAuth2 or bot token)                 | Slack workspace + bot invited to `#audience-reports`                     | Bot needs `chat:write` and, if uploading the chart as a file, `files:write`.                               |

**Google Cloud setup for the API key:** create/select a project → APIs & Services → Enable APIs → "YouTube Data API v3" → Credentials → Create Credentials → API key. Restrict the key to the YouTube Data API.

**Create the shared Google Doc folder** and the **Google Sheet log** now, and copy their IDs - you'll paste them into nodes later.

---

## 2. Model names — confirmed real on this instance

The brief's model names turned out to be **real models exposed by the "n8n free OpenAI API credits" credential** on this instance (confirmed in the Model dropdown: `GPT-5-MINI`, `GPT-5-NANO`, `GPT-5.6-LUNA`, plus image models). No mapping needed — use them exactly per the brief:

| Model (dropdown) | Role in pipeline | Why |
| ---------------- | ---------------- | --- |
| `GPT-5-NANO`   | **SCORE** — 50-comment batches, runs many times in parallel | cheapest + fastest |
| `GPT-5.6-LUNA` | **ANALYSE** — one big reasoning/writing call                | strongest reasoning + writing |
| `GPT-5-MINI`   | **CHECK** — verification/reviewer                           | mid-tier, good at strict checking |

The OpenAI "Message a Model" node on this instance supports **Output Format → JSON Schema (Strict)**, which we use to force structured output instead of prompt-only JSON.

---

## 3. Phase 1 - COLLECT (Day 1)

### 3.1 Form Trigger

- Node: **n8n Form Trigger**.
- Two fields:
  - `videoUrl` - text, required, label "YouTube video URL".
  - `email` - email, required, label "Send report to".
- Set a friendly form title/description. Note the production form URL for the demo.

### 3.2 Extract video ID (Code node)

- Node: **Code** (JavaScript). Handle all common URL shapes: `watch?v=`, `youtu.be/`, `/shorts/`, `/embed/`, and extra query params.

> Form field labels are the data keys. With labels `YouTube Video URL` and `Send report to`, read them as `$json['YouTube Video URL']` and `$json['Send report to']` (the code below already does this).

```js
const url = ($json['YouTube Video URL'] || '').trim();
const email = $json['Send report to'];
const patterns = [
  /[?&]v=([a-zA-Z0-9_-]{11})/,
  /youtu\.be\/([a-zA-Z0-9_-]{11})/,
  /\/shorts\/([a-zA-Z0-9_-]{11})/,
  /\/embed\/([a-zA-Z0-9_-]{11})/,
];
let videoId = null;
for (const re of patterns) {
  const m = url.match(re);
  if (m) {
    videoId = m[1];
    break;
  }
}
if (!videoId) {
  throw new Error("Could not extract a valid 11-char video ID from: " + url);
}
return [{ json: { videoId, email, videoUrl: url } }];
```

### 3.3 Fetch video details (HTTP Request)

- **GET** `https://www.googleapis.com/youtube/v3/videos`
- Query params: `part=snippet,statistics,status` · `id={{ $json.videoId }}` · `key=<API key>` (or via generic-credential header).
- `part=status` lets you read `items[0].status` and detect issues; `snippet`/`statistics` give title, channel, publishedAt, viewCount, likeCount, commentCount.
- If `items` is empty → invalid/private/deleted video → route to the "no report" reply.

### 3.4 Early gate - comments disabled or too few (IF / Switch node)

Decide **before** paginating comments (saves quota and time):

- **Comments disabled:** `statistics.commentCount` is absent, OR the first `commentThreads.list` call returns HTTP 403 with reason `commentsDisabled`. Practical approach: read `commentCount`; if missing/zero, treat as disabled.
- **Too few:** `commentCount < 20`.
- Either condition → **Set** `status = skipped_too_few_comments` → send the short "no report produced, here's why" reply (Gmail and/or Form completion message) → append a Sheet row with the skip status → **END**. Do not proceed to scoring.

### 3.5 Fetch comments with pagination (Loop up to 500)

- **GET** `https://www.googleapis.com/youtube/v3/commentThreads`
- Query params: `part=snippet,replies` · `videoId={{ $json.videoId }}` · `order=relevance` · `maxResults=100` · `key=<API key>` · `pageToken={{ $json.nextPageToken }}` (empty on first call).
- Loop with **HTTP Request → check `nextPageToken` → repeat**, stopping when: no `nextPageToken`, OR you have **500 top-level comments** (5 pages × 100). Use n8n's pagination option on the HTTP node if available, capped at 5 pages, else a manual loop with an IF.
- From each item keep: `textDisplay` (or `textOriginal`), `authorDisplayName`, `likeCount`, `totalReplyCount` (`snippet.topLevelComment.snippet...` and `snippet.totalReplyCount`).
- Wrap 403/`commentsDisabled` here in an **error branch** back to the "no report" path (belt-and-suspenders with the early gate).

**Phase 1 test:** run the form with a known video; confirm you get clean `{videoId, details, [up to 500 comments]}`. Confirm the comments-disabled test video routes to the skip reply.

---

## 4. Phase 2 - SCORE (Day 2)

> Prior node (end of COLLECT) is a **Flatten** Code node that outputs a single item: `{ email, video, commentsFetched, comments[] }` where each comment is `{ id, text, likeCount, replies }`. See the actual flatten code in the build log — it pulls video details via `$('HTTP Request').first()` and email via `$('Code in JavaScript').first()`.

### 4.1 Batch the comments

- Node: **Code** (Run Once for All Items) - chunk the comment array into groups.
- ⚠️ **Batch size = 25, not 50.** `GPT-5-NANO` is a reasoning model whose reasoning tokens share the output budget; at 50/batch it returned empty `results: []` for many batches (only 147/500 scored). 25/batch reliably scores ~488/500.

```js
const data = $input.first().json;
const comments = data.comments;
const size = 25;                 // 25, NOT 50 — see note above
const out = [];
for (let i = 0; i < comments.length; i += size) {
  out.push({ json: { batchIndex: out.length, batch: comments.slice(i, i + size) } });
}
return out; // 500 comments -> 20 items
```

### 4.2 Score each batch (OpenAI "Message a Model" node — `GPT-5-NANO`)

Runs once per batch item (20 calls, sequential in n8n). Critical settings learned during the build:

| Setting | Value | Why |
| ------- | ----- | --- |
| Model | `GPT-5-NANO` | cheapest/fastest |
| Message 1 | Role `User`, Prompt in **Expression** mode (leading `=`) | see prompt below |
| Simplify Output | ON | |
| Options → Output Format | **JSON Schema (recommended)**, Name `comment_scores`, **Strict ON** | forces structure |
| Options → Reasoning → **Effort** | **Low** | Medium was very slow; Low is fast and accurate enough |
| Options → Reasoning → **Summary** | **None** | skip reasoning-summary text = faster |
| Options → **Maximum Number of Tokens** | **16000** | reasoning + output share this budget; too low → empty `results` |

**Output Format schema** (paste into the Schema box):

```json
{
  "type": "object",
  "properties": {
    "results": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": { "type": "string" },
          "sentiment": { "type": "string", "enum": ["positive", "neutral", "negative"] },
          "topic": { "type": "string" },
          "unanswered_question": { "type": "boolean" }
        },
        "required": ["id", "sentiment", "topic", "unanswered_question"],
        "additionalProperties": false
      }
    }
  },
  "required": ["results"],
  "additionalProperties": false
}
```

**Prompt** (Expression mode — note leading `=`):

```
=You are a precise comment classifier. Classify every comment in the list below.
Return ONLY a valid JSON object, no markdown, no commentary, in exactly this shape:
{"results": [ {"id": "<id>", "sentiment": "positive|neutral|negative", "topic": "<1-3 word tag>", "unanswered_question": true|false} ]}

Rules:
- One result object per input comment, keeping the same "id".
- sentiment is exactly one of positive, neutral, negative.
- unanswered_question is true ONLY if the comment asks a genuine, non-rhetorical question a creator would want to answer, otherwise false.

Comments (JSON):
{{ JSON.stringify($json.batch) }}
```

### 4.3 Aggregate + compute counts (Code node, after all batches)

- The scored data sits at `output[0].content[0].text.results` per item (Responses-API shape), but `output[0]` is not always the message — **scan the `output` array** (see extractor below) rather than hard-indexing.
- Join scores back onto the original comment text (kept in the Flatten node, keyed by `id`), compute sentiment counts/percentages, rank topics, collect unanswered questions.

```js
// 1. Robust extraction across all batch outputs
function extractResults(j) {
  if (Array.isArray(j.results)) return j.results;
  if (Array.isArray(j.message?.results)) return j.message.results;
  const outs = j.output || j.response?.output || [];
  for (const o of outs) {
    for (const cc of (o.content || [])) {
      const t = cc.text;
      if (t && Array.isArray(t.results)) return t.results;
      if (typeof t === 'string') { try { const p = JSON.parse(t); if (Array.isArray(p.results)) return p.results; } catch {} }
    }
  }
  return [];
}
let scored = [];
for (const it of $input.all()) scored = scored.concat(extractResults(it.json));

// 2. Join with original text from the Flatten node
const flat = $('Flatten the response').first().json;
const byId = {};
for (const c of flat.comments) byId[c.id] = c;
const comments = scored.map(s => ({
  id: s.id, text: byId[s.id]?.text ?? '', likeCount: byId[s.id]?.likeCount ?? 0,
  replies: byId[s.id]?.replies ?? 0, sentiment: s.sentiment, topic: s.topic,
  unanswered_question: s.unanswered_question,
}));

// 3. Counts + %s
const total = comments.length;
const c = { positive: 0, neutral: 0, negative: 0 };
for (const x of comments) c[x.sentiment] = (c[x.sentiment] || 0) + 1;
const pct = k => total ? Math.round((c[k] / total) * 1000) / 10 : 0;

// 4. Topics ranked + unanswered
const topics = {};
for (const x of comments) topics[x.topic] = (topics[x.topic] || 0) + 1;
const topThemes = Object.entries(topics).map(([topic, count]) => ({ topic, count })).sort((a, b) => b.count - a.count);
const unansweredQuestions = comments.filter(x => x.unanswered_question);

return [{ json: {
  email: flat.email, video: flat.video,
  counts: { total, positive: c.positive, neutral: c.neutral, negative: c.negative,
    positive_pct: pct('positive'), neutral_pct: pct('neutral'), negative_pct: pct('negative') },
  topThemes, unansweredQuestions, comments,
} }];
```

**Phase 2 result (Rick Astley test):** `total: 488/500`, positive 53.9% / neutral 42% / negative 4.1%, top theme "rickroll". Percentages sum to ~100 ✓.

> Hardening (post-demo): auto-retry any batch that returns empty `results`, or `log()` a warning when `scored.length < commentsFetched`, so silent drops are visible.

---

## 5. Phase 3 - ANALYSE (Day 3)

### 5.1 Report writer (OpenAI node - ANALYSE model, one call)

Input to the prompt: video details, the scored comments (with text), and the computed counts. Ask for **structured JSON** that maps 1:1 to the Doc layout in §8 - this makes Doc/Gmail/Slack rendering trivial and makes the Check step mechanical.

**Analysis prompt:**

```
You are an audience-insights analyst. Using ONLY the data provided, write a report.
Every quoted comment MUST be copied verbatim from the data - never invent or paraphrase a quote.
Every number MUST match the provided counts. Output strict JSON in this shape:

{
  "verdict": "three sentences on how the video landed",
  "sentiment": {
    "positive_pct": n, "neutral_pct": n, "negative_pct": n,
    "positive_quotes": ["...","...","..."],   // 3, verbatim
    "neutral_quotes":  ["...","...","..."],
    "negative_quotes": ["...","...","..."]
  },
  "themes": [ { "name": "...", "count": n, "note": "one line" } ],   // 5 to 8, ranked by frequency
  "unanswered_questions": [ { "question": "verbatim", "suggested_answer": "one line" } ], // up to 10
  "content_ideas": [ { "idea": "...", "from_comment": "verbatim comment that inspired it" } ], // exactly 5
  "method_note": "how many comments were read and when"
}

VIDEO: {{ ...details }}
COUNTS: {{ ...counts }}
SCORED_COMMENTS: {{ ...scored comments with text }}
```

**Phase 3 test:** valid JSON, 5 content ideas, themes ranked descending by count, quotes present in the input.

---

## 6. Phase 4 - CHECK (Day 3)

### 6.1 Reviewer (OpenAI node - CHECK model)

Give it the Analyse JSON **and** the source data. **Reviewer rubric prompt:**

```
You are a fact-checker. Verify the report against the source data. Return strict JSON:
{ "pass": true|false, "issues": ["short description", ...] }

Fail (pass=false) if ANY of these are true:
 - A quoted comment (in sentiment quotes, unanswered_questions, or content_ideas.from_comment)
   does not appear verbatim in the source comments.
 - Any percentage or count in the report disagrees with the provided COUNTS.
 - Fewer than 5 content ideas, or themes not ranked by count.
List each problem briefly in "issues". If everything checks out, pass=true, issues=[].

REPORT: {{ analyse output }}
COUNTS: {{ counts }}
SOURCE_COMMENTS: {{ comments with text }}
```

> Tip: also add a **deterministic Code check** for quotes (string-contains against the source array) - cheaper and more reliable than trusting the model for exact-match verification. Use the model for the judgement calls.

### 6.2 Retry loop (IF + counter)

- Maintain a `reviewAttempts` counter (Set node, start 0).
- `pass = true` → continue to Deliver with `status = sent`.
- `pass = false` **and** `reviewAttempts < 1` → increment counter → re-run **Analyse** with the reviewer's `issues` appended to the prompt ("Fix these problems: …") → back into Check.
- `pass = false` **and** `reviewAttempts >= 1` (second failure) → continue to Deliver anyway with `status = sent_unreviewed`, and add a visible "⚠ Unreviewed" note at the top of the Doc/email.

**Phase 4 test:** deliberately corrupt a quote in test data → confirm one retry fires → confirm second failure still delivers with `sent_unreviewed`.

---

## 7. Google Sheet log - create it now

One sheet, header row exactly:

```
run_at | requested_by | video_url | video_title | channel |
views | likes | comments_total | comments_analysed |
positive_pct | neutral_pct | negative_pct |
top_theme | doc_link | status
```

`status` ∈ `sent` · `sent_unreviewed` · `skipped_too_few_comments`. Copy the Sheet ID for the append node.

---

## 8. Phase 5 - DELIVER (Days 4–5)

Order the four outputs so `doc_link` exists before Slack/Sheet reference it. Build: **Doc → (Gmail ∥ Chart→Slack) → Sheet append**.

### 8.1 Google Doc (shared folder)

- Node: **Google Docs → Create** (in the shared folder) then **Update** to insert content, or create from a formatted template.
- Doc layout (from the brief):
  1. **Title line:** title · channel · publish date · views · likes · comment count · like-to-view ratio (`likes/views`).
  2. **Verdict** - 3 sentences.
  3. **Sentiment** - pos/neu/neg % + bar, 3 quoted comments each.
  4. **Themes** - 5–8, each count + one-line description.
  5. **Unanswered questions** - up to 10, verbatim + suggested one-line answer.
  6. **Content ideas** - 5, each tied to its source comment.
  7. **Method note** - how many comments read and when.
  - If `status = sent_unreviewed`, prepend a "⚠ Unreviewed - automated report" banner.
- Capture the returned Doc URL → `doc_link`.

### 8.2 Gmail (HTML)

- Node: **Gmail → Send**. To: `{{ email }}`. Subject: `Audience report - {{ video_title }}`.
- Body: HTML rendering of the same sections (formatted, not a JSON dump). Include the `doc_link`.

### 8.3 QuickChart doughnut + Slack

- **QuickChart** node → doughnut chart of positive/neutral/negative. Config sketch:

```json
{ "type": "doughnut",
  "data": { "labels": ["Positive","Neutral","Negative"],
    "datasets": [{ "data": [{{positive_pct}}, {{neutral_pct}}, {{negative_pct}}] }] } }
```

- **Slack → Post message** to `#audience-reports`: headline numbers (title, views, comment count, sentiment split, top theme) + the chart (attach the QuickChart image or post its URL) + link to the Doc.

### 8.4 Google Sheet append

- Node: **Google Sheets → Append Row**, mapping every column in §7. `run_at` = now (ISO), `requested_by` = email, `doc_link` from §8.1, `status` from the pipeline.

**Phase 5 test:** one full run end to end; verify Doc opens, email arrives formatted, Slack shows the chart + Doc link, and a correct Sheet row appears.

---

## 9. Phase 6 - Error handler workflow (separate export)

A second workflow, set as the **Error Workflow** for the main one (Workflow Settings → Error Workflow).

- Trigger: **Error Trigger**.
- Action: post to Slack `#audience-reports` (or a dev channel) with the failed workflow name, the node that failed, the error message, and the input video URL if available.
- Optional: append a row to the Sheet with `status = error` for traceability.
- This is one of the two required JSON deliverables (report generator + error handler).

---

## 10. Testing checklist (Days 4–5)

Run all ten provided test videos through the form. For each, confirm:

- [ ] Doc created, opens, all seven sections present and readable.
- [ ] Email arrives, formatted HTML, Doc link works.
- [ ] Slack message with chart + Doc link in `#audience-reports`.
- [ ] Sheet row correct; `views`/`comments_total` **match what YouTube shows**.
- [ ] **Spot-check 2–3 quoted comments per report** against the actual YouTube comment section - they must be verbatim and real.
- [ ] The comments-disabled test video → clean skip reply, `status = skipped_too_few_comments`, no crash.
- [ ] A `< 20 comments` video → same skip path.
- [ ] Full run completes in ~1 minute.
- [ ] Identify the **three competitor videos that produce the strongest reports** - note them for the demo script.

---

## 11. Demo script (deliverable)

Write a short live-run script:

1. Open the form. Ask the prospect to **name a competitor's recent video**; paste its URL + your email.
2. While it runs (~1 min), narrate the stages (collect → score → analyse → verify → deliver).
3. Open the Doc live: read the verdict, show grounded quotes ("every quote is real and verified"), the five content ideas.
4. Show the Slack message + chart, and the Sheet row ("each run becomes a competitor-tracker history").
5. Have the **three known-strong competitor videos** pre-vetted as reliable fallbacks.

---

## 12. Handover notes (deliverable)

Document how to switch the trigger, per the brief:

- **Slack slash command** instead of the form: replace the Form Trigger with a Slack Trigger / webhook that reads the URL from the command text; reply in-channel.
- **Weekly schedule over a channel's newest videos:** replace the Form Trigger with a **Schedule Trigger** → YouTube `search.list` (or `playlistItems.list` on the channel's uploads playlist) for the newest video IDs → feed each into the same Collect→Deliver path.
- Note credentials, the API key location, quota (~100 reports/day), the model-role mapping from §2, and the Sheet/folder IDs.

---

## 13. Final deliverables checklist (from the brief)

- [ ] `report-generator.workflow.json` (exported)
- [ ] `error-handler.workflow.json` (exported)
- [ ] 10 reports produced from the test list, quotes spot-checked
- [ ] Google Sheet log populated; comments-disabled case handled cleanly
- [ ] Slack summary + chart delivered for each run
- [ ] Demo script (with 3 strong competitor videos)
- [ ] Handover notes (trigger swaps, credentials, quota, model mapping)

---

### Open items to confirm with the client / instance owner

1. **Exact real model IDs** available on the free-OpenAI-credits credential (fill in §2). The brief's names are placeholders.
2. **Shared Google Doc folder ID** and **Google Sheet ID**.
3. **Slack channel** confirmed as `#audience-reports` and the bot invited with `chat:write`/`files:write`.
4. The `templates/` community template ("YouTube Video Comment Analysis Agent") - not present in this repo; obtain it if you want to start from it rather than build Collect/Deliver from scratch.

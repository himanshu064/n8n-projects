# Build Guide - YouTube Audience Report (n8n + Claude Haiku)

A single n8n workflow: **paste a YouTube URL + email into a form → ~1 minute later a Google Doc, a Gmail, a Google Sheet row, and a Slack message with a sentiment doughnut appear**, describing what viewers liked, complained about, asked (unanswered), and five content ideas drawn from the comments.

This is the **demo configuration**:

- **AI:** Anthropic **Claude Haiku** (`claude-haiku-4-5`), called via **HTTP Request** nodes (n8n has no native Anthropic action node). Structured JSON via `output_config.format`.
- **Scope:** **10 comments** so every AI step is one fast call.
- **Reviewer retry:** the brief's second-pass re-analyse loop is left out; CHECK just tags `sent` vs `sent_unreviewed`. (Full retry documented in `RUNBOOK.md`.)

---

## Final node graph

```
On form submission (Form Trigger)
 → Extract Video ID          (Code)
 → Get Video Details         (HTTP: YouTube videos)
 → Enough Comments?          (IF ≥ 20)            false → (skip / open)
 → Get Comments              (HTTP: YouTube commentThreads, maxResults=10)
 → Flatten                   (Code - trim + slice to 10)
 → Score                     (HTTP: Anthropic Haiku, structured JSON)
 → Aggregate                 (Code - counts, themes, join text)
 → Analyse                   (HTTP: Anthropic Haiku, structured report)
 → Parse Report              (Code)
 → Check                     (HTTP: Anthropic Haiku, {pass, issues})
 → Parse Review              (Code)
 → Review Passed?            (IF)
        true  → Status: sent            (Set)
        false → Status: sent_unreviewed (Set)
 → Prepare Delivery          (Code - decode entities, build doc/email/slack/chart)
 → Create Doc → Insert Doc Text (Google Docs)
 → Send Report Email         (Gmail, HTML)
 → Append Log Row            (Google Sheets)
 → Post to Slack             (Slack, headline + Doc link + QuickChart doughnut)
```

---

## 0. Credentials

| Credential              | Type                                      | Notes                                                                                                                 |
| ----------------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| **YouTube Data API v3** | API key (query param)                     | Google Cloud Console → enable "YouTube Data API v3" → create API key. Restrict it to that API.                        |
| **Anthropic**           | **Header Auth** (generic)                 | Header **Name** `x-api-key`, **Value** = your Anthropic key. Non-secret `anthropic-version` header is added per-node. |
| **Google**              | OAuth2 (n8n Cloud: "Sign in with Google") | One credential covers Docs, Sheets, Gmail.                                                                            |
| **Slack**               | Bot token (`xoxb-…`)                      | Slack app with scopes `chat:write` + `chat:write.public`.                                                             |

Also create a **Google Drive folder** for the report Docs and a **Google Sheet** for the log; keep both IDs handy (e.g. in `.env`).

**Google Sheet header row (row 1, exact names):**

```
run_at | requested_by | video_url | video_title | channel | views | likes | comments_total | comments_analysed | positive_pct | neutral_pct | negative_pct | top_theme | doc_link | status
```

---

## Phase 1 - Collect

### 1. On form submission (Form Trigger → "On new n8n Form event")

- Title: `YouTube Audience Report`, Authentication: None.
- Field 1: **Text**, label `YouTube Video URL`, required.
- Field 2: **Email**, label `Send report to`, required.
- Field labels become data keys: `$json['YouTube Video URL']`, `$json['Send report to']`.

### 2. Extract Video ID (Code, Run Once for All Items)

```js
const url = ($json["YouTube Video URL"] || "").trim();
const email = $json["Send report to"];
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
if (!videoId)
  throw new Error("Could not extract a valid 11-char video ID from: " + url);
return [{ json: { videoId, email, videoUrl: url } }];
```

### 3. Get Video Details (HTTP Request)

- **GET** `https://www.googleapis.com/youtube/v3/videos`
- Send Query Parameters → Using Fields Below:
  - `part` = `snippet,statistics,status`
  - `id` = `={{ $json.videoId }}` ⚠️ in the value box type only `{{ $json.videoId }}` - the field's own `=` toggle adds the leading `=`. A stray leading `=` in the value makes YouTube return empty `items`.
  - `key` = your YouTube API key

### 4. Enough Comments? (IF)

- Value 1: `={{ Number($json.items[0].statistics.commentCount ?? 0) }}` · Type **Number** · Operation **Greater than or equal to** · Value 2 `20`.
- `Number(... ?? 0)` matters: YouTube returns `commentCount` as a **string**, and omits it entirely when comments are disabled → treated as `0`, correctly failing the gate.
- **TRUE** → continue. **FALSE** → optional "no report" reply (leave open for the demo).

### 5. Get Comments (HTTP Request) - on the TRUE branch

- **GET** `https://www.googleapis.com/youtube/v3/commentThreads`
- Query params:
  - `part` = `snippet,replies`
  - `videoId` = `={{ $json.items[0].id }}`
  - `order` = `relevance`
  - `maxResults` = `10` ← demo cap at the source
  - `key` = your YouTube API key

### 6. Flatten (Code, Run Once for All Items)

```js
const pages = $input.all();
const comments = [];
for (const page of pages) {
  for (const t of page.json.items || []) {
    const tc = t.snippet?.topLevelComment;
    const s = tc?.snippet;
    if (!s) continue;
    comments.push({
      id: tc.id,
      text: s.textDisplay,
      likeCount: s.likeCount ?? 0,
      replies: t.snippet?.totalReplyCount ?? 0,
    });
  }
}
const v = $("Get Video Details").first().json.items[0];
const video = {
  videoId: v.id,
  title: v.snippet.title,
  channel: v.snippet.channelTitle,
  publishedAt: v.snippet.publishedAt,
  views: Number(v.statistics.viewCount ?? 0),
  likes: Number(v.statistics.likeCount ?? 0),
  commentCountTotal: Number(v.statistics.commentCount ?? 0),
};
const demo = comments.slice(0, 10); // demo cap (matches maxResults=10)
return [
  {
    json: {
      email: $("Extract Video ID").first().json.email,
      video,
      commentsFetched: demo.length,
      comments: demo,
    },
  },
];
```

---

## The Anthropic HTTP pattern (Score, Analyse, Check)

Every AI node is an **HTTP Request**:

- **POST** `https://api.anthropic.com/v1/messages`
- **Authentication:** Generic Credential Type → **Header Auth** → your Anthropic key.
- **Send Headers:** ON → `anthropic-version` = `2023-06-01`.
- **Send Body:** ON → Body Content Type **JSON** → Specify Body **Using JSON** → field in **Expression** mode → paste the body.
- **Response:** the model's JSON is a string at `content[0].text`.

### 7. Score (HTTP → Haiku)

JSON body (Expression):

```
={{ {
  "model": "claude-haiku-4-5",
  "max_tokens": 4096,
  "messages": [
    { "role": "user", "content": "You are a precise comment classifier. Classify every comment below. Return a JSON object {results:[...]} with one element per comment: id (same as input), sentiment (positive|neutral|negative), topic (1-3 words), unanswered_question (true only for a genuine non-rhetorical question a creator would answer).\n\nComments (JSON):\n" + JSON.stringify($json.comments) }
  ],
  "output_config": { "format": { "type": "json_schema", "schema": {
    "type": "object",
    "properties": { "results": { "type": "array", "items": {
      "type": "object",
      "properties": {
        "id": { "type": "string" },
        "sentiment": { "type": "string", "enum": ["positive","neutral","negative"] },
        "topic": { "type": "string" },
        "unanswered_question": { "type": "boolean" }
      },
      "required": ["id","sentiment","topic","unanswered_question"],
      "additionalProperties": false
    } } },
    "required": ["results"], "additionalProperties": false
  } } }
} }}
```

### 8. Aggregate (Code, Run Once for All Items)

```js
function extractResults(j) {
  if (Array.isArray(j.results)) return j.results;
  const at = j.content?.[0]?.text; // Anthropic shape
  if (typeof at === "string") {
    try {
      const p = JSON.parse(at);
      if (Array.isArray(p.results)) return p.results;
    } catch {}
  }
  return [];
}
let scored = [];
for (const it of $input.all()) scored = scored.concat(extractResults(it.json));

const flat = $("Flatten").first().json;
const byId = {};
for (const c of flat.comments) byId[c.id] = c;
const comments = scored.map((s) => ({
  id: s.id,
  text: byId[s.id]?.text ?? "",
  likeCount: byId[s.id]?.likeCount ?? 0,
  replies: byId[s.id]?.replies ?? 0,
  sentiment: s.sentiment,
  topic: s.topic,
  unanswered_question: s.unanswered_question,
}));

const total = comments.length;
const c = { positive: 0, neutral: 0, negative: 0 };
for (const x of comments) c[x.sentiment] = (c[x.sentiment] || 0) + 1;
const pct = (k) => (total ? Math.round((c[k] / total) * 1000) / 10 : 0);

const topics = {};
for (const x of comments) topics[x.topic] = (topics[x.topic] || 0) + 1;
const topThemes = Object.entries(topics)
  .map(([topic, count]) => ({ topic, count }))
  .sort((a, b) => b.count - a.count);
const unansweredQuestions = comments.filter((x) => x.unanswered_question);

return [
  {
    json: {
      email: flat.email,
      video: flat.video,
      counts: {
        total,
        positive: c.positive,
        neutral: c.neutral,
        negative: c.negative,
        positive_pct: pct("positive"),
        neutral_pct: pct("neutral"),
        negative_pct: pct("negative"),
      },
      topThemes,
      unansweredQuestions,
      comments,
    },
  },
];
```

### 9. Analyse (HTTP → Haiku) - writes the report

JSON body (Expression):

```
={{ {
  "model": "claude-haiku-4-5",
  "max_tokens": 8000,
  "messages": [
    { "role": "user", "content": "You are an audience-insights analyst. Using ONLY the data provided, write the report. Copy every quoted comment verbatim from SCORED_COMMENTS text, character-for-character including HTML entities like &#39; and <br>. Use the exact numbers in COUNTS. Rank themes by frequency (up to 5). Give exactly 5 content_ideas, each tied to a real comment. Up to 10 unanswered_questions, verbatim.\n\nVIDEO:\n" + JSON.stringify($json.video) + "\n\nCOUNTS:\n" + JSON.stringify($json.counts) + "\n\nTOP_THEMES:\n" + JSON.stringify($json.topThemes) + "\n\nSCORED_COMMENTS:\n" + JSON.stringify($json.comments) }
  ],
  "output_config": { "format": { "type": "json_schema", "schema": {
    "type": "object",
    "properties": {
      "verdict": { "type": "string" },
      "sentiment": { "type": "object", "properties": {
        "positive_pct": { "type": "number" }, "neutral_pct": { "type": "number" }, "negative_pct": { "type": "number" },
        "positive_quotes": { "type": "array", "items": { "type": "string" } },
        "neutral_quotes": { "type": "array", "items": { "type": "string" } },
        "negative_quotes": { "type": "array", "items": { "type": "string" } }
      }, "required": ["positive_pct","neutral_pct","negative_pct","positive_quotes","neutral_quotes","negative_quotes"], "additionalProperties": false },
      "themes": { "type": "array", "items": { "type": "object",
        "properties": { "name": { "type": "string" }, "count": { "type": "number" }, "note": { "type": "string" } },
        "required": ["name","count","note"], "additionalProperties": false } },
      "unanswered_questions": { "type": "array", "items": { "type": "object",
        "properties": { "question": { "type": "string" }, "suggested_answer": { "type": "string" } },
        "required": ["question","suggested_answer"], "additionalProperties": false } },
      "content_ideas": { "type": "array", "items": { "type": "object",
        "properties": { "idea": { "type": "string" }, "from_comment": { "type": "string" } },
        "required": ["idea","from_comment"], "additionalProperties": false } },
      "method_note": { "type": "string" }
    },
    "required": ["verdict","sentiment","themes","unanswered_questions","content_ideas","method_note"],
    "additionalProperties": false
  } } }
} }}
```

### 10. Parse Report (Code, Run Once for All Items)

```js
const j = $input.first().json;
function extractReport(x) {
  if (x.verdict) return x;
  const at = x.content?.[0]?.text;
  if (typeof at === "string") {
    try {
      const p = JSON.parse(at);
      if (p.verdict) return p;
    } catch {}
  }
  return null;
}
const report = extractReport(j);
const agg = $("Aggregate").first().json;
return [
  {
    json: {
      email: agg.email,
      video: agg.video,
      counts: agg.counts,
      comments: agg.comments,
      report,
    },
  },
];
```

### 11. Check (HTTP → Haiku) - reviewer

JSON body (Expression):

```
={{ {
  "model": "claude-haiku-4-5",
  "max_tokens": 2048,
  "messages": [
    { "role": "user", "content": "You are a strict fact-checker. Verify REPORT against the data. Set pass=false and add a short issue for each problem: a quoted string (in sentiment quotes, unanswered_questions.question, or content_ideas.from_comment) that is not verbatim in SOURCE_COMMENTS; any percentage or count that disagrees with COUNTS; fewer than 5 content_ideas; or themes not ordered by descending count. Otherwise pass=true, issues=[].\n\nREPORT:\n" + JSON.stringify($json.report) + "\n\nCOUNTS:\n" + JSON.stringify($json.counts) + "\n\nSOURCE_COMMENTS:\n" + JSON.stringify($json.comments.map(c => ({ id: c.id, text: c.text }))) }
  ],
  "output_config": { "format": { "type": "json_schema", "schema": {
    "type": "object",
    "properties": { "pass": { "type": "boolean" }, "issues": { "type": "array", "items": { "type": "string" } } },
    "required": ["pass","issues"], "additionalProperties": false
  } } }
} }}
```

### 12. Parse Review (Code, Run Once for All Items)

```js
const j = $input.first().json;
function extractReview(x) {
  if (typeof x.pass === "boolean")
    return { pass: x.pass, issues: x.issues || [] };
  const at = x.content?.[0]?.text;
  if (typeof at === "string") {
    try {
      const p = JSON.parse(at);
      if (typeof p.pass === "boolean")
        return { pass: p.pass, issues: p.issues || [] };
    } catch {}
  }
  return { pass: true, issues: [] };
}
const base = $("Parse Report").first().json;
return [{ json: { ...base, review: extractReview(j) } }];
```

### 13. Review Passed? (IF) + status tags

- IF: Value 1 `={{ $json.review.pass }}` · Boolean · **is true**.
- **TRUE** → **Status: sent** (Edit Fields): field `status` = `sent`, "Include Other Input Fields" **ON**.
- **FALSE** → **Status: sent_unreviewed** (Edit Fields): field `status` = `sent_unreviewed`, "Include Other Input Fields" **ON**.

---

## Phase 2 - Deliver

### 14. Prepare Delivery (Code, Run Once for All Items)

Wire **both** Status nodes into this one node. It decodes HTML entities and builds everything the delivery nodes need.

```js
const j = $input.first().json;
const status = j.status || "sent";
const { email, video, counts, report } = j;

const dec = (s) =>
  (s || "")
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/&#39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");

const l2v = video.views
  ? ((video.likes / video.views) * 100).toFixed(3) + "%"
  : "n/a";
const banner =
  status === "sent_unreviewed" ? "⚠ UNREVIEWED - automated report\n\n" : "";

const t = [];
t.push(`${video.title}`);
t.push(`${video.channel} · published ${video.publishedAt}`);
t.push(
  `Views ${video.views} · Likes ${video.likes} · Comments ${video.commentCountTotal} · Like/View ${l2v}`,
);
t.push("", "VERDICT", dec(report.verdict), "");
t.push(
  "SENTIMENT",
  `Positive ${counts.positive_pct}%  Neutral ${counts.neutral_pct}%  Negative ${counts.negative_pct}%`,
);
t.push("Positive:");
report.sentiment.positive_quotes.forEach((q) => t.push("  • " + dec(q)));
t.push("Neutral:");
report.sentiment.neutral_quotes.forEach((q) => t.push("  • " + dec(q)));
t.push("Negative:");
report.sentiment.negative_quotes.forEach((q) => t.push("  • " + dec(q)));
t.push("", "THEMES");
report.themes.forEach((x) => t.push(`  • ${x.name} (${x.count}) - ${x.note}`));
t.push("", "UNANSWERED QUESTIONS");
report.unanswered_questions.forEach((u) =>
  t.push(`  • ${dec(u.question)}  →  ${u.suggested_answer}`),
);
t.push("", "CONTENT IDEAS");
report.content_ideas.forEach((c) =>
  t.push(`  • ${c.idea}\n     (from: ${dec(c.from_comment)})`),
);
t.push("", "METHOD", dec(report.method_note));
const docText = banner + t.join("\n");

const esc = (s) =>
  dec(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const htmlBody = `<div style="font-family:Arial,sans-serif;line-height:1.5">
${status === "sent_unreviewed" ? '<p style="color:#b45309"><b>⚠ Unreviewed - automated report</b></p>' : ""}
<h2>${esc(video.title)}</h2>
<p>${esc(video.channel)} · ${video.publishedAt}<br>Views ${video.views} · Likes ${video.likes} · Comments ${video.commentCountTotal} · Like/View ${l2v}</p>
<h3>Verdict</h3><p>${esc(report.verdict)}</p>
<h3>Sentiment - ${counts.positive_pct}% / ${counts.neutral_pct}% / ${counts.negative_pct}%</h3>
<b>Positive</b><ul>${report.sentiment.positive_quotes.map((q) => "<li>" + esc(q) + "</li>").join("")}</ul>
<b>Neutral</b><ul>${report.sentiment.neutral_quotes.map((q) => "<li>" + esc(q) + "</li>").join("")}</ul>
<b>Negative</b><ul>${report.sentiment.negative_quotes.map((q) => "<li>" + esc(q) + "</li>").join("")}</ul>
<h3>Themes</h3><ul>${report.themes.map((x) => `<li><b>${esc(x.name)}</b> (${x.count}) - ${esc(x.note)}</li>`).join("")}</ul>
<h3>Unanswered questions</h3><ul>${report.unanswered_questions.map((u) => `<li>${esc(u.question)} → ${esc(u.suggested_answer)}</li>`).join("")}</ul>
<h3>Content ideas</h3><ul>${report.content_ideas.map((c) => `<li>${esc(c.idea)} <i>(from: ${esc(c.from_comment)})</i></li>`).join("")}</ul>
<p style="color:#666">${esc(report.method_note)}</p></div>`;

const chart = {
  type: "doughnut",
  data: {
    labels: ["Positive", "Neutral", "Negative"],
    datasets: [
      { data: [counts.positive_pct, counts.neutral_pct, counts.negative_pct] },
    ],
  },
};
const chartUrl =
  "https://quickchart.io/chart?c=" + encodeURIComponent(JSON.stringify(chart));

const topTheme = report.themes?.[0]?.name || "";
const slackText = `*${video.title}*\n${video.channel} · ${video.commentCountTotal} comments\nSentiment: ${counts.positive_pct}% pos / ${counts.neutral_pct}% neu / ${counts.negative_pct}% neg · Top theme: ${topTheme} · Status: ${status}`;

return [
  {
    json: {
      status,
      email,
      docTitle: `Audience report - ${video.title}`.slice(0, 120),
      docText,
      htmlBody,
      chartUrl,
      slackText,
      topTheme,
      video,
      counts,
    },
  },
];
```

### 15. Create Doc (Google Docs → Document → Create)

- Operation **Create** · Title `={{ $json.docTitle }}` · Folder = your Drive folder ID.
- Returns the doc under **`id`** (not `documentId`).

### 16. Insert Doc Text (Google Docs → Document → Update)

- **Doc ID or URL:** `={{ $json.id }}` ← use `id`, not `documentId`
- Action Fields: Object **Text** · Action **Insert** · Insert Segment **Body** · Insert Location **At Beginning** · Text `={{ $('Prepare Delivery').first().json.docText }}`
- Doc URL for links: `https://docs.google.com/document/d/<id>/edit`

### 17. Send Report Email (Gmail → Message → Send)

- To: `={{ $('Prepare Delivery').first().json.email }}`
- Subject: `={{ $('Prepare Delivery').first().json.docTitle }}`
- **Email Type: HTML**
- Message: `={{ $('Prepare Delivery').first().json.htmlBody + '<p><a href="https://docs.google.com/document/d/' + $('Create Doc').first().json.id + '/edit">📄 Open the full Google Doc</a></p>' }}`

### 18. Append Log Row (Google Sheets → Sheet Within Document → Append Row)

- **Document:** By ID → your `GOOGLE_SHEET_ID` (the whole spreadsheet).
- **Sheet:** From list → `Sheet1` (the tab inside - do **not** paste the spreadsheet ID here).
- Mapping: **Map Each Column Manually**:

| Column            | Value                                                                                          |
| ----------------- | ---------------------------------------------------------------------------------------------- |
| run_at            | `={{ $now.toISO() }}`                                                                          |
| requested_by      | `={{ $('Prepare Delivery').first().json.email }}`                                              |
| video_url         | `={{ 'https://www.youtube.com/watch?v=' + $('Prepare Delivery').first().json.video.videoId }}` |
| video_title       | `={{ $('Prepare Delivery').first().json.video.title }}`                                        |
| channel           | `={{ $('Prepare Delivery').first().json.video.channel }}`                                      |
| views             | `={{ $('Prepare Delivery').first().json.video.views }}`                                        |
| likes             | `={{ $('Prepare Delivery').first().json.video.likes }}`                                        |
| comments_total    | `={{ $('Prepare Delivery').first().json.video.commentCountTotal }}`                            |
| comments_analysed | `={{ $('Prepare Delivery').first().json.counts.total }}`                                       |
| positive_pct      | `={{ $('Prepare Delivery').first().json.counts.positive_pct }}`                                |
| neutral_pct       | `={{ $('Prepare Delivery').first().json.counts.neutral_pct }}`                                 |
| negative_pct      | `={{ $('Prepare Delivery').first().json.counts.negative_pct }}`                                |
| top_theme         | `={{ $('Prepare Delivery').first().json.topTheme }}`                                           |
| doc_link          | `={{ 'https://docs.google.com/document/d/' + $('Create Doc').first().json.id + '/edit' }}`     |
| status            | `={{ $('Prepare Delivery').first().json.status }}`                                             |

### 19. Post to Slack (Slack → Message → Send)

- Credential: your Slack bot token.
- Send Message To: **Channel** → `#audience-reports`.
- Message Type: Simple Text Message.
- Text: `={{ $('Prepare Delivery').first().json.slackText + '\n📄 https://docs.google.com/document/d/' + $('Create Doc').first().json.id + '/edit' + '\n📊 ' + $('Prepare Delivery').first().json.chartUrl }}`

---

## Troubleshooting (issues hit during the build)

| Symptom                              | Cause                                                                          | Fix                                                                                                                             |
| ------------------------------------ | ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| YouTube `items: []` (200 OK)         | The `id` query param had a stray leading `=` (e.g. `=dQw4...`)                 | In the value box type only `{{ $json.videoId }}`; check the grey preview is a clean 11-char ID                                  |
| IF always goes FALSE                 | IF wired to the wrong upstream node, or Get Video Details returned empty items | Value 1 preview should show the real count; ensure IF's input is the YouTube `videos` response and `part` includes `statistics` |
| `report` is `null`                   | The **Analyse** node was missing / not wired before Parse Report               | Insert Analyse between Aggregate and Parse Report                                                                               |
| Analyse JSON truncated               | `max_tokens` too low for the report                                            | Set Analyse `max_tokens` to `8000`                                                                                              |
| Google Sheets "Not a valid Sheet ID" | Spreadsheet ID pasted into the **Sheet** field                                 | Put the spreadsheet ID in **Document (By ID)**; pick the tab in **Sheet (From list)**                                           |
| Doc ID `undefined`                   | Create Doc returns `id`, not `documentId`                                      | Use `={{ $json.id }}`                                                                                                           |
| Slack posts under the wrong name     | n8n token belonged to an app whose bot display name was cached                 | Cleanest fix: fresh workspace + fresh Blank app both named as you want, install, use its bot token                              |

---

## Demo notes & scaling

- **10-comment cap:** `Get Comments maxResults=10` + `.slice(0, 10)` in Flatten. To run the full pipeline, raise `maxResults` (paginate to 500), remove/raise the slice, and re-add **batching** (see `RUNBOOK.md` §4.1) - one Haiku call can't take 500 comments comfortably.
- **Reviewer retry (parked):** for the demo, CHECK failure tags `sent_unreviewed`. The brief's re-analyse-once-then-send loop is in `RUNBOOK.md` §6.
- **Model:** swap `claude-haiku-4-5` on the **Analyse** node for a stronger Claude model if you want richer reports.

## ⚠️ Security

- Keep all API keys in **n8n credentials** (encrypted, not included in workflow exports) - never typed inline in nodes.
- Rotate any key that appeared in a screen recording (YouTube key, Anthropic key, Slack `xoxb-…` token).

## Trigger variations (per brief)

- **Slack slash command:** replace the Form Trigger with a Slack/webhook trigger reading the URL from the command text.
- **Weekly schedule** over a channel's newest videos: **Schedule Trigger** → YouTube `search.list` / uploads `playlistItems.list` → feed each new video ID into the same Collect→Deliver path.

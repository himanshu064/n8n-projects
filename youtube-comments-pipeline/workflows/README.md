# workflows — the n8n workflow file

This project is **one single n8n workflow** (19 nodes, form → four deliverables), unlike the multi-workflow projects in this repo.

The workflow was built directly in the n8n Cloud editor and has **not been exported yet**, so there is no `.json` file here right now. Until it is, `../BUILD_GUIDE.md` reconstructs the whole thing node-by-node — every node's exact settings, the Code node bodies, and the three Anthropic HTTP request bodies — which is enough to rebuild it from scratch.

## Export the workflow into this folder

1. Open the workflow in n8n (**YouTube Audience Report**).
2. Top-right **⋯ menu → Download**.
3. Save the file here as `YouTube Audience Report.json`.

> **Before committing it:** the export should carry **no credential secrets** (n8n stores those separately and exports only credential *references*). Open the JSON and confirm no API keys, tokens, or the Anthropic/YouTube keys are present. If any node has a key typed inline, move it into an n8n credential first, then re-export.

## Import it on another n8n instance

1. **Overview → Workflows → ⋯ → Import from File** → pick the JSON.
2. Open each node that needs a credential (the two YouTube HTTP nodes, the three Anthropic HTTP nodes, Google Docs / Sheets / Gmail, Slack) and select the matching credential from the dropdown — see `../README.md` section 4.
3. Set the YouTube Drive folder ID and Google Sheet ID (and confirm the sheet's header row from `../scripts/README.md`).
4. Activate the workflow to get the form's Production URL, or use the Test URL to trial it.

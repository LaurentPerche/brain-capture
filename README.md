# Brain Capture (v1)

Minimal Alfred keyword workflow that:

- Reads the current macOS clipboard (text or a single http/https URL)
- Uses OpenAI Structured Outputs (JSON Schema) to decide where/how to save
- Writes Markdown into a single local vault folder (create or append)
- Falls back to `_review/` when confidence is low
- Optionally auto-commits to Git (no push)

## What’s in this repo

- `workflow/`: Alfred workflow contents (what gets zipped into `.alfredworkflow`)
- `scripts/build_workflow.sh`: builds `dist/Brain Capture.alfredworkflow`
- `requirements.txt`: Python deps (install on your machine)

## Install

1) Install Python deps (system Python is fine):

```bash
python3 -m pip install -r requirements.txt
```

2) Build the Alfred workflow:

```bash
scripts/build_workflow.sh
```

3) Double-click `dist/Brain Capture.alfredworkflow` to import into Alfred.

## Updating + versioning

- Each build also produces a versioned artifact: `dist/Brain Capture vX.Y.alfredworkflow`.
- To bump the version, create a git tag, and open the updated workflow for import:

```bash
scripts/release_workflow.sh
```

Use `scripts/release_workflow.sh major` for a major bump.

## GitHub releases

This repo includes a GitHub Actions workflow that builds and attaches the `.alfredworkflow` file to a GitHub Release whenever you push a tag like `v1.1`.

Typical flow:

```bash
# once
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main

# each release
scripts/release_workflow.sh
git push origin main --tags
```

Then download the `.alfredworkflow` from the GitHub Release assets and double-click to install/update in Alfred.

If a tag push doesn’t trigger the release workflow, you can run it manually from GitHub Actions:

- Actions → “Release Alfred Workflow” → Run workflow
- Enter the existing tag (for example `v1.0`)

4) Run Alfred → type `br` → select “Open config file” once to create/open:

`~/.config/brain-capture/config.yaml`

Set `vault_path` and set `OPENAI_API_KEY` in the workflow’s Environment Variables (Alfred Workflow Editor → the `[x]` icon → Environment Variables).

## Config fields (v1)

- `vault_path`: absolute path to your Markdown vault folder
- `review_threshold`: if model confidence is below this, the note is saved to `_review/` as a new file
- `allowed_folders`: folder allow-list inside the vault (v1 is intersected with the built-in defaults)
- `log_dir`: where `audit.jsonl` is written; relative paths are resolved under `vault_path` (default: `.brain-capture/logs`)
- `openai.model`: model name (default: `gpt-5.2`)
- `openai.timeout_seconds`, `openai.max_output_tokens`
- `url_fetch.timeout_seconds`, `url_fetch.max_bytes`
- `git.enabled`, `git.auto_commit`: when both true, auto-commit note + audit log (no push)

## Dev/testing

- You can override the config location with `BRAIN_CAPTURE_CONFIG=/path/to/config.yaml`.

## Notes

- v1 is keyword-only: type `br` in Alfred to see actions.
- URL capture is intentionally conservative (size limits + basic SSRF protections).

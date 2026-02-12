# Jobs Tracker

This repository tracks changes on the following Erste Group jobs page and sends updates to Discord:

- https://www.erstegroup.com/de/karriere/stellenangebote#/joblist/location/Wien/discipline_items/Legal%20%2F%20Compliance%20%2F%20Audit

## What is included

- `tracker.py`: Renders the page with Playwright, creates a stable snapshot, detects changes, and notifies Discord.
- `.github/workflows/track-jobs.yml`: Runs every 30 minutes and on manual trigger.
- `state/erstegroup_wien_legal_jobs.json`: Snapshot state file (auto-generated/updated).

## Setup

1. Create a GitHub repository named `jobs`.
2. Push this project to that repository.
3. Add a repository secret:
   - `DISCORD_WEBHOOK_URL` = your Discord webhook URL
4. Enable GitHub Actions for the repository.

## Behavior

- First run creates an initial baseline and does not send Discord notification.
- Later runs notify Discord only when tracked content changes.
- On every detected change, the workflow commits the updated state file back to the repo.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium
set DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...  # optional
python tracker.py
```

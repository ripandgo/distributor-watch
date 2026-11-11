# One-weekday side-by-side test (before any switch)

Goal: show that the GitHub/box scanner wakes the bot for **everything** the
live "Distributor watch" emailed that day, with few false alarms, and that an
email the bot writes from the payload matches today's format and links.

Nothing live changes during the test. The live cron routine keeps running and
keeps emailing as usual. The test scanner runs in `--dry-run`, so nothing is
POSTed and no email is sent.

## Setup (day before)
1. Fresh baseline. In the morning before 5 AM CT on test day, run
   `python3 -m watch --seed-from /workspace/distro-monitor/state.json`
   into the test state dir.
2. Where to run:
   - **A. Box cron (works today, no approvals):**
     `*/5 * * * * cd /workspace/gh-distro-watch && WATCH_STATE_DIR=$PWD/test_state python3 -m watch --dry-run >> out/run.log 2>&1`
   - **B. GitHub (after Martin OKs the public repo):** push, set secrets
     *without* `WATCH_WEBHOOK_URL`, and run with `dry_run` (add `--dry-run`
     to the workflow for the day). Payloads go to the run log as an artifact.
     This is the only way to test GitHub cron lag and Cloudflare from GitHub IPs.
   Ideally run A and B at the same time.
3. Gmail part: only if the app password exists by then (`GMAIL_USER`,
   `GMAIL_APP_PASSWORD`). Without it, Gmail coverage is checked by hand
   against the live emails' Gmail sections.

## During the day (5:00 AM-10:00 PM CT)
- Each payload is saved to `out/payload_*.json`.
- `tools/render_preview.py out/payload_*.json > out/preview.txt` gives the
  coverage checklist.
- For 3-5 payloads that day, including at least one with Gold River prices,
  one with a new email, and the ASM inventory PDF, the bot writes the email it
  *would* send from that payload alone, in the live format with the HTML logo
  signature, and saves it as `out/email_<time>.html`. The bot does not send it.

## Comparison (evening)
Pull the live emails: shop Gmail Sent, `subject:"Distributor watch" newer_than:1d`
(read-only). For each live email, then for each payload:

| Check | How | Pass |
|---|---|---|
| Coverage | Every item in each live email shows up in some payload within ±10 min (GitHub ±20 min) | 100% of site/price/stock items. 100% of distributor emails (if Gmail creds set). ASM diff identical |
| Missed items | Items in live emails that never showed up in a payload | 0, or explained by a noise rule Martin accepts (e.g. a pure rename) |
| False alarms | Payloads whose content the bot would judge not worth emailing | ≤ 10% of payloads, and none from renames/encoding/flapping |
| Formatting | Side-by-side of `out/email_*.html` vs the live email covering the same items: subject pattern, distributor grouping/order, bullet style, price "old → new" format, product links clickable and identical URLs, logo signature present | Identical layout; same links |
| Errors | `errors` in payloads; Southern Hobby Cloudflare from GitHub | No source failing more than 30 min straight. On GitHub, Southern Hobby upcoming completes in most runs |
| Timing (GitHub) | Workflow run start times vs schedule | Median lag ≤ 10 min, no gap > 30 min in window |
| Quiet | Count of runs with no POST | Recorded, used for the real savings estimate |
| Window | No runs act outside Mon-Fri 5:00-21:59 CT | 0 out-of-window payloads |

Already verified on the box, 11/11/2026 ~11:30 AM CT (dry-run, read-only):
- 11/11 public sources fetched OK in 3m45s, 0 errors.
- Gold River: 16 price/stock changes vs the 11:11 AM CT snapshot. The live 11:31 AM run
  emailed 15 of them, and the 16th (25 Topps Finest Basketball) was already in an
  earlier live run between the snapshots.
- An immediate re-run was quiet (no POST). The next one caught one new
  live Gold River price drop (25 Topps Midnight Basketball $985 → $925).
- ASM PDF diff 11/09 → 11/10 matches the live routine's `cmp_2026-11-10.json` exactly.
- The noise-filter unit tests pass. A webhook POST to a local test server
  delivered the payload with the `Authorization: Bearer` header.
- Gmail IMAP: not tested (no app password yet). It skips cleanly when creds are absent.

## Go / no-go
Switch only if every pass criterion holds **and** Martin OKs it. Switching means:
create the webhook routine, put the URL/key into GitHub secrets, remove
`--dry-run`, and pause (don't delete) the old cron routine. To roll back,
resume the cron routine and disable the workflow.

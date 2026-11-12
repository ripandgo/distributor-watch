# Rip and Go distributor watch: GitHub change detector (DRAFT, not deployed)

Moves the polling half of the live "Distributor watch" routine to a free GitHub
Actions schedule. Grok Bot is woken (via a routine webhook) **only when
something real changed**; it then writes and sends the same email it sends today.

Status: prepared and tested locally on the box, 2026-11-11. No repo has been
created, nothing has been pushed, and the live routine is unchanged.

```
watch/fetchers.py    unchanged copy of /workspace/distro-monitor/distro_monitor.py (11 public sources)
watch/noise.py       rename / encoding-glitch / flapping filter
watch/gmail_imap.py  read-only IMAP check of All Mail + Trash for distributor senders
watch/asm.py         ASM Inventory PDF -> pdftotext -layout -> price/new/removed diff
watch/state.py       per-source JSON state in state/ (one item per line, small git deltas);
                     ASM prices + Gmail ids go to state/private/ (gitignored, Actions cache)
watch/__main__.py    runner: window guard, scan, filter, compact payload, POST or exit quietly, save state
watch/payload.py      cap the webhook JSON under 6 KB; summary + per-source counts first
.github/workflows/watch.yml   */20 cron (UTC, covers CST+CDT); job loops every 5 min,
                             concurrency, commits state/ after each pass
tools/render_preview.py       payload -> plain-text coverage checklist (for TEST_PLAN.md)
tests/               noise unit tests, ASM fixture PDFs (11/09, 11/10) + expected diff
```

Run locally:
```
python3 -m watch --seed-from /workspace/distro-monitor/state.json   # one-time baseline import
python3 -m watch --dry-run            # print payload instead of POSTing (inside window)
python3 -m watch --dry-run --force    # ignore the weekday 5 AM-10 PM CT window
python3 -m watch --skip-sites --asm-pdf old.pdf new.pdf --no-save --dry-run --force
python3 tests/test_noise.py
```
Env: `GMAIL_USER`, `GMAIL_APP_PASSWORD` (Gmail part skips cleanly if absent),
`WATCH_WEBHOOK_URL`, `WATCH_WEBHOOK_TOKEN`, optional `WATCH_WEBHOOK_HEADER`
(default `Authorization: Bearer <token>`), `WATCH_FLAP_MINUTES` (default 9),
`WATCH_ERROR_ALERT_RUNS` (default 6), `WATCH_STATE_DIR`, optional `WATCH_PAYLOAD_MAX_BYTES` (default 6000).

What counts as "real" (triggers a POST): any new / removed / changed item after
noise filtering, any new distributor email, an ASM inventory diff with at least
one change, or a source that has failed 6 runs in a row (alerted once per streak).
State is saved only after a successful POST, so a failed POST is retried next run.

Noise dropped: pure renames (same key or same SKU/URL, only name differs),
encoding glitches (entities, mojibake, Unicode/quote/dash/case/whitespace),
and remove/re-add flapping (a removal is held until the item has been gone
for 9 minutes, i.e. two consecutive runs; if it comes back first, both are dropped).
Trade-off: removals are reported one cycle (~5 min) later than today. Set
`WATCH_FLAP_MINUTES=0` to report removals immediately.

Not handled here (stays with the bot): the signed-in dealer-price check (browser + vault logins).

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
watch/window.py      24/7 scan gate (ALWAYS_ON); legacy weekday 4 AM-10 PM CT
                     window and bridge planner kept for ALWAYS_ON = False
watch/__main__.py    runner: window guard, scan, filter, compact payload, POST or exit quietly, save state
watch/payload.py      cap the webhook JSON under 6 KB; summary + per-source counts first
.github/workflows/watch.yml   self-chaining 55-min scanning jobs (5-min inner loop)
                             around the clock, plus a */30 backup cron all day;
                             legacy bridge job remains but does not sleep while
                             ALWAYS_ON is set; one scanning chain via the
                             duplicate-run guard; commits state/ after each pass
tools/run_loop.sh             5-minute inner loop for a scanning run
tools/run_bridge.sh           legacy hop (up to 5h30m); in 24/7 mode dispatches a loop immediately
tools/should_chain.sh         exit 0 when a follow-on loop should start (always, in 24/7 mode)
tools/should_bridge.sh        exit 0 only if the legacy window is closed and no bridge is going
tools/skip_if_other_run.sh    skip a second scanning chain or a second bridge (counts them separately)
tools/render_preview.py       payload -> plain-text coverage checklist (for TEST_PLAN.md)
tests/               noise unit tests, window/bridge plan tests, ASM fixture PDFs (11/09, 11/10) + expected diff
```

Cadence (24/7 since 2026-11-14): a scanning run loops every 5 minutes for about
55 minutes, then starts the next scanning run itself, around the clock, every
day (`watch.window.ALWAYS_ON = True`). The backup cron is `*/30 * * * *`
(every 30 minutes, every day). Set `ALWAYS_ON = False` and restore the two weekday cron lines to
go back to the legacy schedule described next.

Legacy schedule: when the weekday window closes (10 PM CT),
that last run dispatches a *bridge* job instead. A bridge does no scanning and
no webhook; it sleeps until the next weekday 4:00 AM CT (5 AM ET; Friday night
and the weekend roll to Monday), in hops of at most 5 hours 30 minutes so each
job stays under GitHub's 6-hour limit, then starts a looping scan. The */30
cron inside the window is only a backup if the chain or the bridge misses.
Manual `workflow_dispatch` with no inputs is still a single pass; `loop=true`
still runs the 55-minute loop.

Run locally:
```
python3 -m watch --seed-from /workspace/distro-monitor/state.json   # one-time baseline import
python3 -m watch --dry-run            # print payload instead of POSTing
python3 -m watch --dry-run --force    # ignore the watch window (no effect in 24/7 mode)
python3 -m watch --skip-sites --asm-pdf old.pdf new.pdf --no-save --dry-run --force
python3 tests/test_noise.py
python3 tests/test_window.py
python3 tests/test_payload.py
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

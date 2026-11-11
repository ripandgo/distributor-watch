"""python -m watch [--dry-run] [--no-save] [--force] [--only SRC ...] [--skip-sites]
                  [--asm-pdf FILE] [--seed-from STATE_JSON]

Exit 0 on quiet runs and after a successful webhook POST.
State is saved only after the payload is delivered (or printed in --dry-run),
so a failed POST is retried on the next run instead of losing the change.
"""
import argparse, datetime as dt, json, os, sys, time, traceback, urllib.request
from zoneinfo import ZoneInfo

from . import state, noise, asm, gmail_imap
from .fetchers import SOURCES, IncompleteFetch, SHRINK_GUARD, SHRINK_MIN_DROP

TZ = ZoneInfo("America/Chicago")
WINDOW = (5 * 60, 21 * 60 + 59)          # 05:00-21:59 Central, Mon-Fri
ERROR_ALERT_RUNS = int(os.environ.get("WATCH_ERROR_ALERT_RUNS", "6"))  # 6 x 5 min = 30 min
MAX_PER_SOURCE = 200


def in_window(now):
    loc = now.astimezone(TZ)
    m = loc.hour * 60 + loc.minute
    return loc.weekday() < 5 and WINDOW[0] <= m <= WINDOW[1]


def post(payload):
    url = os.environ.get("WATCH_WEBHOOK_URL")
    tok = os.environ.get("WATCH_WEBHOOK_TOKEN")
    hdr = os.environ.get("WATCH_WEBHOOK_HEADER", "Authorization")
    if not url:
        raise RuntimeError("WATCH_WEBHOOK_URL not set")
    headers = {"Content-Type": "application/json", "User-Agent": "rip-and-go-distro-watch/1"}
    if tok:
        headers[hdr] = f"Bearer {tok}" if hdr.lower() == "authorization" else tok
    body = json.dumps(payload, ensure_ascii=False).encode()
    last = None
    for i in range(3):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as r:
                if 200 <= r.status < 300:
                    return r.status
                last = f"HTTP {r.status}"
        except Exception as e:  # noqa
            last = e
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"webhook POST failed: {last}")


def scan_sites(only, meta, now):
    """Returns (site_changes, noise_totals, errors, new_states, ok_count)."""
    site_changes, errors, new_states = [], [], {}
    noise_tot = {"rename": 0, "encoding": 0, "flap": 0, "pending_removal": 0}
    ok = 0
    err_streak = meta.setdefault("error_streak", {})
    for key, (distro, desc, fn) in SOURCES.items():
        if only and key not in only:
            continue
        prev = state.load(key)
        t0 = time.time()
        try:
            items = fn()
            if not items:
                raise RuntimeError("returned 0 items")
            if prev and prev.get("items"):
                n_prev = len(prev["items"])
                if len(items) < SHRINK_GUARD * n_prev and n_prev - len(items) >= SHRINK_MIN_DROP:
                    raise IncompleteFetch(f"only {len(items)} items vs {n_prev} last time; looks partial")
        except Exception as e:
            n = err_streak.get(key, 0) + 1
            err_streak[key] = n
            errors.append({"source": key, "distributor": distro, "error": str(e)[:500],
                           "consecutive_runs": n, "alert": n == ERROR_ALERT_RUNS})
            print(f"[{key}] ERROR ({n} in a row): {e}", file=sys.stderr)
            continue
        ok += 1
        err_streak.pop(key, None)
        secs = time.time() - t0
        if not prev:
            print(f"[{key}] baseline saved ({len(items)} items, {secs:.0f}s)")
            new_states[key] = {"fetched_at": now.isoformat(), "count": len(items), "items": items, "pending": {}}
            continue
        ch, nz, pending = noise.filter_diff(key, prev["items"], items, prev.get("pending", {}), now)
        for k, v in nz.items():
            noise_tot[k] += v
        print(f"[{key}] {len(items)} items, {secs:.0f}s: {len(ch)} real change(s), noise {nz}")
        for c in ch[:MAX_PER_SOURCE]:
            c.update({"source": key, "distributor": distro, "source_desc": desc,
                      "name": c["item"].get("name"), "url": c["item"].get("url")})
            if "fields" in c:
                c["fields"] = {f: list(v) for f, v in c["fields"].items()}
            site_changes.append(c)
        if len(ch) > MAX_PER_SOURCE:
            site_changes.append({"source": key, "distributor": distro, "kind": "truncated",
                                 "more": len(ch) - MAX_PER_SOURCE})
        new_states[key] = {"fetched_at": now.isoformat(), "count": len(items), "items": items, "pending": pending}
    return site_changes, noise_tot, errors, new_states, ok


def run_asm(pdfs, meta, errors):
    """pdfs: list of (msg_id, filename, bytes). Returns asm_diff dict or None, state update."""
    prev = state.load("asm_latest")
    out = None
    for mid, fn, data in pdfs:
        try:
            text = asm.pdf_to_text(data)
            cur = asm.parse_text(text)
            if not cur:
                raise RuntimeError(f"{fn}: no rows parsed")
            rdate = asm.report_date(text)
        except Exception as e:
            errors.append({"source": "asm_pdf", "distributor": "All Sports Marketing",
                           "error": str(e)[:500], "consecutive_runs": 1, "alert": True})
            continue
        if prev and prev.get("items"):
            d = asm.diff(prev["items"], cur)
            d.update({"date": rdate, "vs": prev.get("date"), "email_id": mid, "file": fn, "count": len(cur)})
            out = d  # newest wins; older ones already folded into prev
        prev = {"date": rdate, "email_id": mid, "items": cur}
    return out, prev


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m watch")
    ap.add_argument("--dry-run", action="store_true", help="print the payload instead of POSTing")
    ap.add_argument("--no-save", action="store_true", help="do not write state")
    ap.add_argument("--force", action="store_true", help="ignore the 5 AM-10 PM weekday window")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--skip-sites", action="store_true")
    ap.add_argument("--asm-pdf", nargs="*", default=[], help="local ASM PDF(s) to process (testing)")
    ap.add_argument("--seed-from", help="import a distro-monitor state.json as the baseline, then exit")
    a = ap.parse_args(argv)
    now = dt.datetime.now(dt.timezone.utc)

    if a.seed_from:
        old = json.load(open(a.seed_from))
        for k, v in old.items():
            if k in SOURCES:
                state.save(k, {"fetched_at": v.get("fetched_at"), "count": v.get("count"),
                               "items": v["items"], "pending": {}})
                print(f"seeded {k}: {len(v['items'])} items")
        return 0

    if not a.force and not in_window(now):
        print(f"outside watch window ({now.astimezone(TZ):%a %H:%M %Z}); exiting")
        return 0

    meta = state.load("meta", {}) or {}
    site_changes, noise_tot, errors, new_states, ok = ([], {}, [], {}, 0)
    if not a.skip_sites:
        site_changes, noise_tot, errors, new_states, ok = scan_sites(a.only, meta, now)

    # Gmail (read-only); skipped cleanly without creds
    seen = state.load("gmail_seen", {}) or {}
    new_emails, asm_pdfs, gmail_status = [], [], "skipped (GMAIL_USER / GMAIL_APP_PASSWORD not set)"
    try:
        res, asm_pdfs = gmail_imap.check(seen)
        if res is not None:
            if not seen.get("_baseline"):
                gmail_status = f"baseline: {len(res)} existing message(s) marked seen, not reported"
                seen["_baseline"] = now.isoformat()
                for e in res:
                    seen[e["id"]] = e["date"] or now.isoformat()
                res = []
                # still baseline the ASM list from the newest PDF without reporting
            else:
                gmail_status = f"ok: {len(res)} new"
            new_emails = res
    except Exception as e:
        gmail_status = f"error: {e}"
        n = meta.setdefault("error_streak", {}).get("gmail", 0) + 1
        meta["error_streak"]["gmail"] = n
        errors.append({"source": "gmail", "distributor": "-", "error": str(e)[:500],
                       "consecutive_runs": n, "alert": n == ERROR_ALERT_RUNS})
    else:
        meta.setdefault("error_streak", {}).pop("gmail", None)
    print(f"[gmail] {gmail_status}")

    local = [(f"local:{os.path.basename(p)}", os.path.basename(p), open(p, "rb").read()) for p in a.asm_pdf]
    asm_diff, asm_state = run_asm(asm_pdfs + local, meta, errors) if (asm_pdfs or local) else (None, None)
    if asm_diff is not None and asm.is_empty(asm_diff):
        print(f"[asm] {asm_diff['date']} vs {asm_diff['vs']}: no price/list changes")
        asm_diff = None
    elif asm_diff:
        print(f"[asm] {asm_diff['date']} vs {asm_diff['vs']}: +{len(asm_diff['increases'])} up, "
              f"{len(asm_diff['decreases'])} down, {len(asm_diff['new'])} new, {len(asm_diff['removed'])} removed")
    elif asm_state:
        print(f"[asm] baseline saved from {asm_state.get('date')}")

    alert_errors = [e for e in errors if e["alert"]]
    real = bool(site_changes or new_emails or asm_diff or alert_errors)
    payload = {
        "run_at": now.isoformat(),
        "run_at_central": now.astimezone(TZ).isoformat(),
        "site_changes": site_changes,
        "new_emails": new_emails,
        "asm_diff": asm_diff,
        "errors": errors,
        "noise_dropped": noise_tot,
        "sources_ok": ok,
        "gmail": gmail_status,
        "run_url": (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
                    if os.environ.get("GITHUB_RUN_ID") else None),
    }
    if real:
        if a.dry_run:
            print("DRY RUN - would POST:")
            print(json.dumps(payload, indent=1, ensure_ascii=False))
            os.makedirs("out", exist_ok=True)
            fn = os.path.join("out", f"payload_{now:%Y%m%d_%H%M%S}.json")
            json.dump(payload, open(fn, "w"), indent=1, ensure_ascii=False)
            print(f"(saved {fn})")
        else:
            st = post(payload)   # raises -> state not saved -> retried next run
            print(f"webhook POST ok (HTTP {st}): {len(site_changes)} site change(s), "
                  f"{len(new_emails)} email(s), asm={'yes' if asm_diff else 'no'}, {len(alert_errors)} alert error(s)")
    else:
        print(f"quiet: no real changes ({ok} source(s) ok, {len(errors)} transient error(s), noise {noise_tot})")

    if not a.no_save:
        for k, v in new_states.items():
            state.save(k, v)
        for e in new_emails:
            seen[e["id"]] = e["date"] or now.isoformat()
        cutoff = (now - dt.timedelta(days=10)).isoformat()
        seen = {k: v for k, v in seen.items() if k == "_baseline" or (v or "") >= cutoff}
        if gmail_imap.creds():
            state.save("gmail_seen", seen)
        if asm_state:
            state.save("asm_latest", asm_state)
        meta["last_run"] = now.isoformat()
        if real:
            meta["last_post"] = now.isoformat()
        state.save("meta", meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())

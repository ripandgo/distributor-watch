"""Compact the webhook payload so a busy run always arrives whole.

The receiving routine saw the first busy posts (56 and 31 site_changes)
cut off mid-list, with only Mad Al items visible. Those payloads shipped
the full item dict for every change and had a 200-item per-source cap,
so they could run to tens of KB. Grok Bot webhook/event wrappers commonly
cap around 8 KB; this module hard-caps the JSON at PAYLOAD_MAX_BYTES
(6000) to leave ~2 KB of headroom under that ceiling for wrapping,
escaping, and prompt chrome.

A compact human `summary` is always the first key. Per-source counts
(including removals) are always present, even when item lists are
trimmed, so truncation never hides which sources changed or by how much.
Removed, added, and changed items are listed whenever they fit under
the byte cap; `truncated` is true only when something was dropped for size.
"""
from collections import OrderedDict

# Stay well under an ~8 KB event cap. See module docstring.
PAYLOAD_MAX_BYTES = 6000

MAX_NAME = 80
MAX_URL = 140
MAX_SUBJECT = 120
MAX_ERROR = 240
MAX_FROM = 80

# Initial list caps; build() steps these down until the JSON fits.
ITEM_CAPS = (8, 5, 3, 2, 1, 0)
EMAIL_CAPS = (8, 5, 3, 2, 1, 0)
ASM_CAPS = (8, 5, 3, 2, 1, 0)

# Removals are listed when they fit, but they stay last in the
# priority order and are the first kind dropped when the JSON would
# exceed PAYLOAD_MAX_BYTES.


def payload_size(payload):
    import json
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _plural(n, word):
    w = word if n == 1 else word + "s"
    return f"{n} {w}"


def clip(s, n):
    s = "" if s is None else str(s)
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def _truthy(v):
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s not in {"", "0", "false", "no", "out", "n", "none", "unavailable"}


def is_price(c):
    return "price" in (c.get("fields") or {})


def is_stock_in(c):
    fields = c.get("fields") or {}
    if "available" not in fields:
        return False
    old, new = fields["available"][:2]
    return (not _truthy(old)) and _truthy(new)


def classify(c):
    """Return the primary list-priority kind for a slim change record."""
    kind = c.get("kind")
    if kind == "new":
        return "new"
    if kind == "removed":
        return "removed"
    if kind == "changed":
        if is_price(c):
            return "price"
        if is_stock_in(c):
            return "stock_in"
        return "changed_other"
    return kind or "changed_other"


def _rank(c):
    return {"new": 0, "price": 1, "stock_in": 2, "changed_other": 3, "removed": 4}.get(classify(c), 5)


def slim_change(c, source, distributor, source_desc=None):
    """Drop the full item dict; keep the fields the report needs."""
    item = c.get("item") or {}
    fields = None
    if "fields" in c and c["fields"] is not None:
        fields = {f: list(v) for f, v in c["fields"].items()}
    out = {
        "source": source,
        "distributor": distributor,
        "kind": c.get("kind"),
        "key": c.get("key"),
        "name": clip(c.get("name") or item.get("name"), MAX_NAME),
        "url": clip(c.get("url") or item.get("url"), MAX_URL),
    }
    if fields:
        out["fields"] = fields
    return out


def slim_email(e):
    return {
        "id": e.get("id"),
        "thread_id": e.get("thread_id"),
        "from": clip(e.get("from"), MAX_FROM),
        "subject": clip(e.get("subject"), MAX_SUBJECT),
        "date": e.get("date"),
        "distributor": e.get("distributor") or "",
        "mailbox": e.get("mailbox"),
    }


def slim_asm(asm, cap):
    if not asm:
        return None
    out = {
        "date": asm.get("date"),
        "vs": asm.get("vs"),
        "email_id": asm.get("email_id"),
        "file": asm.get("file"),
        "count": asm.get("count"),
    }
    omitted = {}
    for key, row_fn in (
        ("increases", _asm_price_row),
        ("decreases", _asm_price_row),
        ("new", _asm_item_row),
        ("removed", _asm_item_row),
    ):
        rows = asm.get(key) or []
        out[f"n_{key}"] = len(rows)
        listed = [row_fn(r) for r in rows[:cap]]
        out[key] = listed
        extra = max(0, len(rows) - len(listed))
        if extra:
            omitted[key] = extra
    if omitted:
        out["omitted"] = omitted
    return out


def _asm_price_row(r):
    if isinstance(r, (list, tuple)) and len(r) >= 4:
        return [r[0], clip(r[1], MAX_NAME), r[2], r[3]]
    return r


def _asm_item_row(it):
    if not isinstance(it, dict):
        return it
    return {k: (clip(it[k], MAX_NAME) if k == "desc" else it[k])
            for k in ("code", "desc", "price", "qty") if k in it}


def source_counts(changes, listed):
    """Full per-source counts; `listed`/`omitted` reflect what is in site_changes."""
    listed_keys = [(c.get("source"), c.get("key"), c.get("kind"), c.get("name")) for c in listed]
    listed_set = set(listed_keys)
    by = OrderedDict()
    for c in changes:
        src = c.get("source") or ""
        if src not in by:
            by[src] = {
                "source": src,
                "distributor": c.get("distributor") or "",
                "new": 0, "price": 0, "stock_in": 0, "changed_other": 0,
                "removed": 0, "total": 0, "listed": 0, "omitted": 0,
            }
        row = by[src]
        row[classify(c)] = row.get(classify(c), 0) + 1
        row["total"] += 1
        ident = (c.get("source"), c.get("key"), c.get("kind"), c.get("name"))
        if ident in listed_set:
            row["listed"] += 1
        else:
            row["omitted"] += 1
    return list(by.values())


def select_changes(changes, cap_per_source):
    by = OrderedDict()
    for c in changes:
        by.setdefault(c.get("source") or "", []).append(c)
    out = []
    for src, items in by.items():
        ranked = sorted(items, key=_rank)
        out.extend(ranked[:cap_per_source])
    return out


def _fmt_src_counts(row):
    parts = []
    for key, label in (("new", "new"), ("price", "price"), ("stock_in", "stock-in"),
                       ("changed_other", "other"), ("removed", "removed")):
        n = row.get(key) or 0
        if n:
            parts.append(f"{n} {label}")
    body = ", ".join(parts) if parts else f"{row.get('total', 0)} change(s)"
    name = row.get("distributor") or row.get("source") or "source"
    return f"{name} {body}"


def build_summary(now_central, counts, emails, asm, errors, n_listed, n_total):
    loc = now_central
    try:
        stamp = loc.strftime("%I:%M %p").lstrip("0") + " CT"
    except Exception:
        stamp = str(loc)
    bits = [f"Distributor watch {stamp}"]
    if counts:
        bits.append("; ".join(_fmt_src_counts(r) for r in counts))
    else:
        bits.append("no site changes")
    n_mail = len(emails) if emails else 0
    if n_mail:
        who = []
        for e in emails:
            d = e.get("distributor") or "email"
            if d not in who:
                who.append(d)
        bits.append(_plural(n_mail, "new email") + (f" ({", ".join(who)})" if who else ""))
    else:
        bits.append("no new emails")
    if asm:
        bits.append(
            f"ASM {asm.get('date') or '?'} vs {asm.get('vs') or '?'}: "
            f"{asm.get('n_increases', len(asm.get('increases') or []))} up, "
            f"{asm.get('n_decreases', len(asm.get('decreases') or []))} down, "
            f"{asm.get('n_new', len(asm.get('new') or []))} new, "
            f"{asm.get('n_removed', len(asm.get('removed') or []))} removed"
        )
    else:
        bits.append("no ASM")
    alert = [e for e in (errors or []) if e.get("alert")]
    if alert:
        bits.append(_plural(len(alert), "alert error"))
    elif errors:
        bits.append(_plural(len(errors), "transient error"))
    else:
        bits.append("0 errors")
    if n_total and n_listed < n_total:
        bits.append(f"{n_listed} of {_plural(n_total, "site change")} listed")
    # First clause is the title; the rest are '; '-joined.
    title, rest = bits[0], bits[1:]
    return title + ": " + "; ".join(rest) + "."


def slim_error(e):
    return {
        "source": e.get("source"),
        "distributor": e.get("distributor"),
        "error": clip(e.get("error"), MAX_ERROR),
        "consecutive_runs": e.get("consecutive_runs"),
        "alert": bool(e.get("alert")),
    }


def build(now_central, site_changes, new_emails, asm_diff, errors, noise_tot,
          sources_ok, gmail_status, run_at, run_at_central, run_url,
          max_bytes=None):
    """Return a compact payload. `summary` is the first key."""
    import json  # noqa: F401  (size via payload_size)
    max_bytes = PAYLOAD_MAX_BYTES if max_bytes is None else int(max_bytes)
    changes = list(site_changes or [])
    emails_all = [slim_email(e) for e in (new_emails or [])]
    errors_all = [slim_error(e) for e in (errors or [])]
    n_total = len(changes)

    def _max_per_source(items):
        by = OrderedDict()
        for c in items:
            by.setdefault(c.get("source") or "", []).append(c)
        return max((len(v) for v in by.values()), default=0)

    def _asm_max(asm):
        if not asm:
            return 0
        return max(len(asm.get(k) or []) for k in ("increases", "decreases", "new", "removed"))

    # Try a complete listing first so items are only dropped when the
    # JSON would exceed max_bytes; then step the caps down as before.
    full_item = _max_per_source(changes)
    full_email = len(emails_all)
    full_asm = _asm_max(asm_diff)
    cap_iters = [(full_item, full_email, full_asm)]
    for i, item_cap in enumerate(ITEM_CAPS):
        cap_iters.append((
            min(item_cap, full_item),
            min(EMAIL_CAPS[min(i, len(EMAIL_CAPS) - 1)], full_email),
            min(ASM_CAPS[min(i, len(ASM_CAPS) - 1)], full_asm),
        ))

    payload = None
    seen_caps = set()
    for item_cap, email_cap, asm_cap in cap_iters:
        cap_key = (item_cap, email_cap, asm_cap)
        if cap_key in seen_caps:
            continue
        seen_caps.add(cap_key)
        listed = select_changes(changes, item_cap)
        emails = emails_all[:email_cap]
        asm = slim_asm(asm_diff, asm_cap)
        counts = source_counts(changes, listed)
        # If emails were trimmed, the summary still uses the full count.
        summary = build_summary(now_central, counts, emails_all, asm, errors_all,
                                len(listed), n_total)
        if len(emails_all) > len(emails):
            summary = summary.rstrip(".") + f"; {len(emails)} of {_plural(len(emails_all), "email")} listed."
        truncated = (
            any(r["omitted"] for r in counts)
            or len(emails_all) > len(emails)
            or bool(asm and asm.get("omitted"))
        )
        payload = _assemble(
            summary, run_at, run_at_central, counts, listed, emails, asm,
            errors_all, noise_tot, sources_ok, gmail_status, run_url, truncated,
        )
        if payload_size(payload) <= max_bytes:
            return payload

    # Still over: drop list bodies, keep summary + counts.
    counts = source_counts(changes, [])
    asm = slim_asm(asm_diff, 0) if asm_diff else None
    emails = emails_all[:1]
    errors_keep = errors_all[:5]
    summary = build_summary(now_central, counts, emails_all, asm, errors_all, 0, n_total)
    payload = _assemble(
        summary, run_at, run_at_central, counts, [], emails, asm,
        errors_keep, noise_tot, sources_ok, gmail_status, run_url, True,
    )
    if payload_size(payload) <= max_bytes:
        return payload

    # Last resort: drop emails/asm/error details, keep counts and a short summary.
    short = build_summary(now_central, counts, emails_all, asm, errors_all, 0, n_total)
    payload = _assemble(
        short, run_at, run_at_central, counts, [], [], None,
        [{"source": e.get("source"), "alert": e.get("alert"),
          "distributor": e.get("distributor"), "consecutive_runs": e.get("consecutive_runs")}
         for e in errors_all],
        noise_tot, sources_ok, gmail_status, run_url, True,
    )
    return payload


def _assemble(summary, run_at, run_at_central, counts, listed, emails, asm,
              errors, noise_tot, sources_ok, gmail_status, run_url, truncated):
    # Insert summary first so a mid-payload cut still leaves the headline
    # and (just after the timestamps) the per-source counts.
    return {
        "summary": summary,
        "run_at": run_at,
        "run_at_central": run_at_central,
        "source_counts": counts,
        "site_changes": listed,
        "new_emails": emails,
        "asm_diff": asm,
        "errors": errors,
        "noise_dropped": noise_tot or {"rename": 0, "encoding": 0, "flap": 0, "pending_removal": 0},
        "sources_ok": sources_ok,
        "gmail": gmail_status,
        "run_url": run_url,
        "truncated": bool(truncated),
    }

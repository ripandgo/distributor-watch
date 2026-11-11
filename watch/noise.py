"""Noise filter for site diffs.

Dropped as noise (counted, not reported):
  * encoding glitches: a field differs only by HTML entities, mojibake
    (e.g. 'Ã©' vs 'é', 'â€™' vs '’'), Unicode form, smart quotes/dashes,
    whitespace or letter case;
  * pure renames: same key (or same SKU/URL under a new key), same price and
    availability and every other field, only the display name changed;
  * flapping: an item that vanishes and comes back within FLAP_MINUTES.
    A removal is held as 'pending' and only reported once the item has been
    missing for FLAP_MINUTES (default 9 = two consecutive 5-minute runs);
    if it reappears first, both the removal and the re-add are dropped.
"""
import datetime as dt, html, os, re, unicodedata

FLAP_MINUTES = float(os.environ.get("WATCH_FLAP_MINUTES", "9"))
RENAME_FIELDS = {"name"}           # fields whose change alone is a "rename"
IGNORE_FIELDS = {"url", "sell_sheet"}  # same as distro_monitor.py
_MOJI = re.compile("[\u00c2\u00c3\u00e2][\u0080-\u00bf\u2018-\u203a\u20ac\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u02c6\u02dc]")
_TRANS = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                        "\u2014": "-", "\u00a0": " ", "\u2122": "tm", "\u00ae": "(r)", "\ufffd": ""})


def _fix_mojibake(s):
    """Undo UTF-8-read-as-cp1252 (e.g. 'PokÃ©mon' -> 'Pokémon'), run by run."""
    if not _MOJI.search(s):
        return s
    def fix(m):
        for enc in ("cp1252", "latin-1"):
            try:
                return m.group(0).encode(enc).decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
        return m.group(0)
    return re.sub("[\u00c2-\u00f4][\u0080-\u00bf\u2018-\u203a\u20ac\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u02c6\u02dc]{1,3}", fix, s)


def norm(v):
    if not isinstance(v, str):
        return v
    s = _fix_mojibake(v)
    s = _fix_mojibake(html.unescape(html.unescape(s)))
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", s).translate(_TRANS))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().casefold()


def _real_field_changes(old, new):
    ch, glitch = {}, False
    for f in set(old) | set(new):
        if f in IGNORE_FIELDS:
            continue
        o, n = old.get(f), new.get(f)
        if o == n:
            continue
        if norm(o) == norm(n):
            glitch = True
            continue
        ch[f] = (o, n)
    return ch, glitch


def _ident(it):
    """Secondary identity for re-keyed items: SKU first, then URL."""
    sku = (it.get("sku") or "").strip()
    if sku:
        return ("sku", sku.upper())
    u = (it.get("url") or "").strip()
    return ("url", u.rstrip("/").lower()) if u else None


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def filter_diff(source, old_items, new_items, pending, now=None):
    """Returns (changes, noise_counts, new_pending).

    changes: list of dicts {kind: new|changed|removed, key, item, fields?}
    pending: {key: {"item": old_item, "since": iso}} carried between runs.
    """
    now = now or now_utc()
    noise = {"rename": 0, "encoding": 0, "flap": 0, "pending_removal": 0}
    pending = dict(pending or {})
    changes = []
    added = [k for k in new_items if k not in old_items]
    removed = [k for k in old_items if k not in new_items]

    # 1. items that were pending removal and are back -> flap, compare to old copy
    for k in list(added):
        if k in pending:
            noise["flap"] += 1
            old_it = pending.pop(k)["item"]
            ch, glitch = _real_field_changes(old_it, new_items[k])
            if ch and not set(ch) <= RENAME_FIELDS:
                changes.append({"kind": "changed", "key": k, "item": new_items[k], "fields": ch})
            added.remove(k)

    # 2. re-keyed items: removed key + added key with same SKU/URL
    by_ident = {}
    for k in removed:
        i = _ident(old_items[k])
        if i:
            by_ident.setdefault(i, k)
    for k in list(added):
        i = _ident(new_items[k])
        ok = by_ident.get(i) if i else None
        if ok and ok in removed:
            ch, glitch = _real_field_changes(old_items[ok], new_items[k])
            removed.remove(ok)
            added.remove(k)
            if not ch:
                noise["encoding" if glitch else "rename"] += 1
            elif set(ch) <= RENAME_FIELDS:
                noise["rename"] += 1
            else:
                changes.append({"kind": "changed", "key": k, "item": new_items[k], "fields": ch,
                                "rekeyed_from": ok})

    # 3. same-key field changes
    for k in new_items:
        if k in old_items:
            ch, glitch = _real_field_changes(old_items[k], new_items[k])
            if not ch:
                if glitch:
                    noise["encoding"] += 1
                continue
            if set(ch) <= RENAME_FIELDS:
                noise["rename"] += 1
                continue
            changes.append({"kind": "changed", "key": k, "item": new_items[k], "fields": ch})

    for k in added:
        changes.append({"kind": "new", "key": k, "item": new_items[k]})

    # 4. removals: hold for FLAP_MINUTES before reporting
    for k in removed:
        pending.setdefault(k, {"item": old_items[k], "since": now.isoformat()})
    for k, p in list(pending.items()):
        if k in new_items:          # back under same key (covered above) - drop
            pending.pop(k)
            continue
        since = dt.datetime.fromisoformat(p["since"])
        if FLAP_MINUTES <= 0 or (now - since).total_seconds() >= FLAP_MINUTES * 60:
            changes.append({"kind": "removed", "key": k, "item": p["item"]})
            pending.pop(k)
        else:
            noise["pending_removal"] += 1
    return changes, noise, pending

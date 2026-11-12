#!/usr/bin/env python3
"""Render a plain-text preview of what a payload covers, grouped by distributor,
with every link, for the side-by-side test (TEST_PLAN.md). This is NOT the
email: the bot still writes the real HTML email. It is the checklist the
bot-written email must cover.

usage: tools/render_preview.py out/payload_*.json > preview.txt
"""
import json, sys
from collections import defaultdict

for fn in sys.argv[1:]:
    d = json.load(open(fn))
    print(f"=== {fn}  run {d.get('run_at_central')}")
    if d.get("summary"):
        print(d["summary"])
        if d.get("truncated"):
            print("(payload truncated; counts in source_counts are complete)")
    by = defaultdict(list)
    for c in d.get("site_changes", []):
        if c.get("kind") == "truncated":
            by[c["distributor"]].append(f"  ... and {c['more']} more ({c['source']})"); continue
        if c.get("kind") == "changed":
            f = "; ".join(f"{k}: {o} -> {n}" for k, (o, n) in c["fields"].items())
            by[c["distributor"]].append(f"  * {c['name']}: {f}  <{c.get('url')}>")
        else:
            sign = "+" if c["kind"] == "new" else "-"
            by[c["distributor"]].append(f"  {sign} {c['kind'].upper()} {c['name']}  <{c.get('url')}>")
    for e in d.get("new_emails", []):
        by[e.get("distributor") or "Email"].append(
            f"  @ email {e['date']}: {e['subject']} ({e['from']})  <https://mail.google.com/mail/u/0/#all/{e['id']}>")
    a = d.get("asm_diff")
    if a:
        inc, dec = a.get("increases") or [], a.get("decreases") or []
        for c, desc, o, n in inc + dec:
            by["All Sports Marketing"].append(f"  * ASM {c} {desc}: ${o:g} -> ${n:g}")
        for it in a.get("new") or []:
            by["All Sports Marketing"].append(f"  + ASM new {it['code']} {it['desc']} ${it['price']:g}")
        for it in a.get("removed") or []:
            by["All Sports Marketing"].append(f"  - ASM removed {it['code']} {it['desc']}")
        om = a.get("omitted") or {}
        counts = ", ".join(f"{a.get(f'n_{k}', '?')} {k}" for k in ("increases", "decreases", "new", "removed"))
        by["All Sports Marketing"].append(f"  ASM totals: {counts}" + (f" (omitted {om})" if om else ""))
    if d.get("source_counts"):
        print("\n## source counts")
        for r in d["source_counts"]:
            print(f"  {r.get('distributor') or r.get('source')}: {r}")
    for dist in sorted(by):
        print(f"\n## {dist}")
        print("\n".join(by[dist]))
    errs = [e for e in d.get("errors", []) if e.get("alert")]
    if errs:
        print("\n## Errors")
        for e in errs:
            print(f"  ! {e['distributor']} / {e['source']}: {e['error']} ({e['consecutive_runs']} runs)")
    print()

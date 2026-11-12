"""Payload compaction: a busy run stays under the size cap and never hides counts."""
import datetime as dt, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from watch import payload

TZ_CENTRAL = dt.timezone(dt.timedelta(hours=-6))
NOW = dt.datetime(2026, 11, 11, 18, 33, tzinfo=TZ_CENTRAL)


def ch(source, distro, kind, i, fields=None, name=None):
    item = {
        "name": name or f"{distro} item {i} with a fairly long product title for size",
        "type": "hobby",
        "price": "99.00",
        "available": kind != "removed",
        "published": "2026-11-01",
        "url": f"https://example.com/{source}/products/{i}-long-slug-path",
        "sku": f"SKU{i:05d}",
        "extra": "x" * 80,
    }
    raw = {"kind": kind, "key": f"{source}-{i}", "item": item}
    if fields:
        raw["fields"] = fields
    return payload.slim_change(raw, source, distro)


def big_run():
    """Roughly run-1 sized: 56+ site changes across several sources, plus email/ASM."""
    changes = []
    # Mad Al: 24 new, 12 price, 8 stock-in, 6 removed (50) — like the first busy run
    changes += [ch("madal_catalog", "Mad Al", "new", i) for i in range(24)]
    changes += [ch("madal_catalog", "Mad Al", "changed", 100 + i,
                   fields={"price": [f"{80+i}.00", f"{70+i}.00"]}) for i in range(12)]
    changes += [ch("madal_catalog", "Mad Al", "changed", 200 + i,
                   fields={"available": [False, True]}) for i in range(8)]
    changes += [ch("madal_catalog", "Mad Al", "removed", 300 + i) for i in range(6)]
    # Other sources so a mid-list cutoff would have hidden them
    changes += [ch("goldriver_products", "Gold River", "changed", i,
                   fields={"price": ["975.00", "915.00"]}) for i in range(16)]
    changes += [ch("southernhobby_new", "Southern Hobby", "new", i) for i in range(8)]
    changes += [ch("acd_products", "ACD", "changed", i,
                   fields={"name": ["Old", "New"], "price": ["10.00", "9.00"]}) for i in range(5)]
    changes += [ch("phd_news", "PHD Games", "removed", i) for i in range(4)]
    emails = [
        {"id": "abc111", "thread_id": "thr1", "from": "sales@madal.com",
         "subject": "New arrivals this week at Mad Al", "date": "2026-11-11T18:00:00-06:00",
         "distributor": "Mad Al", "mailbox": "[Gmail]/All Mail"},
        {"id": "abc222", "thread_id": "thr2", "from": "no-reply@phdgames.com",
         "subject": "Weekly News", "date": "2026-11-11T18:10:00-06:00",
         "distributor": "PHD Games", "mailbox": "[Gmail]/All Mail"},
        {"id": "abc333", "thread_id": "thr3", "from": "marketing@acdd.com",
         "subject": "ACD promo " + ("Y" * 200), "date": "2026-11-11T18:20:00-06:00",
         "distributor": "ACD", "mailbox": "[Gmail]/All Mail"},
    ]
    asm = {
        "date": "2026-11-11", "vs": "2026-11-10", "email_id": "asm1", "file": "ASM Inventory 11-11-26.pdf",
        "count": 400,
        "increases": [[f"U{i:03d}", f"Up item {i}", 10.0 + i, 12.0 + i] for i in range(40)],
        "decreases": [[f"D{i:03d}", f"Down item {i}", 20.0, 18.0] for i in range(20)],
        "new": [{"code": f"N{i:03d}", "desc": f"New ASM {i}", "price": 15.0, "qty": 4} for i in range(15)],
        "removed": [{"code": f"R{i:03d}", "desc": f"Removed ASM {i}", "price": 9.0, "qty": 0} for i in range(30)],
    }
    errors = [{"source": "southernhobby_rss", "distributor": "Southern Hobby",
               "error": "timeout " + ("z" * 400), "consecutive_runs": 2, "alert": False}]
    return changes, emails, asm, errors


def test_large_payload_fits_and_keeps_counts():
    changes, emails, asm, errors = big_run()
    raw_guess = json.dumps({
        "site_changes": changes,  # already slimmed; unslimmed would be larger
        "new_emails": emails, "asm_diff": asm, "errors": errors,
    }, ensure_ascii=False)
    # Even slimmed, an uncapped body is over the 8 KB ceiling; the builder must shrink it.
    print(f"uncapped slim body ~{len(raw_guess.encode())} bytes, {len(changes)} changes")
    body = payload.build(
        now_central=NOW,
        site_changes=changes,
        new_emails=emails,
        asm_diff=asm,
        errors=errors,
        noise_tot={"rename": 1, "encoding": 0, "flap": 0, "pending_removal": 0},
        sources_ok=11,
        gmail_status="ok: 3 new",
        run_at="2026-11-12T00:33:00+00:00",
        run_at_central="2026-11-11T18:33:00-06:00",
        run_url="https://github.com/ripandgo/distributor-watch/actions/runs/1",
    )
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    n = len(raw)
    print(f"compact payload {n} bytes, truncated={body['truncated']}")
    print(body["summary"])
    assert n <= payload.PAYLOAD_MAX_BYTES, f"payload {n} bytes exceeds {payload.PAYLOAD_MAX_BYTES}"
    assert n < 8000, "must stay well under ~8 KB"
    assert list(body.keys())[0] == "summary"
    assert body["summary"].startswith("Distributor watch")
    # Every source that changed is in source_counts with full totals
    by = {r["source"]: r for r in body["source_counts"]}
    assert set(by) == {"madal_catalog", "goldriver_products", "southernhobby_new", "acd_products", "phd_news"}
    assert by["madal_catalog"]["new"] == 24
    assert by["madal_catalog"]["price"] == 12
    assert by["madal_catalog"]["stock_in"] == 8
    assert by["madal_catalog"]["removed"] == 6
    assert by["madal_catalog"]["total"] == 50
    assert by["goldriver_products"]["price"] == 16
    assert by["southernhobby_new"]["new"] == 8
    assert by["acd_products"]["price"] == 5
    assert by["phd_news"]["removed"] == 4
    for r in body["source_counts"]:
        assert r["listed"] + r["omitted"] == r["total"], r
        # Summary must name the distributor and not drop its counts
        assert r["distributor"] in body["summary"]
    # Removals are listed when they fit (phd_news is removals-only and
    # is well under the per-source cap once the busy body is shrunk).
    listed_removed = [c for c in body["site_changes"] if c["kind"] == "removed"]
    assert body["truncated"] is True
    if by["phd_news"]["omitted"] == 0:
        assert len([c for c in listed_removed if c["source"] == "phd_news"]) == 4
    # A source should not list a removal while omitting a higher-priority kind.
    listed_kinds = {}
    for c in body["site_changes"]:
        listed_kinds.setdefault(c["source"], set()).add(payload.classify(c))
    for src, row in by.items():
        kinds = listed_kinds.get(src, set())
        if "removed" in kinds:
            for higher in ("new", "price", "stock_in", "changed_other"):
                if row.get(higher):
                    assert higher in kinds, (src, higher, kinds, row)
    # No full item dicts
    assert all("item" not in c for c in body["site_changes"])
    # Emails kept (ids the bot needs) and subject clipped
    assert {e["id"] for e in body["new_emails"]} <= {"abc111", "abc222", "abc333"}
    assert body["new_emails"], "should keep at least some emails"
    assert all(len(e["subject"]) <= payload.MAX_SUBJECT for e in body["new_emails"])
    # ASM counts preserved even when rows are capped
    a = body["asm_diff"]
    assert a["n_increases"] == 40 and a["n_decreases"] == 20
    assert a["n_new"] == 15 and a["n_removed"] == 30
    assert "40 up" in body["summary"] and "30 removed" in body["summary"]
    # Valid JSON round-trip
    json.loads(raw)


def test_quiet_shape():
    body = payload.build(
        now_central=NOW, site_changes=[], new_emails=[], asm_diff=None, errors=[],
        noise_tot={}, sources_ok=11, gmail_status="ok: 0 new",
        run_at="x", run_at_central="y", run_url=None,
    )
    assert body["summary"].startswith("Distributor watch")
    assert body["source_counts"] == []
    assert body["site_changes"] == []
    assert body["truncated"] is False
    assert payload.payload_size(body) < 1000


def test_priority_prefers_new_and_price():
    changes = (
        [ch("s", "X", "removed", i) for i in range(5)]
        + [ch("s", "X", "changed", 10 + i, fields={"desc": ["a", "b"]}) for i in range(5)]
        + [ch("s", "X", "changed", 20 + i, fields={"available": [False, True]}) for i in range(3)]
        + [ch("s", "X", "changed", 30 + i, fields={"price": ["2", "1"]}) for i in range(3)]
        + [ch("s", "X", "new", 40 + i) for i in range(3)]
    )
    picked = payload.select_changes(changes, cap_per_source=8)
    kinds = [payload.classify(c) for c in picked]
    assert kinds == ["new"] * 3 + ["price"] * 3 + ["stock_in"] * 2
    assert "removed" not in kinds
    # With enough room, removals come after every other kind — they are
    # listed, not dropped as a policy.
    picked_all = payload.select_changes(changes, cap_per_source=19)
    kinds_all = [payload.classify(c) for c in picked_all]
    assert kinds_all == (
        ["new"] * 3 + ["price"] * 3 + ["stock_in"] * 3
        + ["changed_other"] * 5 + ["removed"] * 5
    )


def _build(changes, emails=None, asm=None, errors=None, max_bytes=None, **kw):
    kwargs = dict(
        now_central=NOW,
        site_changes=changes,
        new_emails=emails or [],
        asm_diff=asm,
        errors=errors or [],
        noise_tot={"rename": 0, "encoding": 0, "flap": 0, "pending_removal": 0},
        sources_ok=11,
        gmail_status="ok: 0 new",
        run_at="2026-11-12T02:04:00+00:00",
        run_at_central="2026-11-11T20:04:00-06:00",
        run_url="https://github.com/ripandgo/distributor-watch/actions/runs/43138802373",
    )
    kwargs.update(kw)
    return payload.build(**kwargs, max_bytes=max_bytes)


def test_small_run_lists_removal_and_is_not_truncated():
    """Run #5 pass 1 shape: a stock flip + a catalog removal must both be listed.

    The 8:04 PM CT webhook was 972 bytes, well under the 6000-byte cap, yet
    marked truncated with '1 of 2 site changes listed' because removals were
    filtered out unconditionally.
    """
    changes = [
        ch(
            "madal_catalog", "Mad Al", "changed", 1,
            fields={"available": [True, False]},
            name="Some in-stock item that flipped",
        ),
        ch(
            "madal_catalog", "Mad Al", "removed", 2,
            name="Ultra PRO 12-Pocket Zippered PRO-Binder, Purple",
        ),
    ]
    body = _build(changes)
    n = payload.payload_size(body)
    assert n <= payload.PAYLOAD_MAX_BYTES
    assert n < 2000, n
    assert body["truncated"] is False
    assert "1 of 2" not in body["summary"]
    kinds = [c["kind"] for c in body["site_changes"]]
    assert kinds.count("changed") == 1
    assert kinds.count("removed") == 1
    names = {c["name"] for c in body["site_changes"]}
    assert any("PRO-Binder" in n for n in names)
    row = body["source_counts"][0]
    assert row["removed"] == 1
    assert row["changed_other"] == 1
    assert row["listed"] == 2
    assert row["omitted"] == 0
    assert row["total"] == 2


def test_mixed_kinds_all_listed_when_they_fit():
    changes = (
        [ch("s", "X", "new", i) for i in range(3)]
        + [ch("s", "X", "changed", 10 + i, fields={"price": ["2", "1"]}) for i in range(2)]
        + [ch("s", "X", "changed", 20 + i, fields={"available": [False, True]}) for i in range(2)]
        + [ch("s", "X", "removed", 30 + i) for i in range(2)]
    )
    body = _build(changes)
    assert body["truncated"] is False
    assert payload.payload_size(body) <= payload.PAYLOAD_MAX_BYTES
    kinds = [payload.classify(c) for c in body["site_changes"]]
    assert kinds == ["new"] * 3 + ["price"] * 2 + ["stock_in"] * 2 + ["removed"] * 2
    row = body["source_counts"][0]
    assert row["listed"] == 9 and row["omitted"] == 0


def test_tiny_budget_drops_removals_first_and_sets_truncated():
    changes = (
        [ch("s", "X", "new", i) for i in range(3)]
        + [ch("s", "X", "removed", 10 + i) for i in range(3)]
    )
    full = _build(changes)
    assert full["truncated"] is False
    assert len(full["site_changes"]) == 6
    # Just under the full size, so at least one item must be dropped.
    body = _build(changes, max_bytes=payload.payload_size(full) - 1)
    assert body["truncated"] is True
    assert len(body["site_changes"]) < 6
    kinds = [payload.classify(c) for c in body["site_changes"]]
    # Removals are dropped before any "new" item.
    if "removed" in kinds:
        assert kinds.count("new") == 3
    else:
        assert kinds.count("new") >= 1 or kinds == []
    row = body["source_counts"][0]
    assert row["listed"] + row["omitted"] == 6
    assert row["omitted"] > 0



if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("PASS", n)

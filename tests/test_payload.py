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
    # Removals are counts, not a flood of listed rows
    listed_removed = [c for c in body["site_changes"] if c["kind"] == "removed"]
    assert listed_removed == []
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


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("PASS", n)

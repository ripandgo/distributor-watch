import datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from watch import noise

T0 = dt.datetime(2026, 11, 11, 17, 0, tzinfo=dt.timezone.utc)
def it(name, price="10.00", avail=True, url="https://x/p/a", sku=""):
    return {"name": name, "price": price, "available": avail, "url": url, "sku": sku}

def test_pure_rename_same_key():
    ch, nz, _ = noise.filter_diff("s", {"1": it("Winx Club Box")}, {"1": it("WINX CLUB Booster Box")}, {}, T0)
    assert ch == [] and nz["rename"] == 1

def test_rename_with_price_change_is_real():
    ch, _, _ = noise.filter_diff("s", {"1": it("A")}, {"1": it("A2", price="9.00")}, {}, T0)
    assert len(ch) == 1 and set(ch[0]["fields"]) == {"name", "price"}

def test_encoding_glitch():
    ch, nz, _ = noise.filter_diff("s", {"1": it("Pok\u00e9mon \u2013 ETB")}, {"1": it("Pok\u00c3\u00a9mon &ndash; ETB")}, {}, T0)
    assert ch == [] and nz["encoding"] == 1

def test_rekeyed_same_sku_rename():
    ch, nz, _ = noise.filter_diff("s", {"1": it("A", sku="SKU1")}, {"2": it("A new", sku="SKU1", url="https://x/p/b")}, {}, T0)
    assert ch == [] and nz["rename"] == 1

def test_flap_dropped():
    old = {"1": it("A"), "2": it("B")}
    ch, nz, pend = noise.filter_diff("s", old, {"1": it("A")}, {}, T0)
    assert ch == [] and "2" in pend
    ch, nz, pend = noise.filter_diff("s", {"1": it("A")}, {"1": it("A"), "2": it("B")}, pend, T0 + dt.timedelta(minutes=5))
    assert ch == [] and nz["flap"] == 1 and pend == {}

def test_removal_reported_after_window():
    ch, _, pend = noise.filter_diff("s", {"1": it("A"), "2": it("B")}, {"1": it("A")}, {}, T0)
    ch, _, pend = noise.filter_diff("s", {"1": it("A")}, {"1": it("A")}, pend, T0 + dt.timedelta(minutes=10))
    assert [c["kind"] for c in ch] == ["removed"] and pend == {}

def test_new_and_price_reported_immediately():
    ch, _, _ = noise.filter_diff("s", {"1": it("A")}, {"1": it("A", price="12.00"), "3": it("C", url="https://x/p/c")}, {}, T0)
    assert sorted(c["kind"] for c in ch) == ["changed", "new"]

if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("PASS", n)

#!/usr/bin/env python3
"""
Distributor change monitor for Rip and Go.

Fetches public catalog / release / news sources for each distributor, saves a
snapshot to state.json, compares with the previous run and prints a readable
list of NEW, CHANGED and REMOVED items.

Usage:
  python3 distro_monitor.py                 # run all sources, update state
  python3 distro_monitor.py --only madal_catalog goldriver_products
  python3 distro_monitor.py --dry-run       # compare but do not save state
  python3 distro_monitor.py --list          # list sources
Report is also written to last_report.txt next to this script.
Read-only: only public GET requests, no login, no carts, no forms.
"""
import argparse, datetime as dt, html, json, os, re, sys, time, traceback
import urllib.request, urllib.error
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
REPORT = os.path.join(HERE, "last_report.txt")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_LIST = 40          # max lines printed per section per source
SHRINK_GUARD = 0.75    # if a source returns < 75% of last count ...
SHRINK_MIN_DROP = 25   # ... and at least this many fewer items, treat as a failed fetch


def get(url, tries=3, delay=1.0, timeout=40):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:  # noqa
            last = e
            time.sleep(delay * (i + 2))
    raise RuntimeError(f"GET {url} failed: {last}")


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def clean(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


# ---------------------------------------------------------------- Mad Al
def madal_catalog():
    """NetSuite SuiteCommerce public item search API. Prices are hidden without
    login, so this tracks new/removed items, AK/KY warehouse stock flags and
    pre-order status. API caps offset at 5000, so crawl by stock-facet slices."""
    base = "https://www.madal.com/api/items?fieldset=search&limit=100"
    items = {}
    for ak in ("Yes", "No"):
        for ky in ("Yes", "No"):
            off = 0
            while True:
                d = get_json(f"{base}&offset={off}&custitem_acs_stock_alaska={ak}&custitem_acs_stock_kentucky={ky}")
                batch = d.get("items", [])
                for it in batch:
                    iid = str(it["internalid"])
                    items[iid] = {
                        "name": it.get("displayname") or it.get("storedisplayname2") or it.get("itemid"),
                        "sku": it.get("itemid"),
                        "maker": it.get("custitem_manufacturer_for_facets"),
                        "stock_AK": ak, "stock_KY": ky,
                        "badge": it.get("custitem_ns_ib_badges") or "",
                        "preorder_status": "",
                        "url": "https://www.madal.com/" + (it.get("urlcomponent") or f"product/{iid}"),
                    }
                off += 100
                if not batch or off >= d.get("total", 0):
                    break
                time.sleep(0.3)
    for st in ("Pre~Order-Open", "Pre~Order-Open-After-Deadline", "Closed-for-Pre~Order", "Allocated"):
        off = 0
        while True:
            d = get_json(f"{base}&offset={off}&custitempreorder_status={st}")
            batch = d.get("items", [])
            for it in batch:
                iid = str(it["internalid"])
                if iid in items:
                    items[iid]["preorder_status"] = st.replace("~", "-")
            off += 100
            if not batch or off >= min(d.get("total", 0), 5000):
                break
            time.sleep(0.3)
    return items


# ---------------------------------------------------------------- Southern Hobby
SH = "https://www.southernhobby.com"


class IncompleteFetch(RuntimeError):
    """Raised when a fetch is known to be partial; the old snapshot is kept."""


def _is_cf_challenge(s):
    return ("request is being verified" in s or "One moment, please" in s) and "pageresults" not in s


def _sh_page(url, tries=6):
    """GET one Southern Hobby listing page. Cloudflare intermittently answers
    with an HTTP 200 'One moment, please... request is being verified' page
    (about 12 KB, no product rows). Retry those with backoff instead of
    treating them as the end of the list."""
    for i in range(tries):
        s = get(url)
        if not _is_cf_challenge(s):
            return s
        time.sleep(min(5 * (i + 1), 30))
    raise IncompleteFetch(f"{url}: Cloudflare check page {tries} times in a row")


def _sh_listing(path, max_pages, onpage=None, require_complete=False):
    """products_new.php / upcoming_products.php table rows:
    name | SKU | release date | order-due date | (price needs login).
    The page shows 'N items' and the current page number; pages past the end
    repeat the last page."""
    out = {}
    extra = f"&onpage={onpage}" if onpage else ""
    total = per_page = None
    for page in range(1, max_pages + 1):
        s = _sh_page(f"{SH}/{path}?page={page}{extra}")
        m = re.search(r"([\d,]+) items", s)
        if m:
            total = int(m.group(1).replace(",", ""))
        cur = re.search(r'<b class="pageresults">(\d+)</b>', s)
        if cur and int(cur.group(1)) != page:
            break  # past the last page: the site repeats the last one
        rows = re.findall(r'<a href="(https://www\.southernhobby\.com/[^"]+/p(\d+)/)">([^<]+)</a></td>(.*?)</tr>', s, re.S)
        if not rows:
            if total and len(out) < total and require_complete:
                raise IncompleteFetch(f"{path} p{page}: no product rows but only {len(out)}/{total} items read")
            break
        per_page = per_page or len(rows)
        new_on_page = 0
        for url, pid, name, rest in rows:
            cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", rest, re.S)]
            if pid not in out:
                new_on_page += 1
            out[pid] = {"name": clean(name), "sku": cells[0] if cells else "",
                        "release_date": cells[1] if len(cells) > 1 else "",
                        "order_due": cells[2] if len(cells) > 2 else "", "url": url}
        if new_on_page == 0 or (total and page * per_page >= total):
            break
        time.sleep(2.0)
    if require_complete:
        if not total:
            raise IncompleteFetch(f"{path}: could not read the site's item total")
        # allow a few duplicates/shifts if the list changes mid-crawl
        if len(out) < total - max(5, total // 100):
            raise IncompleteFetch(f"{path}: read {len(out)} of {total} items the site lists")
    return out


def southernhobby_upcoming():
    # ~3100 items: 250 per page = ~13 pages (was 40/page with a 30-page cap,
    # which could never reach the end and stopped at the first Cloudflare page)
    return _sh_listing("upcoming_products.php", 40, onpage=250, require_complete=True)


def southernhobby_new():
    return _sh_listing("products_new.php", 5)


def southernhobby_rss():
    root = ET.fromstring(get(f"{SH}/rss.php?language=en").encode())
    out = {}
    for it in root.iter("item"):
        link = it.findtext("link") or ""
        m = re.search(r"products_id=(\d+)", link)
        key = m.group(1) if m else link
        out[key] = {"name": clean(it.findtext("title")), "category": clean(it.findtext("category")), "url": link}
    return out


def southernhobby_solicitations():
    s = get(f"{SH}/weekly-solicitations/c486/")
    out = {}
    for url, name in re.findall(r'href="(https://www\.southernhobby\.com/weekly-solicitations/[a-z0-9-]+/c486_\d+/)"[^>]*>([^<]*)<', s):
        out[url] = {"name": clean(name) or url.rstrip("/").split("/")[-2].replace("-", " ").title(), "url": url}
    for url in re.findall(r'(https://www\.southernhobby\.com/weekly-solicitations/[a-z0-9-]+/c486_\d+/)', s):
        out.setdefault(url, {"name": url.rstrip("/").split("/")[-2].replace("-", " ").title(), "url": url})
    return out


# ---------------------------------------------------------------- PHD Games
def phd_news():
    """WordPress REST API: release announcements, sell sheets, news posts."""
    out = {}
    for page in (1, 2):
        d = get_json(f"https://www.phdgames.com/wp-json/wp/v2/posts?per_page=50&page={page}&_fields=id,date,modified,link,title")
        for p in d:
            out[str(p["id"])] = {"name": clean(p["title"]["rendered"]), "posted": p["date"][:10],
                                 "modified": p["modified"], "url": p["link"]}
        if len(d) < 50:
            break
        time.sleep(10)  # robots.txt crawl-delay 10
    return out


# ---------------------------------------------------------------- Gold River
def goldriver_products():
    """Shopify public products.json: prices are public here."""
    out = {}
    for page in range(1, 60):
        d = get_json(f"https://goldriverdistributors.com/products.json?limit=250&page={page}")
        prods = d.get("products", [])
        if not prods:
            break
        for p in prods:
            vs = p.get("variants", [])
            prices = sorted({v.get("price") for v in vs if v.get("price")}, key=lambda x: float(x))
            out[str(p["id"])] = {
                "name": p.get("title"), "type": p.get("product_type") or "",
                "price": "/".join(prices), "available": any(v.get("available") for v in vs),
                "published": (p.get("published_at") or "")[:10],
                "url": f"https://goldriverdistributors.com/products/{p.get('handle')}",
            }
        time.sleep(0.5)
    return out


# ---------------------------------------------------------------- ACD
def _sitemap_locs(url):
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", get(url))


def acd_products():
    """Public BigCommerce product sitemaps (no prices/stock without login):
    detects newly listed and delisted products."""
    idx = _sitemap_locs("https://www.acdd.com/sitemap.xml")
    out = {}
    for sm in idx:
        if "sitemap_products" not in sm:
            continue
        for u in _sitemap_locs(sm):
            slug = u.rstrip("/").split("/")[-1]
            out[u] = {"name": slug.replace("-", " ").strip().title(), "url": u}
        time.sleep(0.5)
    return out


def acd_news():
    out = {}
    for u in _sitemap_locs("https://www.acdd.com/sitemaps/sitemap_news_1.xml"):
        slug = u.rstrip("/").split("/")[-1]
        out[u] = {"name": slug.replace("-", " ").strip().title(), "url": u}
    return out


# ---------------------------------------------------------------- All Sports Marketing
ASM = "https://www.allsportsmarketing.net"


def allsports_presells():
    s = get(f"{ASM}/presells")
    out = {}
    for href, body in re.findall(r'<a\s+href="([^"]+)"[^>]*class="gallery-grid-image-link"[^>]*>(.*?)</a>', s, re.S):
        alt = re.search(r'alt="([^"]+)"', body)
        name = clean(alt.group(1)) if alt else href.split("/")[-1]
        out[name.lower()] = {"name": name, "sell_sheet": href, "url": f"{ASM}/presells"}
    if not out:  # fallback: text list
        m = re.search(r"<main.*?</main>", s, re.S)
        for line in _text_lines(m.group(0) if m else s):
            out[line.lower()] = {"name": line, "url": f"{ASM}/presells"}
    return out


def _text_lines(h):
    t = re.sub(r"<script.*?</script>|<style.*?</style>", "", h, flags=re.S)
    t = re.sub(r"<[^>]+>", "\n", t)
    return [clean(l) for l in t.split("\n") if clean(l)]


MONTHS = "JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER"


def allsports_releasedates():
    s = get(f"{ASM}/releasedates")
    m = re.search(r"<main.*?</main>", s, re.S)
    lines = _text_lines(m.group(0) if m else s)
    out, cur = {}, "TBD"
    started = False
    for l in lines:
        if l.lower() == "release dates":
            started = True
            continue
        if not started or l.startswith("[leave in place") or l.lower().startswith("view "):
            continue
        if re.fullmatch(rf"({MONTHS})", l.upper()):
            cur = l.upper() + " (date TBD)"
            continue
        if re.fullmatch(rf"({MONTHS})\s+\d{{1,2}}", l.upper()) or l.upper() in ("TBD", "TBA"):
            cur = l.upper()
            continue
        key = l.upper()
        out[key] = {"name": l, "release_date": cur, "url": f"{ASM}/releasedates"}
    return out


SOURCES = {
    "madal_catalog":              ("Mad Al", "Catalog: new items, AK/KY stock, pre-order status", madal_catalog),
    "southernhobby_new":          ("Southern Hobby", "New products list", southernhobby_new),
    "southernhobby_upcoming":     ("Southern Hobby", "Upcoming / pre-order list (release + order-due dates)", southernhobby_upcoming),
    "southernhobby_rss":          ("Southern Hobby", "RSS feed of newly added products", southernhobby_rss),
    "southernhobby_solicitations":("Southern Hobby", "Weekly solicitations", southernhobby_solicitations),
    "phd_news":                   ("PHD Games", "News & release posts", phd_news),
    "goldriver_products":         ("Gold River", "Full catalog with prices and availability", goldriver_products),
    "acd_products":               ("ACD", "Product listings (sitemap)", acd_products),
    "acd_news":                   ("ACD", "Blog / news posts", acd_news),
    "allsports_presells":         ("All Sports Marketing", "Presells page", allsports_presells),
    "allsports_releasedates":     ("All Sports Marketing", "Release dates page", allsports_releasedates),
}
IGNORE_FIELDS = {"url", "sell_sheet"}


def diff(old, new):
    added = [k for k in new if k not in old]
    removed = [k for k in old if k not in new]
    changed = []
    for k in new:
        if k in old:
            ch = {f: (old[k].get(f), new[k].get(f)) for f in set(old[k]) | set(new[k])
                  if f not in IGNORE_FIELDS and old[k].get(f) != new[k].get(f)}
            if ch:
                changed.append((k, ch))
    return added, removed, changed


def fmt_item(it, extra=()):
    bits = [str(it.get("name"))]
    for f in extra:
        if it.get(f) not in (None, "", False):
            bits.append(f"{f}={it[f]}")
    return " | ".join(bits) + (f"  <{it['url']}>" if it.get("url") else "")


SHOW = {
    "madal_catalog": ("sku", "preorder_status", "stock_AK", "stock_KY"),
    "southernhobby_new": ("sku", "release_date", "order_due"),
    "southernhobby_upcoming": ("sku", "release_date", "order_due"),
    "southernhobby_rss": ("category",),
    "phd_news": ("posted",),
    "goldriver_products": ("price", "available", "type"),
    "allsports_releasedates": ("release_date",),
    "allsports_presells": ("sell_sheet",),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--rebaseline", nargs="*", default=[],
                    help="save these sources' fresh fetch as the new baseline without reporting changes")
    a = ap.parse_args()
    if a.list:
        for k, (d, desc, _) in SOURCES.items():
            print(f"{k:30} {d:22} {desc}")
        return 0
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"Distributor monitor run {now}"]
    total_changes, errors = 0, []
    for key, (distro, desc, fn) in SOURCES.items():
        if a.only and key not in a.only:
            continue
        prev = state.get(key)
        try:
            items = fn()
            if not items:
                raise RuntimeError("returned 0 items")
            if key not in a.rebaseline and prev and prev.get("items"):
                n_prev = len(prev["items"])
                if len(items) < SHRINK_GUARD * n_prev and n_prev - len(items) >= SHRINK_MIN_DROP:
                    raise IncompleteFetch(f"only {len(items)} items vs {n_prev} last time; looks partial")
        except Exception as e:
            errors.append(f"{distro} / {key}: {e}; kept old snapshot")
            out.append(f"\n## {distro} - {desc}\n  !! FETCH ERROR: {e} (old snapshot kept, not compared)")
            continue
        hdr = f"\n## {distro} - {desc}  ({len(items)} items)"
        if key in a.rebaseline:
            was = len(prev["items"]) if prev and prev.get("items") else 0
            out.append(hdr + f"\n  Baseline reset (was {was} items); changes not reported this run.")
        elif not prev:
            out.append(hdr + "\n  Baseline saved (first run, nothing to compare).")
        else:
            added, removed, changed = diff(prev["items"], items)
            n = len(added) + len(removed) + len(changed)
            total_changes += n
            if n == 0:
                out.append(hdr + "\n  No changes.")
            else:
                out.append(hdr)
                ex = SHOW.get(key, ())
                if added:
                    out.append(f"  NEW ({len(added)}):")
                    out += [f"   + {fmt_item(items[k], ex)}" for k in added[:MAX_LIST]]
                    if len(added) > MAX_LIST: out.append(f"   ... and {len(added)-MAX_LIST} more")
                if changed:
                    out.append(f"  CHANGED ({len(changed)}):")
                    for k, ch in changed[:MAX_LIST]:
                        desc_ch = "; ".join(f"{f}: {o!r} -> {nv!r}" for f, (o, nv) in sorted(ch.items()))
                        out.append(f"   * {items[k].get('name')}: {desc_ch}  <{items[k].get('url','')}>")
                    if len(changed) > MAX_LIST: out.append(f"   ... and {len(changed)-MAX_LIST} more")
                if removed:
                    out.append(f"  REMOVED ({len(removed)}):")
                    out += [f"   - {fmt_item(prev['items'][k])}" for k in removed[:MAX_LIST]]
                    if len(removed) > MAX_LIST: out.append(f"   ... and {len(removed)-MAX_LIST} more")
        state[key] = {"fetched_at": now, "count": len(items), "items": items}
    out.append(f"\nSummary: {total_changes} change(s); {len(errors)} source error(s).")
    for e in errors:
        out.append(f"  error: {e}")
    text = "\n".join(out)
    print(text)
    open(REPORT, "w").write(text + "\n")
    if not a.dry_run:
        tmp = STATE + ".tmp"
        json.dump(state, open(tmp, "w"), indent=0, sort_keys=True)
        os.replace(tmp, STATE)
    return 0


if __name__ == "__main__":
    sys.exit(main())

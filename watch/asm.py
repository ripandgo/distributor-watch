"""All Sports Marketing inventory PDF diff (same parser as distro-monitor/asm/parse.py).

The PDF arrives as an attachment on Alex Beard's 'ASM Inventory <date>' email.
pdftotext -layout -> rows 'CODE  DESCRIPTION [QTY]  PRICE  NFD'.
Diff vs the previous parsed list: price increases/decreases, new, removed.
"""
import re, subprocess, tempfile, os


def pdf_to_text(pdf_bytes):
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in.pdf")
        open(src, "wb").write(pdf_bytes)
        out = subprocess.run(["pdftotext", "-layout", src, "-"], check=True,
                             capture_output=True, timeout=60).stdout
    return out.decode("utf-8", "replace")


def parse_text(text):
    out = {}
    for l in text.splitlines():
        m = re.match(r'^(\S+)\s{2,}(.*?)\s+(-?[\d,]*\.\d+)\s+(-?[\d,]*\.\d+)\s*$', l.rstrip("\n"))
        if not m:
            continue
        code, rest, p, n = m.groups()
        qty, desc = None, rest
        mm = re.search(r"\s(\d+)$", rest)
        if mm:
            endcol = m.start(2) + mm.end(1)
            if endcol >= 55:
                qty = int(mm.group(1))
                desc = rest[:mm.start()].strip()
        out[code] = {"code": code, "desc": desc.strip(), "qty": qty,
                     "price": float(p.replace(",", "")), "nfd": float(n.replace(",", ""))}
    return out


def report_date(text):
    m = re.search(r"Date\s+(\d{1,2})/(\d{1,2})/(\d{4})", text)
    return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}" if m else None


def diff(old, new):
    inc, dec = [], []
    for c, it in new.items():
        if c in old and old[c]["price"] != it["price"]:
            row = [c, it["desc"], old[c]["price"], it["price"]]
            (inc if it["price"] > old[c]["price"] else dec).append(row)
    return {
        "increases": inc,
        "decreases": dec,
        "new": [new[c] for c in new if c not in old],
        "removed": [old[c] for c in old if c not in new],
    }


def is_empty(d):
    return not any(d.get(k) for k in ("increases", "decreases", "new", "removed"))

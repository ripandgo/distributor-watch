"""Read-only Gmail check over IMAP (imap.gmail.com:993).

Needs env GMAIL_USER and GMAIL_APP_PASSWORD (a Google app password; requires
2-Step Verification on the account). Mailboxes are opened with EXAMINE
(readonly) and bodies fetched with BODY.PEEK, so nothing is marked read,
moved, labelled or deleted.

Message ids are reported as the Gmail API id (hex of X-GM-MSGID), so the bot
can open them directly with the Gmail connector.
"""
import email, email.header, email.utils, imaplib, os, re

MAILBOXES = ['"[Gmail]/All Mail"', '"[Gmail]/Trash"']
LOOKBACK = os.environ.get("WATCH_GMAIL_LOOKBACK", "2d")

# Mirrors the Gmail-watch rules in /workspace/distro-monitor/README.md,
# plus marketing@universaldist.com (Universal's newsletters moved there in Nov 2026).
SENDER_RULES = {
    "Mad Al":         'from:sales@madal.com -subject:("order receipt")',
    "Southern Hobby": 'from:info@southernhobby.com "this week at Southern Hobby"',
    "PHD Games":      'from:no-reply@phdgames.com subject:("Weekly News" OR "Pre-Orders")',
    "Gold River":     'from:contact@goldriverdistributors.com -subject:(order OR invoice OR shipment)',
    "Universal":      'from:(universaldist.com OR promo@alliance-games.com)',
    "ACD":            'from:(marketing@acdd.com OR jaredk@acdd.com)',
    "All Sports Marketing": 'from:allsportsmarketing.net',
}
EXCLUDE = '-subject:"Distributor watch" -from:ripndgo@gmail.com'
ASM_INVENTORY = re.compile(r"\bASM Inventory\b", re.I)


def gmail_query():
    rules = " OR ".join(f"({q})" for q in SENDER_RULES.values())
    return f"newer_than:{LOOKBACK} {EXCLUDE} ({rules})"


def creds():
    u, p = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD")
    return (u, p.replace(" ", "")) if u and p else None


def _dec(h):
    try:
        return str(email.header.make_header(email.header.decode_header(h or "")))
    except Exception:
        return h or ""


def _quote(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def which_distributor(frm, subject):
    f = frm.lower()
    for name, key in (("Mad Al", "madal.com"), ("Southern Hobby", "southernhobby.com"),
                      ("PHD Games", "phdgames.com"), ("Gold River", "goldriverdistributors.com"),
                      ("Universal", "universaldist.com"), ("Universal", "alliance-games.com"),
                      ("ACD", "acdd.com"), ("All Sports Marketing", "allsportsmarketing.net")):
        if key in f:
            return name
    return ""


def check(seen):
    """Returns (new_emails, asm_pdfs). new_emails: list of dicts.
    asm_pdfs: list of (msg_id, filename, bytes) for new ASM Inventory mails."""
    c = creds()
    if not c:
        return None, []
    user, pw = c
    q = _quote(gmail_query())
    found, asm = {}, []
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=60)
    try:
        M.login(user, pw)
        for box in MAILBOXES:
            typ, _ = M.select(box, readonly=True)
            if typ != "OK":
                continue
            typ, data = M.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW", q)
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            for uid in uids:
                typ, resp = M.uid("FETCH", uid, "(X-GM-MSGID X-GM-THRID BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                if typ != "OK" or not resp or not isinstance(resp[0], tuple):
                    continue
                meta = resp[0][0].decode()
                gid = re.search(r"X-GM-MSGID (\d+)", meta)
                thr = re.search(r"X-GM-THRID (\d+)", meta)
                if not gid:
                    continue
                mid = format(int(gid.group(1)), "x")
                if mid in seen or mid in found:
                    continue
                h = email.message_from_bytes(resp[0][1])
                subj, frm = _dec(h.get("Subject")), _dec(h.get("From"))
                found[mid] = {
                    "id": mid,
                    "thread_id": format(int(thr.group(1)), "x") if thr else None,
                    "from": frm, "subject": subj,
                    "date": email.utils.parsedate_to_datetime(h.get("Date")).isoformat() if h.get("Date") else None,
                    "distributor": which_distributor(frm, subj),
                    "mailbox": box.strip('"'),
                }
                if "allsportsmarketing.net" in frm.lower() and ASM_INVENTORY.search(subj):
                    typ, full = M.uid("FETCH", uid, "(BODY.PEEK[])")
                    if typ == "OK" and full and isinstance(full[0], tuple):
                        msg = email.message_from_bytes(full[0][1])
                        for part in msg.walk():
                            fn = part.get_filename() or ""
                            if part.get_content_type() == "application/pdf" or fn.lower().endswith(".pdf"):
                                asm.append((mid, _dec(fn), part.get_payload(decode=True)))
                                break
    finally:
        try:
            M.logout()
        except Exception:
            pass
    emails = sorted(found.values(), key=lambda e: e["date"] or "")
    return emails, asm

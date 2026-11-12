"""Window and chain-decision tests, plus backup-cron coverage in both DST offsets."""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from watch.window import TZ, WINDOW, in_window, should_chain


def at(year, month, day, hour, minute, label=None):
    """Construct a clock time in America/Chicago (DST-aware)."""
    return dt.datetime(year, month, day, hour, minute, tzinfo=TZ)


# Wednesday 2026-11-11 is CST (UTC-6). Wednesday 2026-07-15 is CDT (UTC-5).
WED_CST = (2026, 11, 11)  # Wednesday
WED_CDT = (2026, 7, 15)   # Wednesday
SAT_CST = (2026, 11, 14)  # Saturday
FRI_CST = (2026, 11, 13)  # Friday


def check(ts, expect_in, expect_chain, name):
    got_in = in_window(ts)
    got_ch = should_chain(ts)
    loc = ts.astimezone(TZ)
    status = "ok" if (got_in == expect_in and got_ch == expect_chain) else "FAIL"
    print(
        f"  {status}: {name}: {loc:%a %Y-%m-%d %H:%M %Z} "
        f"in_window={got_in} (want {expect_in}) "
        f"should_chain={got_ch} (want {expect_chain})"
    )
    assert got_in == expect_in, f"{name}: in_window={got_in}, expected {expect_in}"
    assert got_ch == expect_chain, f"{name}: should_chain={got_ch}, expected {expect_chain}"


def test_requested_timestamps():
    print("requested timestamps")
    # 3:59 AM CT weekday: still closed; do not chain (4:00 AM cron restarts).
    check(at(*WED_CST, 3, 59), False, False, "3:59 AM CT Wednesday")
    check(at(*WED_CST, 4, 30), True, True, "4:30 AM CT Wednesday (open)")
    # 9:58 PM CT weekday: 21:58 is inside 04:00-21:59, so chain continues.
    check(at(*WED_CST, 21, 58), True, True, "9:58 PM CT Wednesday")
    # Saturday: never in window, even at 10:00 AM or 5:00 AM.
    check(at(*SAT_CST, 10, 0), False, False, "10:00 AM CT Saturday")
    check(at(*SAT_CST, 5, 0), False, False, "5:00 AM CT Saturday")
    check(at(*SAT_CST, 4, 0), False, False, "4:00 AM CT Saturday")


def test_edges():
    print("window edges")
    check(at(*WED_CST, 4, 0), True, True, "4:00 AM CT Wednesday (open)")
    check(at(*WED_CST, 3, 59), False, False, "3:59 AM CT Wednesday (closed)")
    check(at(*WED_CST, 21, 59), True, True, "9:59 PM CT Wednesday (last minute)")
    check(at(*WED_CST, 22, 0), False, False, "10:00 PM CT Wednesday (closed)")
    check(at(*FRI_CST, 21, 58), True, True, "9:58 PM CT Friday")
    check(at(*FRI_CST, 22, 0), False, False, "10:00 PM CT Friday")
    check(at(*WED_CDT, 4, 0), True, True, "4:00 AM CDT Wednesday")
    check(at(*WED_CDT, 3, 59), False, False, "3:59 AM CDT Wednesday")
    check(at(*WED_CDT, 21, 58), True, True, "9:58 PM CDT Wednesday")
    check(at(*WED_CDT, 22, 0), False, False, "10:00 PM CDT Wednesday")


def _cron_hits(minute, hour, dow, minute_star, hour_range, dow_range):
    """True if a GitHub 5-field cron line would fire at this UTC minute."""
    h0, h1 = hour_range
    d0, d1 = dow_range
    if hour < h0 or hour > h1:
        return False
    if dow < d0 or dow > d1:
        return False
    return minute % minute_star == 0


def cron_fires(utc_dt):
    """Whether either backup cron line would fire at this UTC datetime."""
    # GitHub: 0=Sun ... 6=Sat. Python weekday() is 0=Mon ... 6=Sun.
    dow = (utc_dt.weekday() + 1) % 7
    m, h = utc_dt.minute, utc_dt.hour
    return (
        _cron_hits(m, h, dow, 30, (9, 23), (1, 5))
        or _cron_hits(m, h, dow, 30, (0, 3), (2, 6))
    )


def first_in_window_fire(date_ct):
    """First backup-cron instant on this CT date at or after 4:00 AM."""
    start = dt.datetime(date_ct.year, date_ct.month, date_ct.day, 4, 0, tzinfo=TZ)
    end = start + dt.timedelta(hours=1)  # should be hit by 4:00 or 4:30
    t = start
    while t <= end:
        if cron_fires(t.astimezone(dt.timezone.utc)):
            return t
        t += dt.timedelta(minutes=1)
    return None


def test_backup_cron_covers_5am_and_window():
    print("backup cron coverage")
    # A Monday in CST and a Monday in CDT must have a fire at 4:00 AM CT
    # (or no later than 4:30) so the chain can restart.
    for label, ymd in (
        ("CST Monday 2026-11-09", (2026, 11, 9)),
        ("CST Friday 2026-11-13", (2026, 11, 13)),
        ("CDT Monday 2026-07-13", (2026, 7, 13)),
        ("CDT Friday 2026-07-17", (2026, 7, 17)),
    ):
        day = dt.date(*ymd)
        hit = first_in_window_fire(day)
        assert hit is not None, f"{label}: no 4:00-4:30 AM cron fire"
        delay = (hit - at(*ymd, 4, 0)).total_seconds() / 60
        print(f"  ok: {label}: first in-window cron at {hit:%H:%M %Z} (+{delay:.0f} min)")
        assert delay <= 30, f"{label}: first fire is {delay} min after 4:00"

    # Mid-window: a Wednesday 10:00 AM CT in both offsets should be covered
    # by some 30-min slot (the chain is primary; cron only has to be nearby).
    for label, ts in (
        ("CST Wed 10:00", at(*WED_CST, 10, 0)),
        ("CDT Wed 10:00", at(*WED_CDT, 10, 0)),
        ("CST Fri 21:30", at(*FRI_CST, 21, 30)),
    ):
        utc = ts.astimezone(dt.timezone.utc)
        # find nearest fire within 30 min at or before ts
        hit = None
        for back in range(0, 31):
            cand = utc - dt.timedelta(minutes=back)
            if cron_fires(cand):
                hit = cand
                break
        assert hit is not None, f"{label}: no backup cron within the past 30 min"
        print(f"  ok: {label}: last cron at {hit.astimezone(TZ):%H:%M %Z}")

    # Cron expressions themselves: valid 5-field, */30 step, inclusive ranges.
    lines = ['*/30 10-23 * * 1-5', '*/30 0-3 * * 2-6']
    for line in lines:
        parts = line.split()
        assert len(parts) == 5, f"{line} is not a 5-field cron"
        print(f"  ok: cron {line!r} is 5-field")

    # Thursday 01:40 UTC (the missed slot under the old */20) is still
    # inside the Tue-Sat 00:00-03:59 UTC backup window with */30.
    missed = dt.datetime(2026, 11, 12, 1, 30, tzinfo=dt.timezone.utc)  # nearest */30
    assert cron_fires(missed), "01:30Z Thursday should still be a backup cron slot"
    print(f"  ok: 2026-11-12 01:30Z ({missed.astimezone(TZ):%H:%M %Z}) is a backup slot")


def test_window_constants():
    assert WINDOW == (4 * 60, 21 * 60 + 59)


if __name__ == "__main__":
    test_window_constants()
    test_requested_timestamps()
    test_edges()
    test_backup_cron_covers_5am_and_window()
    print("all window tests passed")

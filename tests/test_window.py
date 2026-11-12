"""Window and chain-decision tests, plus backup-cron coverage in both DST offsets.

Also covers overnight bridge planning: next_window_start and bridge_plan.
"""
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from watch.window import (
    TZ,
    WINDOW,
    MAX_BRIDGE_SLEEP,
    BRIDGE_EARLY_SLACK,
    in_window,
    should_chain,
    next_window_start,
    bridge_plan,
)


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
    # 3:59 AM CT weekday: still closed; do not chain (bridge / 4:00 AM cron restarts).
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
    # (or no later than 4:30) so the chain can restart if the bridge misses.
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
    # Must match .github/workflows/watch.yml (union of CST and CDT offsets).
    lines = ["*/30 9-23 * * 1-5", "*/30 0-3 * * 2-6"]
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
    assert MAX_BRIDGE_SLEEP == 5 * 3600 + 30 * 60
    assert BRIDGE_EARLY_SLACK == 2 * 60


def _assert_start(ts, want, name):
    got = next_window_start(ts)
    # Compare as Chicago-local instants; equal instants may differ only by tz label
    # after a DST boundary when constructed different ways, so use the epoch.
    status = "ok" if got == want else "FAIL"
    print(
        f"  {status}: {name}: {ts:%a %Y-%m-%d %H:%M %Z} -> "
        f"{got:%a %Y-%m-%d %H:%M %Z} (want {want:%a %H:%M %Z})"
    )
    assert got == want, f"{name}: next_window_start={got}, expected {want}"


def test_next_window_start():
    print("next_window_start")
    # Wednesday 10:00 PM CST -> Thursday 4:00 AM CST.
    _assert_start(at(*WED_CST, 22, 0), at(2026, 11, 12, 4, 0), "Wed 10:00 PM CST")
    # Before 4:00 on a weekday is that same morning.
    _assert_start(at(*WED_CST, 3, 59), at(*WED_CST, 4, 0), "Wed 3:59 AM CST")
    _assert_start(at(*WED_CST, 4, 0), at(*WED_CST, 4, 0), "Wed 4:00 AM CST exactly")
    # After 4:00 rolls to the next weekday.
    _assert_start(at(*WED_CST, 4, 1), at(2026, 11, 12, 4, 0), "Wed 4:01 AM CST")
    _assert_start(at(*WED_CST, 10, 0), at(2026, 11, 12, 4, 0), "Wed 10:00 AM CST")
    # Friday 10:00 PM CST (and the weekend) -> Monday 4:00 AM CST.
    _assert_start(at(*FRI_CST, 22, 0), at(2026, 11, 16, 4, 0), "Fri 10:00 PM CST")
    _assert_start(at(*SAT_CST, 10, 0), at(2026, 11, 16, 4, 0), "Sat 10:00 AM CST")
    _assert_start(at(2026, 11, 15, 3, 59), at(2026, 11, 16, 4, 0), "Sun 3:59 AM CST")
    _assert_start(at(2026, 11, 15, 4, 0), at(2026, 11, 16, 4, 0), "Sun 4:00 AM CST")
    # UTC input converts correctly (Wed 10 PM CST = Thu 04:00 UTC in November).
    utc = at(*WED_CST, 22, 0).astimezone(dt.timezone.utc)
    _assert_start(utc, at(2026, 11, 12, 4, 0), "Wed 10:00 PM CST given as UTC")
    # CDT midweek.
    _assert_start(at(*WED_CDT, 22, 0), at(2026, 7, 16, 4, 0), "Wed 10:00 PM CDT")
    # DST fall-back night: Sunday 2026-11-01. Friday 10 PM CDT -> Monday 4 AM CST.
    _assert_start(at(2026, 10, 30, 22, 0), at(2026, 11, 2, 4, 0), "Fri 10:00 PM CDT over fall-back")
    # DST spring-forward night: Sunday 2026-03-08. Friday 10 PM CST -> Monday 4 AM CDT.
    _assert_start(at(2026, 3, 6, 22, 0), at(2026, 3, 9, 4, 0), "Fri 10:00 PM CST over spring-forward")


def _advance(now, seconds):
    """Advance `now` by real elapsed seconds (DST-safe; not wall-clock math)."""
    return dt.datetime.fromtimestamp(now.timestamp() + seconds, TZ)


def real_seconds(start, end):
    """Elapsed seconds between two aware datetimes, including DST shifts."""
    return end.timestamp() - start.timestamp()


def walk_bridge(start, limit=20):
    """Follow bridge hops from `start`. Returns (hops, arrival, last_plan).

    Each hop is (when_the_hop_started, plan). Arrival is the clock after the
    last hop's sleep, which is when a loop run should be dispatched.
    """
    now = start
    hops = []
    for _ in range(limit):
        plan = bridge_plan(now)
        hops.append((now, plan))
        now = _advance(now, plan.sleep_seconds)
        if plan.action == "loop":
            return hops, now, plan
    raise AssertionError(f"did not reach a loop dispatch in {limit} hops from {start}")


def _assert_arrival(start, want, name, min_hops=1):
    hops, arrival, last = walk_bridge(start)
    n = len(hops)
    delta = abs((arrival - want).total_seconds())
    # Intermediate hops must stay under the 6h-safe cap and not already be a loop
    # except the last one.
    for i, (when, plan) in enumerate(hops):
        assert plan.sleep_seconds <= MAX_BRIDGE_SLEEP, (
            f"{name} hop {i} slept {plan.sleep_seconds}s, over the cap"
        )
        assert plan.sleep_seconds >= 0
        assert plan.action in ("bridge", "loop")
        if i < n - 1:
            assert plan.action == "bridge", f"{name} hop {i} was {plan.action}, expected bridge"
        assert plan.target == want, f"{name} hop {i} target={plan.target}, expected {want}"
    assert last.action == "loop", f"{name}: last action was {last.action}"
    assert n >= min_hops, f"{name}: only {n} hop(s), expected at least {min_hops}"
    assert delta <= 1, f"{name}: arrived {arrival}, want {want} (off by {delta}s)"
    hop_desc = ", ".join(
        f"{plan.sleep_seconds}s->{plan.action}" for _, plan in hops
    )
    print(
        f"  ok: {name}: {n} hop(s) [{hop_desc}] "
        f"arrive {arrival:%a %Y-%m-%d %H:%M %Z}"
    )
    return hops, arrival


def test_bridge_plan_wed_cst_to_thu():
    print("bridge plan: Wed 10:00 PM CST -> Thu 4:00 AM CST")
    start = at(*WED_CST, 22, 0)
    want = at(2026, 11, 12, 4, 0)
    # 6 hours even: first hop 5h30m bridge, second hop 30m loop.
    hops, arrival = _assert_arrival(start, want, "Wed 10 PM CST", min_hops=2)
    assert hops[0][1].sleep_seconds == MAX_BRIDGE_SLEEP
    assert hops[1][1].sleep_seconds == 30 * 60
    assert arrival == want


def test_bridge_plan_fri_to_mon():
    print("bridge plan: Fri 10:00 PM CST -> Mon 4:00 AM CST (multiple hops)")
    start = at(*FRI_CST, 22, 0)
    want = at(2026, 11, 16, 4, 0)
    hops, arrival = _assert_arrival(start, want, "Fri 10 PM CST to Mon 4 AM")
    # 54 hours: 9 full 5h30m hops + a 4h30m final hop.
    total = sum(p.sleep_seconds for _, p in hops)
    expected = int(real_seconds(start, want))
    assert total == expected, f"slept {total}s, expected {expected}s"
    assert len(hops) > 2, "weekend crossing should take more than two hops"
    assert arrival == want


def test_bridge_plan_cdt():
    print("bridge plan: Wed 10:00 PM CDT -> Thu 4:00 AM CDT")
    start = at(*WED_CDT, 22, 0)
    want = at(2026, 7, 16, 4, 0)
    hops, arrival = _assert_arrival(start, want, "Wed 10 PM CDT", min_hops=2)
    assert hops[0][1].sleep_seconds == MAX_BRIDGE_SLEEP
    assert hops[1][1].sleep_seconds == 30 * 60
    assert arrival == want
    # 6 hours in CDT is the same clock span as in CST (no transition that night).
    assert real_seconds(start, want) == 6 * 3600


def test_bridge_plan_dst_transitions():
    print("bridge plan: DST transition nights")
    # Fall back, Sunday 2026-11-01: Friday 10 PM CDT -> Monday 4 AM CST.
    # The repeated hour makes this 55 hours of real time, not 54.
    start = at(2026, 10, 30, 22, 0)
    want = at(2026, 11, 2, 4, 0)
    span = real_seconds(start, want)
    assert span == 55 * 3600, f"fall-back span was {span}s, expected 55h"
    hops, arrival = _assert_arrival(start, want, "fall-back Fri 10 PM CDT -> Mon 4 AM CST")
    assert sum(p.sleep_seconds for _, p in hops) == int(span)
    assert arrival == want

    # Spring forward, Sunday 2026-03-08: Friday 10 PM CST -> Monday 4 AM CDT.
    # The skipped hour makes this 53 hours of real time, not 54.
    start = at(2026, 3, 6, 22, 0)
    want = at(2026, 3, 9, 4, 0)
    span = real_seconds(start, want)
    assert span == 53 * 3600, f"spring-forward span was {span}s, expected 53h"
    hops, arrival = _assert_arrival(start, want, "spring-forward Fri 10 PM CST -> Mon 4 AM CDT")
    assert sum(p.sleep_seconds for _, p in hops) == int(span)
    assert arrival == want


def test_bridge_plan_woke_early():
    print("bridge plan: woke slightly early / already at or in the window")
    target = at(2026, 11, 12, 4, 0)  # Thursday 4:00 AM CST

    # 2 minutes early: sleep the remaining 120s, then start the loop.
    p = bridge_plan(at(2026, 11, 12, 3, 58))
    assert p.action == "loop" and p.sleep_seconds == 120 and p.target == target
    print("  ok: 3:58 AM CST -> sleep 120s, action loop")

    # 1 minute early.
    p = bridge_plan(at(2026, 11, 12, 3, 59))
    assert p.action == "loop" and p.sleep_seconds == 60 and p.target == target
    print("  ok: 3:59 AM CST -> sleep 60s, action loop")

    # Just over the slack (3 minutes early): one short hop that still ends in loop.
    p = bridge_plan(at(2026, 11, 12, 3, 57))
    assert p.action == "loop" and p.sleep_seconds == 180 and p.target == target
    print("  ok: 3:57 AM CST -> sleep 180s, action loop")

    # Exactly 4:00, and a minute later: already in the window, dispatch immediately.
    p = bridge_plan(target)
    assert p.action == "loop" and p.sleep_seconds == 0 and p.target == target
    print("  ok: 4:00 AM CST exactly -> sleep 0s, action loop")
    p = bridge_plan(at(2026, 11, 12, 4, 1))
    assert p.action == "loop" and p.sleep_seconds == 0 and p.target == target
    print("  ok: 4:01 AM CST -> sleep 0s, action loop")

    # Mid-window also starts a loop immediately (today's 4:00 is the target).
    p = bridge_plan(at(*WED_CST, 10, 0))
    assert p.action == "loop" and p.sleep_seconds == 0
    assert p.target == at(*WED_CST, 4, 0)
    print("  ok: Wed 10:00 AM CST (in window) -> sleep 0s, action loop")


def test_bridge_plan_sleep_cap():
    print("bridge plan: sleep cap")
    assert MAX_BRIDGE_SLEEP == 19800
    # A hop more than 5h30m from the target must cap the sleep and continue.
    p = bridge_plan(at(*WED_CST, 22, 0))
    assert p.sleep_seconds == MAX_BRIDGE_SLEEP
    assert p.action == "bridge"
    print("  ok: 6h remaining caps at 5h30m and continues as a bridge")


if __name__ == "__main__":
    test_window_constants()
    test_requested_timestamps()
    test_edges()
    test_backup_cron_covers_5am_and_window()
    test_next_window_start()
    test_bridge_plan_wed_cst_to_thu()
    test_bridge_plan_fri_to_mon()
    test_bridge_plan_cdt()
    test_bridge_plan_dst_transitions()
    test_bridge_plan_woke_early()
    test_bridge_plan_sleep_cap()
    print("all window tests passed")

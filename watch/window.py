"""Weekday 4:00-21:59 America/Chicago (4 AM CT = 5 AM ET) window, DST-aware via ZoneInfo.

When the window closes, a bridge job sleeps (in hops of at most MAX_BRIDGE_SLEEP)
until the next weekday 04:00, then starts a looping scan. Cron is only a backup.
"""
import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
WINDOW = (4 * 60, 21 * 60 + 59)  # 04:00-21:59 Central (5 AM Eastern), Mon-Fri

# Stay well under GitHub Actions' 6-hour (360 min) job limit.
MAX_BRIDGE_SLEEP = 5 * 3600 + 30 * 60  # 5 hours 30 minutes
# If a bridge wakes this close to the target, start the scan (after sleeping
# the remaining seconds) instead of dispatching another hop.
BRIDGE_EARLY_SLACK = 2 * 60  # 2 minutes


def _aware(now):
    """Interpret naive datetimes as America/Chicago; convert aware ones to it."""
    return now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)


def in_window(now):
    """True if `now` is Mon-Fri 04:00-21:59 America/Chicago (DST-aware)."""
    loc = _aware(now)
    m = loc.hour * 60 + loc.minute
    return loc.weekday() < 5 and WINDOW[0] <= m <= WINDOW[1]


def should_chain(now):
    """True if a follow-on looping workflow_dispatch should be triggered.

    Only when *now* is still inside the window. We do not chain just because
    the next 5-minute slot would be in-window: a delayed pre-window cron
    landing at 3:59 AM CT would otherwise dispatch immediately, and a fast
    follow-on could tight-loop skipped jobs until 4:00. When the window has
    closed, the overnight bridge (see bridge_plan) restarts the chain at the
    next weekday 4:00 AM CT; the in-window backup cron is the fallback.
    """
    return in_window(now)


def next_window_start(now):
    """Next weekday 04:00 America/Chicago.

    If `now` is at or before 04:00 on a weekday, that morning's 04:00 is
    returned (so 04:00 exactly is the current open, and 03:59 is two minutes
    away). After 04:00, or on a weekend, this is the following weekday's
    04:00 (Friday evening and the weekend therefore roll to Monday).
    """
    loc = _aware(now)
    candidate = loc.replace(hour=4, minute=0, second=0, microsecond=0)
    if loc <= candidate and loc.weekday() < 5:
        return candidate
    day = candidate.date() + dt.timedelta(days=1)
    while True:
        cand = dt.datetime(day.year, day.month, day.day, 4, 0, tzinfo=TZ)
        if cand.weekday() < 5:
            return cand
        day += dt.timedelta(days=1)


@dataclass(frozen=True)
class BridgePlan:
    """How long this bridge hop should sleep, and what it should dispatch after.

    `action` is "loop" (start a looping scan) or "bridge" (another overnight
    hop). `target` is the next weekday 04:00 the hops are aiming at, or today's
    04:00 when we are already inside the window.
    """

    sleep_seconds: int
    action: str
    target: dt.datetime


def bridge_plan(now, max_sleep=MAX_BRIDGE_SLEEP, early_slack=BRIDGE_EARLY_SLACK):
    """Sleep length and next action for an overnight bridge hop.

    A hop sleeps `min(time until target, max_sleep)` seconds (default 5h30m,
    under the 6-hour Actions limit) and then dispatches either another bridge
    or a `loop=true` run. If `now` is already inside the window, or within
    `early_slack` seconds of the target (woke slightly early), the action is
    "loop" and any remaining wait is at most that slack.
    """
    loc = _aware(now)
    if in_window(loc):
        today_start = loc.replace(hour=4, minute=0, second=0, microsecond=0)
        return BridgePlan(0, "loop", today_start)
    target = next_window_start(loc)
    # Use POSIX timestamps, not datetime subtraction: two datetimes that
    # share a ZoneInfo tzinfo subtract as wall-clock times and would miss
    # the gained or lost hour on a DST transition night.
    remaining = target.timestamp() - loc.timestamp()
    if remaining <= early_slack:
        return BridgePlan(max(0, int(remaining)), "loop", target)
    sleep = min(int(remaining), int(max_sleep))
    left = remaining - sleep
    action = "loop" if left <= early_slack else "bridge"
    return BridgePlan(sleep, action, target)

"""Weekday 5:00-21:59 America/Chicago window, DST-aware via ZoneInfo."""
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Chicago")
WINDOW = (5 * 60, 21 * 60 + 59)  # 05:00-21:59 Central, Mon-Fri


def in_window(now):
    """True if `now` is Mon-Fri 05:00-21:59 America/Chicago (DST-aware)."""
    loc = now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)
    m = loc.hour * 60 + loc.minute
    return loc.weekday() < 5 and WINDOW[0] <= m <= WINDOW[1]


def should_chain(now):
    """True if a follow-on workflow_dispatch should be triggered.

    Only when *now* is still inside the window. We do not chain just because
    the next 5-minute slot would be in-window: a delayed pre-window cron
    landing at 4:59 AM CT would otherwise dispatch immediately, and a fast
    follow-on could tight-loop skipped jobs until 5:00. The 5:00 AM CT
    backup cron is what restarts the chain the next weekday morning.
    """
    loc = now.astimezone(TZ) if now.tzinfo else now.replace(tzinfo=TZ)
    return in_window(loc)

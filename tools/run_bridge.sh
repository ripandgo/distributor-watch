#!/usr/bin/env bash
# Overnight bridge hop: sleep until the next weekday 4:00 AM America/Chicago
# (in slices of at most 5h30m, under the 6-hour Actions job limit), then
# dispatch either another bridge hop or a looping scan. No scanning, no
# webhook, and no state commit.
#
# After the sleep, the plan is recomputed so a DST shift or an early wake is
# handled. A remaining wait of at most 2 minutes is finished in this job so
# the loop starts inside the window; a longer remainder becomes another hop
# (this job must not sleep again past the timeout).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/.."
if command -v python >/dev/null 2>&1; then PY=python; else PY=python3; fi

plan_line() {
  $PY -c "
import datetime as dt
from watch.window import bridge_plan
p = bridge_plan(dt.datetime.now(dt.timezone.utc))
print(f'{p.sleep_seconds} {p.action} {p.target.isoformat()}')
"
}

read -r SLEEP ACTION TARGET <<< "$(plan_line)"
echo "bridge: target $TARGET; sleeping ${SLEEP}s, then dispatch $ACTION"
if [ "$SLEEP" -gt 0 ]; then
  sleep "$SLEEP"
fi

# Recompute after waking (DST, clock skew, or a slightly early wake).
read -r SLEEP ACTION TARGET <<< "$(plan_line)"
if [ "$SLEEP" -gt 0 ] && [ "$SLEEP" -le 120 ]; then
  echo "bridge: woke early; sleeping ${SLEEP}s more to reach $TARGET"
  sleep "$SLEEP"
  ACTION=loop
  SLEEP=0
fi

if [ "$ACTION" = "loop" ] && [ "$SLEEP" -le 120 ]; then
  echo "bridge: window reached ($TARGET); dispatching loop=true"
  gh workflow run watch.yml --ref main -f loop=true
else
  echo "bridge: still ${SLEEP}s from $TARGET; dispatching another bridge hop"
  gh workflow run watch.yml --ref main -f bridge=true
fi

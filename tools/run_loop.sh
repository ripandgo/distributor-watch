#!/usr/bin/env bash
# Run python -m watch every 5 minutes for up to LOOP_SECS (default 1080 = 18 min).
# Commits public state after each successful pass. Stops when the in-code
# weekday 5 AM-10 PM CT window ends, when the budget is used, or after a
# single pass when LOOP=0 (workflow_dispatch default).
set -uo pipefail
ARGS=${WATCH_ARGS:-}
LOOP=${LOOP:-0}
LOOP_SECS=${LOOP_SECS:-1080}
START=$(date +%s)
HERE=$(cd "$(dirname "$0")" && pwd)
LOG=/tmp/watch-pass.log

commit() {
  bash "$HERE/commit_state.sh"
}

pass=0
while true; do
  pass=$((pass + 1))
  echo "=== pass $pass at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  set +e
  # shellcheck disable=SC2086
  python -m watch $ARGS 2>&1 | tee "$LOG"
  st=${PIPESTATUS[0]}
  set -e
  if [ "$st" -ne 0 ]; then
    echo "watch pass $pass failed with exit $st"
    exit "$st"
  fi
  if grep -q "outside watch window" "$LOG"; then
    echo "left the watch window; stopping"
    break
  fi
  commit
  if [ "$LOOP" != "1" ]; then
    break
  fi
  now=$(date +%s)
  elapsed=$((now - START))
  # Don't start another ~4-5 min scan if we'd overrun the loop budget.
  # The next scheduled job is 20 min after this one started.
  if [ $((elapsed + 60)) -ge "$LOOP_SECS" ]; then
    echo "loop budget reached (${elapsed}s); stopping"
    break
  fi
  target=$((START + pass * 300))
  now=$(date +%s)
  if [ "$now" -lt "$target" ]; then
    if [ "$target" -gt $((START + LOOP_SECS)) ]; then
      echo "next slot is past the loop budget; stopping"
      break
    fi
    echo "sleeping $((target - now))s until next 5-min slot"
    sleep $((target - now))
  else
    echo "pass overran the 5-min slot; starting next immediately"
  fi
done

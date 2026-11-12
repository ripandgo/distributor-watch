#!/usr/bin/env bash
# Exit 0 if an overnight bridge should be dispatched: the weekday window is
# closed, and no other bridge run is already in_progress or queued.
# Exit 1 otherwise (still inside the window, or a bridge is already going).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/.."
if command -v python >/dev/null 2>&1; then PY=python; else PY=python3; fi

if $PY -c "import datetime as dt; from watch.window import in_window; raise SystemExit(0 if in_window(dt.datetime.now(dt.timezone.utc)) else 1)"; then
  echo "Still inside the watch window; not starting a bridge"
  exit 1
fi

if [ -z "${GITHUB_REPOSITORY:-}" ]; then
  echo "Outside the watch window; GITHUB_REPOSITORY not set, assuming no other bridge"
  exit 0
fi

# Exclude this run when GITHUB_RUN_ID is set (it is numeric). 0 matches no run.
run_id="${GITHUB_RUN_ID:-0}"
other=$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/watch.yml/runs?per_page=50" \
  --jq "
    def active:
      (.status == \"in_progress\" or .status == \"queued\" or .status == \"waiting\"
       or .status == \"pending\" or .status == \"requested\");
    def is_bridge:
      (.name == \"distributor-watch-bridge\" or .display_title == \"distributor-watch-bridge\");
    [.workflow_runs[] | select(.id != ${run_id} and active and is_bridge)]
    | length
  ")

if [ "${other:-0}" -gt 0 ]; then
  echo "A bridge run is already in_progress or queued; not dispatching another"
  exit 1
fi
echo "Outside the watch window and no bridge is running; should dispatch a bridge"
exit 0

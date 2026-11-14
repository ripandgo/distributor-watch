#!/usr/bin/env bash
# Exit 0 if a follow-on workflow_dispatch should be triggered. In 24/7 mode
# (watch.window.ALWAYS_ON) this is always true; under the legacy schedule it
# means still inside the weekday 4 AM-10 PM America/Chicago window.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE/.."
if command -v python >/dev/null 2>&1; then PY=python; else PY=python3; fi
$PY -c "import sys, datetime as dt; from watch.window import should_chain; sys.exit(0 if should_chain(dt.datetime.now(dt.timezone.utc)) else 1)"

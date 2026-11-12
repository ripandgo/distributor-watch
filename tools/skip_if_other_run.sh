#!/usr/bin/env bash
# If another run of this workflow is already in_progress, queued, waiting,
# pending, or requested (besides $GITHUB_RUN_ID), write skip=true and exit 0.
# Otherwise write skip=false. Used so a backup cron does not start a second
# chain alongside a live one.
set -euo pipefail

say() {
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "$1" >> "$GITHUB_OUTPUT"
  else
    echo "$1"
  fi
}

if [ -z "${GITHUB_REPOSITORY:-}" ] || [ -z "${GITHUB_RUN_ID:-}" ]; then
  echo "GITHUB_REPOSITORY / GITHUB_RUN_ID not set; not skipping"
  say "skip=false"
  exit 0
fi

other=$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/watch.yml/runs?per_page=20" \
  --jq "[.workflow_runs[] | select(.id != ${GITHUB_RUN_ID} and (.status == \"in_progress\" or .status == \"queued\" or .status == \"waiting\" or .status == \"pending\" or .status == \"requested\"))] | length")

if [ "${other:-0}" -gt 0 ]; then
  echo "Another distributor-watch run is already in_progress or queued; skipping this run."
  say "skip=true"
  exit 0
fi
echo "No other in-progress or queued run; continuing."
say "skip=false"

#!/usr/bin/env bash
# If another *comparable* run of this workflow is already in_progress, queued,
# waiting, pending, or requested (besides $GITHUB_RUN_ID), write skip=true and
# exit 0. Otherwise write skip=false.
#
# Bridge runs (run-name / display title "distributor-watch-bridge") and
# scanning runs ("distributor-watch") are counted separately, so:
#   - a loop started by a bridge is not skipped just because the bridge is
#     still finishing its dispatch step
#   - a second bridge exits if one is already sleeping or queued
#   - a backup cron at 4:00 AM CT exits if a scanning chain is already going
#     (the morning overlap of cron + bridge-dispatched loop)
#
# Set IS_BRIDGE=true in the environment when this run is a bridge hop.
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

# GITHUB_RUN_ID is numeric; want_bridge is a jq literal we set ourselves.
if [ "${IS_BRIDGE:-false}" = "true" ]; then
  want_bridge=true
  kind="bridge"
else
  want_bridge=false
  kind="scanning"
fi

# Bridge runs are titled distributor-watch-bridge via the workflow run-name.
# Match on both .name and .display_title; GitHub has used each for run-name.
other=$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/watch.yml/runs?per_page=50" \
  --jq "
    def active:
      (.status == \"in_progress\" or .status == \"queued\" or .status == \"waiting\"
       or .status == \"pending\" or .status == \"requested\");
    def is_bridge:
      (.name == \"distributor-watch-bridge\" or .display_title == \"distributor-watch-bridge\");
    [.workflow_runs[]
      | select(.id != ${GITHUB_RUN_ID} and active
               and (if ${want_bridge} then is_bridge else (is_bridge | not) end))]
    | length
  ")

if [ "${other:-0}" -gt 0 ]; then
  echo "Another distributor-watch ${kind} run is already in_progress or queued; skipping this run."
  say "skip=true"
  exit 0
fi
echo "No other in-progress or queued ${kind} run; continuing."
say "skip=false"

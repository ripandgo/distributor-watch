#!/usr/bin/env bash
# Commit and push public state/ after a watch pass. state/private is gitignored.
# Shallow checkouts and a follow-on run's push can race, so fetch enough
# history, rebase onto origin, and retry.
set -u
git config user.name  "distributor-watch[bot]"
git config user.email "distributor-watch@users.noreply.github.com"
git add state/
if git diff --cached --quiet; then
  echo "state unchanged"
  exit 0
fi
git commit -q -m "state: $(date -u +%Y-%m-%dT%H:%MZ)"
REF="${GITHUB_REF_NAME:-main}"
if [ "$(git rev-parse --is-shallow-repository 2>/dev/null)" = "true" ]; then
  git fetch --quiet --deepen=50 origin "$REF" || git fetch --quiet origin "$REF"
else
  git fetch --quiet origin "$REF" || true
fi
for i in 1 2 3 4 5; do
  if git pull --rebase --quiet origin "$REF" && git push -q origin "HEAD:$REF"; then
    exit 0
  fi
  echo "push attempt $i failed; retrying"
  git rebase --abort >/dev/null 2>&1 || true
  git fetch --quiet origin "$REF" || true
  sleep $((5 * i))
done
echo "push failed after retries"
exit 1

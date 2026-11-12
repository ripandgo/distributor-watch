#!/usr/bin/env bash
# Commit and push public state/ after a watch pass. state/private is gitignored.
set -u
git config user.name  "distributor-watch[bot]"
git config user.email "distributor-watch@users.noreply.github.com"
git add state/
if git diff --cached --quiet; then
  echo "state unchanged"
  exit 0
fi
git commit -q -m "state: $(date -u +%Y-%m-%dT%H:%MZ)"
for i in 1 2 3; do
  git pull -q --rebase origin "${GITHUB_REF_NAME}" && git push -q origin "HEAD:${GITHUB_REF_NAME}" && exit 0
  sleep 5
done
echo "push failed"
exit 1

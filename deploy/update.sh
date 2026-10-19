#!/usr/bin/env bash
# Runs every minute: if GitHub has new code that passes the tests, switch to it and restart.
set -uo pipefail
cd "$(dirname "$0")/.."
git fetch -q origin main || exit 0
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
NEW=$(git rev-parse --short origin/main)
mkdir -p data
grep -q "^$NEW$" data/failed-builds 2>/dev/null && exit 0   # already tried this one
TMP=$(mktemp -d)
git worktree add -q --detach "$TMP" origin/main
if (cd "$TMP" && python3 -m unittest discover -s tests -q >/tmp/ricochet-tests.log 2>&1); then
  git worktree remove --force "$TMP"
  git reset -q --hard origin/main
  sudo /usr/bin/systemctl restart ricochet
  echo "$(date -u +%FT%TZ) deployed $NEW" | tee -a data/deploys.log
else
  git worktree remove --force "$TMP"
  echo "$NEW" >> data/failed-builds
  echo "$(date -u +%FT%TZ) $NEW failed tests, not deployed (see /tmp/ricochet-tests.log)" | tee -a data/deploys.log
fi

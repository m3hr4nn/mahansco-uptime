#!/usr/bin/env bash
set -euo pipefail

git config user.name uptime-bot
git config user.email uptime-bot@users.noreply.github.com
git add -- docs/state.json docs/history.json docs/uptime_daily.json
if git diff --cached --quiet; then exit 0; fi
git commit -m "chore: update monitor state [skip ci]"
branch="${GITHUB_REF_NAME:?Expected workflow branch}"
for attempt in 1 2 3; do
  if git push origin "HEAD:refs/heads/$branch"; then exit 0; fi
  git fetch origin "$branch"
  if ! git rebase "origin/$branch"; then
    git rebase --abort
    echo '::error::Concurrent changes conflict with probe data. Local generated commit preserved; inspect recovery artifact. No remote data overwritten.'
    exit 1
  fi
done
echo '::error::Could not persist monitor state after three attempts; inspect recovery artifact.'
exit 1

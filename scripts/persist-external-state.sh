#!/usr/bin/env bash
set -euo pipefail

data_dir="${1:?Usage: persist-external-state.sh DATA_GIT_CHECKOUT [BRANCH]}"
branch="${2:-main}"
for file in state.json history.json uptime_daily.json; do
  test -f "$data_dir/$file" || { echo "Missing $data_dir/$file" >&2; exit 1; }
done

git -C "$data_dir" config user.name "uptime-runner"
git -C "$data_dir" config user.email "uptime-runner@users.noreply.invalid"
git -C "$data_dir" add -- state.json history.json uptime_daily.json
if git -C "$data_dir" diff --cached --quiet; then exit 0; fi
git -C "$data_dir" commit -m "chore: persist monitor observations [skip ci]"

for attempt in 1 2 3; do
  if git -C "$data_dir" push origin "HEAD:refs/heads/$branch"; then exit 0; fi
  git -C "$data_dir" fetch origin "$branch"
  if ! git -C "$data_dir" rebase "origin/$branch"; then
    git -C "$data_dir" rebase --abort
    echo "Concurrent observation conflict. Local commit preserved; remote data was not overwritten." >&2
    exit 1
  fi
done
echo "Could not persist observations after three serialized retries." >&2
exit 1

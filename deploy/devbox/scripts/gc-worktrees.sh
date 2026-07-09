#!/usr/bin/env bash
# Prune sg-devbox worktrees older than 24 hours and let git forget them.
set -euo pipefail

WORK_ROOT="${SG_WORK_ROOT:-/var/lib/sg-devbox/work}"
REPO="${SG_REPO:-/home/sgagent/SteamGuard}"
MAX_HOURS="${SG_WT_MAX_HOURS:-24}"

if [[ ! -d "$WORK_ROOT" ]]; then
    exit 0
fi

find "$WORK_ROOT" -mindepth 1 -maxdepth 1 -type d -mmin +$((MAX_HOURS * 60)) -print0 \
| while IFS= read -r -d '' wt; do
    echo "gc: removing $wt"
    (cd "$REPO" && git worktree remove --force "$wt") || rm -rf "$wt"
done

(cd "$REPO" && git worktree prune -v || true)

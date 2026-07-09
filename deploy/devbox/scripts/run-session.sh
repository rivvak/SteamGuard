#!/usr/bin/env bash
# Thin shell wrapper — the real logic is in orchestrator.py. This exists as a
# fallback for manual invocation on the VM ("what if the orchestrator is down
# and I want to try a task by hand?").
#
#   ./run-session.sh <session-id> <task-file> [source]
#
# Prints a JSON blob on stdout with the same shape orchestrator.py returns.

set -euo pipefail

SID="${1:?session id required}"
TASK_FILE="${2:?task file required}"
SOURCE="${3:-manual}"

curl -sS -X POST http://127.0.0.1:9090/session \
    -H "Authorization: Bearer ${DEVBOX_TOKEN:?}" \
    -H "Content-Type: application/json" \
    -d "$(jq -Rs --arg src "$SOURCE" --arg init "manual:$USER" \
        '{task: ., source: $src, initiator: $init}' < "$TASK_FILE")"

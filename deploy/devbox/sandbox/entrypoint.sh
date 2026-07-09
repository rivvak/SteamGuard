#!/usr/bin/env bash
# sg-sandbox entrypoint — runs one /create job to completion.
#
# Required env (set by orchestrator via `docker run --env`):
#   NVIDIA_NIM_API_KEY       — passed to FCC as ANTHROPIC_AUTH_TOKEN (FCC treats this as the
#                              upstream key when routing to nvidia_nim/*)
#   GITHUB_TOKEN             — scoped App install token; injected into gh via GH_TOKEN
#   PROMPT                   — user's /create prompt (full text)
#   MODEL                    — e.g. nvidia_nim/moonshotai/kimi-k2.5
#   SESSION_ID               — used purely for log tagging
#
# Optional:
#   MAX_TURNS                — default 200
#   MAX_THINKING_TOKENS      — default 32000
#   WATCHDOG_IDLE_SECONDS    — if claude produces no stdout for this long, we kill it. default 300
#   WALL_CLOCK_SECONDS       — hard cap. default 21600 (6h)

set -euo pipefail

log() { printf '[sandbox %s] %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

# ─── EARLY status writer ─────────────────────────────────────────────────────
# The orchestrator polls /out/status.json to distinguish 'container crashed at
# boot' (no status file) from 'container ran and reported terminal state'. We
# write status.json IMMEDIATELY, before any command that could fail, so even
# early crashes leave a trail.
STATUS_FILE="/out/status.json"
early_write_status() {
    local state="$1" msg="${2:-}"
    if [ -w /out ]; then
        printf '{"session_id":"%s","state":"%s","message":%s,"ended_at":"%s"}\n' \
            "${SESSION_ID:-unknown}" \
            "$state" \
            "$(printf '%s' "${msg}" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))' 2>/dev/null || echo '""')" \
            "$(date -u +%FT%TZ)" \
            > "$STATUS_FILE" 2>/dev/null || true
    fi
}
early_write_status "booting" "entrypoint started"

# On any early failure, dump the last log line into status.json before exiting
_early_trap() {
    local rc=$?
    if [ -w /out ] && [ ! -f "/out/.past_early_init" ]; then
        early_write_status "error" "early init failure rc=${rc} at line ${BASH_LINENO[0]:-?}"
    fi
    exit "$rc"
}
trap _early_trap EXIT

: "${NVIDIA_NIM_API_KEY:?NVIDIA_NIM_API_KEY required}"
: "${GITHUB_TOKEN:?GITHUB_TOKEN required}"
: "${PROMPT:?PROMPT required}"
: "${MODEL:?MODEL required}"
: "${SESSION_ID:=unknown}"

MAX_TURNS="${MAX_TURNS:-200}"
MAX_THINKING_TOKENS="${MAX_THINKING_TOKENS:-32000}"
WATCHDOG_IDLE_SECONDS="${WATCHDOG_IDLE_SECONDS:-300}"
WALL_CLOCK_SECONDS="${WALL_CLOCK_SECONDS:-21600}"

# ─── gh auth (read-only research; sandbox cannot push) ──────────────────────
export GH_TOKEN="$GITHUB_TOKEN"

# git config is best-effort — sandbox does not push commits. If HOME isn't
# writable (e.g. tmpfs mount permissions), don't kill the run over it.
{
    git config --global user.name  "sg-sandbox"
    git config --global user.email "sg-sandbox@rivvak.app"
    git config --global commit.gpgsign false
    git config --global tag.gpgsign false
    git config --global init.defaultBranch main
} 2>/dev/null || log "WARN: git config failed (HOME=$HOME not writable?) — continuing; sandbox doesn't push anyway"

# ─── FCC startup (background) ───────────────────────────────────────────────
# FCC's launcher reads MODEL + ANTHROPIC_AUTH_TOKEN from env and proxies /v1/messages
# through to the configured upstream (nvidia_nim in our case).
export MODEL
export ANTHROPIC_AUTH_TOKEN="$NVIDIA_NIM_API_KEY"
export ANTHROPIC_BASE_URL="http://127.0.0.1:${FCC_PORT:-8082}"
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
export CLAUDE_CODE_AUTO_COMPACT_WINDOW=190000

log "starting FCC (model=$MODEL)"
fcc-server \
    --host "${FCC_HOST:-0.0.0.0}" \
    --port "${FCC_PORT:-8082}" \
    >/tmp/fcc.log 2>&1 &
FCC_PID=$!

# Wait up to 60s for FCC /v1/models to respond
for i in $(seq 1 60); do
    if curl -sf -o /dev/null "${ANTHROPIC_BASE_URL}/v1/models"; then
        log "FCC ready after ${i}s"
        break
    fi
    if ! kill -0 "$FCC_PID" 2>/dev/null; then
        log "FCC died during startup — dumping log"
        tail -50 /tmp/fcc.log >&2
        exit 71
    fi
    sleep 1
done

if ! curl -sf -o /dev/null "${ANTHROPIC_BASE_URL}/v1/models"; then
    log "FCC did not become ready in 60s"
    tail -50 /tmp/fcc.log >&2
    exit 72
fi

# ─── output & cleanup plumbing ──────────────────────────────────────────────
# STATUS_FILE already set at top of script; ARTIFACT/LOG_ARTIFACT are new.
ARTIFACT="/out/build.zip"
LOG_ARTIFACT="/out/claude.log"
: > "$LOG_ARTIFACT"

write_status() {
    local state="$1" exit_code="${2:-null}"
    cat > "$STATUS_FILE" <<JSON
{"session_id":"${SESSION_ID}","state":"${state}","exit_code":${exit_code},"model":"${MODEL}","ended_at":"$(date -u +%FT%TZ)"}
JSON
}

# Past the fragile early-init phase — reset trap to the final finish handler.
touch /out/.past_early_init 2>/dev/null || true
trap - EXIT
write_status "running" "null"

finish() {
    local code=$?
    log "finish handler — exit=$code"
    # kill FCC
    kill "$FCC_PID" 2>/dev/null || true
    # always try to package whatever's in /workspace so the user gets partial work back
    if [ -d /workspace ] && [ -n "$(ls -A /workspace 2>/dev/null || true)" ]; then
        log "packaging /workspace → $ARTIFACT"
        (cd /workspace && zip -qr "$ARTIFACT" . -x '.git/*' 'node_modules/*' '__pycache__/*' '.venv/*' 'venv/*') || {
            log "zip failed"
        }
    else
        log "no /workspace contents to package"
    fi
    write_status "finished" "$code"
    exit "$code"
}
trap finish EXIT INT TERM

# ─── run claude -p with the prompt piped in ────────────────────────────────
# We use `claude -p` (headless), piping PROMPT via stdin. Tools include Web{Search,Fetch}
# so FCC's DuckDuckGo backend gives us free web research. Bash is scoped tight via
# --permission-mode acceptEdits inside the sandbox container (safe: it's throwaway).
log "invoking claude -p (max_turns=$MAX_TURNS, thinking=$MAX_THINKING_TOKENS)"

# Watchdog: rewrite last-activity timestamp on every log line; a separate loop
# kills claude if nothing has been written for $WATCHDOG_IDLE_SECONDS.
LAST_TS_FILE=/tmp/claude.last
date +%s > "$LAST_TS_FILE"

(
    sleep 30  # grace period before watchdog engages
    while :; do
        now=$(date +%s)
        last=$(cat "$LAST_TS_FILE" 2>/dev/null || echo "$now")
        idle=$(( now - last ))
        if [ "$idle" -ge "$WATCHDOG_IDLE_SECONDS" ]; then
            log "watchdog: claude idle ${idle}s ≥ ${WATCHDOG_IDLE_SECONDS}s — killing"
            pkill -TERM -f 'claude' 2>/dev/null || true
            sleep 5
            pkill -KILL -f 'claude' 2>/dev/null || true
            exit 0
        fi
        sleep 15
    done
) &
WATCHDOG_PID=$!

# Hard wall-clock cap
(
    sleep "$WALL_CLOCK_SECONDS"
    log "wall-clock cap ${WALL_CLOCK_SECONDS}s reached — killing"
    pkill -TERM -f 'claude' 2>/dev/null || true
    sleep 5
    pkill -KILL -f 'claude' 2>/dev/null || true
) &
CAP_PID=$!

# Run claude, teeing output to log + updating watchdog heartbeat
set +e
# Use `fcc-claude` (FCC's wrapper) rather than raw `claude`: it scrubs inherited
# ANTHROPIC_* vars, points the CLI at the local FCC proxy, and forwards all argv.
printf '%s' "$PROMPT" | fcc-claude \
    -p \
    --max-turns "$MAX_TURNS" \
    --max-thinking-tokens "$MAX_THINKING_TOKENS" \
    --permission-mode acceptEdits \
    --allowedTools "Read,Edit,Write,Bash,WebSearch,WebFetch" \
    2>&1 \
    | while IFS= read -r line; do
        date +%s > "$LAST_TS_FILE"
        printf '%s\n' "$line" | tee -a "$LOG_ARTIFACT"
      done
CLAUDE_RC=${PIPESTATUS[0]}
set -e

kill "$WATCHDOG_PID" "$CAP_PID" 2>/dev/null || true
log "claude exited rc=$CLAUDE_RC"
exit "$CLAUDE_RC"

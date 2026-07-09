#!/usr/bin/env bash
# One-shot bootstrap for the sg-devbox VM.
# Debian 12. Run as root the first time; idempotent-ish afterward.
#
#   curl -fsSL https://raw.githubusercontent.com/rivvak/SteamGuard/main/deploy/devbox/bootstrap.sh | sudo bash
#
# Prereqs (do these BEFORE running this script):
#   1. /etc/sg-devbox/env exists with NVIDIA_NIM_API_KEY, DEVBOX_TOKEN,
#      SG_HEAL_APP_ID, SG_HEAL_INSTALLATION_ID, SG_HEAL_PRIVATE_KEY_B64,
#      REPO_SLUG=rivvak/SteamGuard.
#   2. SSH signing key at /etc/sg-devbox/sg-heal-bot (0600, sgagent-owned).
#   3. The `sgagent` user's public key is added as a **deploy key with write
#      access** on rivvak/SteamGuard (used only as a signing identity; pushes
#      themselves use the GitHub App token).

set -euo pipefail

echo "[1/9] apt update + base packages"
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg git jq python3 python3-venv python3-pip \
    docker.io tini uuid-runtime

echo "[2/9] create sgagent user"
if ! id sgagent >/dev/null 2>&1; then
    useradd --system --create-home --shell /bin/bash sgagent
fi
usermod -aG docker sgagent

echo "[3/9] install gh CLI"
if ! command -v gh >/dev/null 2>&1; then
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list
    apt-get update
    apt-get install -y gh
fi

echo "[4/9] install Claude Code CLI (Anthropic)"
# Anthropic ships Claude Code as a Node/npm CLI. We install via the official
# tarball to avoid pulling all of Node's package ecosystem.
if ! command -v claude-code >/dev/null 2>&1; then
    apt-get install -y nodejs npm
    npm install -g @anthropic-ai/claude-code
fi

echo "[5/9] pull free-claude-code image"
# free-claude-code is MIT, Anthropic-Messages-API proxy in front of NIM.
# We build from source rather than pulling a random image to keep the supply
# chain visible. Repo: https://github.com/Alishahryar1/free-claude-code
FCC_SRC=/opt/free-claude-code
if [[ ! -d "$FCC_SRC" ]]; then
    git clone --depth 1 https://github.com/Alishahryar1/free-claude-code.git "$FCC_SRC"
fi
(cd "$FCC_SRC" && docker build -t sg/free-claude-code:local .)

echo "[6/9] clone the SteamGuard repo into the agent home"
sudo -u sgagent bash <<'EOF'
set -euo pipefail
cd ~
if [[ ! -d SteamGuard ]]; then
    # Uses gh's default auth (we'll authenticate as the GitHub App per-session,
    # this clone only needs read).
    git clone https://github.com/rivvak/SteamGuard.git
fi
cd SteamGuard
git remote set-url origin https://github.com/rivvak/SteamGuard.git
git config user.name  "rivvak-sg-heal[bot]"
git config user.email "rivvak-sg-heal[bot]@users.noreply.github.com"
# SSH signing
git config gpg.format ssh
git config user.signingkey /etc/sg-devbox/sg-heal-bot.pub
git config commit.gpgsign true
mkdir -p /var/lib/sg-devbox/work
EOF
chown -R sgagent:sgagent /var/lib/sg-devbox

echo "[7/9] install repo scripts under /opt/sg-devbox"
install -d -o sgagent -g sgagent /opt/sg-devbox
install -m 0755 -o sgagent -g sgagent \
    "$(dirname "$0")/scripts/run-session.sh"   /opt/sg-devbox/run-session.sh
install -m 0755 -o sgagent -g sgagent \
    "$(dirname "$0")/scripts/open-pr.sh"       /opt/sg-devbox/open-pr.sh
install -m 0755 -o sgagent -g sgagent \
    "$(dirname "$0")/scripts/gc-worktrees.sh"  /opt/sg-devbox/gc-worktrees.sh
install -m 0755 -o sgagent -g sgagent \
    "$(dirname "$0")/scripts/path-guard.py"    /opt/sg-devbox/path-guard.py
install -m 0755 -o sgagent -g sgagent \
    "$(dirname "$0")/scripts/orchestrator.py"  /opt/sg-devbox/orchestrator.py

# Python deps for the orchestrator
sudo -u sgagent python3 -m venv /opt/sg-devbox/venv
sudo -u sgagent /opt/sg-devbox/venv/bin/pip install --quiet \
    fastapi uvicorn httpx pyjwt cryptography pathspec

echo "[8/9] install systemd units"
install -m 0644 "$(dirname "$0")/systemd/sg-fcc.service"          /etc/systemd/system/sg-fcc.service
install -m 0644 "$(dirname "$0")/systemd/sg-devbox-orch.service"  /etc/systemd/system/sg-devbox-orch.service
install -m 0644 "$(dirname "$0")/systemd/sg-devbox-cleanup.service" /etc/systemd/system/sg-devbox-cleanup.service
install -m 0644 "$(dirname "$0")/systemd/sg-devbox-cleanup.timer"   /etc/systemd/system/sg-devbox-cleanup.timer

systemctl daemon-reload
systemctl enable --now sg-fcc.service sg-devbox-orch.service sg-devbox-cleanup.timer

echo "[9/9] smoke test"
sleep 3
# FCC should answer on loopback with a 401 (no bearer)
curl -sf -o /dev/null -w "fcc:%{http_code}\n" http://127.0.0.1:8787/v1/messages || true
# Orchestrator health
curl -sf http://127.0.0.1:9090/health && echo

echo "bootstrap complete. Tail logs with:  journalctl -u sg-fcc -u sg-devbox-orch -f"

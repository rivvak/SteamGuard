#!/usr/bin/env bash
# One-shot bootstrap for the sg-devbox VM.
# Debian 12. Run as root the first time; idempotent-ish afterward.
#
#   curl -fsSL https://raw.githubusercontent.com/rivvak/SteamGuard/main/deploy/devbox/bootstrap.sh | sudo bash
#
# Prereqs (do these BEFORE running this script):
#   1. /etc/sg-devbox/env exists with NVIDIA_NIM_API_KEY, DEVBOX_TOKEN,
#      SG_HEAL_APP_ID, SG_HEAL_INSTALLATION_ID, SG_HEAL_PRIVATE_KEY_B64,
#      REPO_SLUG=rivvak/SteamGuard, FCC_URL=http://127.0.0.1:8082.
#   2. SSH signing key at /etc/sg-devbox/sg-heal-bot (0600, sgagent-owned).
#   3. The `sgagent` user's public key is added as a **deploy key with write
#      access** on rivvak/SteamGuard (used only as a signing identity; pushes
#      themselves use the GitHub App token).

set -euo pipefail

REPO_SLUG="${REPO_SLUG:-rivvak/SteamGuard}"

echo "[1/12] apt update + base packages"
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg git jq python3 python3-venv python3-pip \
    python3-jwt tini uuid-runtime

echo "[2/12] create sgagent user"
if ! id sgagent >/dev/null 2>&1; then
    useradd --system --create-home --shell /bin/bash sgagent
fi

echo "[3/12] install gh CLI"
if ! command -v gh >/dev/null 2>&1; then
    curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list
    apt-get update
    apt-get install -y gh
fi

echo "[4/12] install Claude Code CLI (Anthropic)"
if ! command -v claude-code >/dev/null 2>&1; then
    apt-get install -y nodejs npm
    npm install -g @anthropic-ai/claude-code || true
    # Anthropic ships the binary as `claude` on Linux; orchestrator invokes it
    # as `claude-code`, so provide a symlink for both names.
    if [ -x /usr/local/bin/claude ] && [ ! -e /usr/local/bin/claude-code ]; then
        ln -sf /usr/local/bin/claude /usr/local/bin/claude-code
    fi
fi

echo "[5/12] install uv (needed for FCC's Python 3.14 pin)"
if ! sudo -u sgagent test -x /home/sgagent/.local/bin/uv; then
    sudo -u sgagent bash -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi

echo "[6/12] install free-claude-code (native Python, not Docker)"
FCC_SRC=/opt/free-claude-code
install -d -o sgagent -g sgagent "$FCC_SRC"
if [[ ! -d "$FCC_SRC/.git" ]]; then
    sudo -u sgagent git clone --depth 1 https://github.com/Alishahryar1/free-claude-code.git "$FCC_SRC"
fi
sudo -u sgagent git -C "$FCC_SRC" config --global --add safe.directory "$FCC_SRC"
sudo -u sgagent bash -c "cd $FCC_SRC && /home/sgagent/.local/bin/uv sync 2>&1 | tail -5"

echo "[7/12] clone the SteamGuard repo into the agent home (via GitHub App token)"
# Mint a short-lived App installation token to bootstrap the initial clone.
# Steady-state operations use per-session tokens minted by the orchestrator.
if [[ ! -d /home/sgagent/SteamGuard/.git ]]; then
    APP_ID=$(awk -F= '/^SG_HEAL_APP_ID=/{print $2}' /etc/sg-devbox/env)
    INSTALL_ID=$(awk -F= '/^SG_HEAL_INSTALLATION_ID=/{print $2}' /etc/sg-devbox/env)
    PEM_B64=$(awk -F= '/^SG_HEAL_PRIVATE_KEY_B64=/{print $2}' /etc/sg-devbox/env)
    if [[ -z "$APP_ID" || -z "$INSTALL_ID" || -z "$PEM_B64" ]]; then
        echo "ERROR: SG_HEAL_* not set in /etc/sg-devbox/env — cannot clone private repo"
        exit 1
    fi
    PEM=$(mktemp)
    echo "$PEM_B64" | base64 -d > "$PEM"
    chmod 600 "$PEM"
    JWT=$(APP_ID="$APP_ID" PEM="$PEM" python3 <<'PY'
import jwt, time, os
now = int(time.time())
with open(os.environ["PEM"]) as f: key = f.read()
print(jwt.encode({"iat": now-60, "exp": now+540, "iss": os.environ["APP_ID"]}, key, algorithm="RS256"))
PY
)
    TOKEN=$(curl -sS -X POST \
        -H "Authorization: Bearer $JWT" \
        -H "Accept: application/vnd.github+json" \
        "https://api.github.com/app/installations/$INSTALL_ID/access_tokens" \
        | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
    rm -f "$PEM"
    sudo -u sgagent bash -c "cd ~ && git clone https://x-access-token:${TOKEN}@github.com/${REPO_SLUG}.git SteamGuard"
    unset TOKEN JWT
fi
sudo -u sgagent bash <<EOF
set -euo pipefail
cd ~/SteamGuard
git remote set-url origin https://github.com/${REPO_SLUG}.git
git config user.name  "rivvak-sg-heal[bot]"
git config user.email "rivvak-sg-heal[bot]@users.noreply.github.com"
git config gpg.format ssh
if [[ -f /etc/sg-devbox/sg-heal-bot.pub ]]; then
    git config user.signingkey /etc/sg-devbox/sg-heal-bot.pub
    git config commit.gpgsign true
fi
mkdir -p /var/lib/sg-devbox/work
EOF
chown -R sgagent:sgagent /var/lib/sg-devbox

echo "[8/12] install repo scripts under /opt/sg-devbox"
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
install -m 0644 -o sgagent -g sgagent \
    "$(dirname "$0")/scripts/create_handler.py" /opt/sg-devbox/create_handler.py

# Python deps for the orchestrator
sudo -u sgagent python3 -m venv /opt/sg-devbox/venv
sudo -u sgagent /opt/sg-devbox/venv/bin/pip install --quiet \
    fastapi uvicorn httpx pyjwt cryptography pathspec

echo "[9/12] install systemd units"
install -m 0644 "$(dirname "$0")/systemd/sg-fcc.service"          /etc/systemd/system/sg-fcc.service
install -m 0644 "$(dirname "$0")/systemd/sg-devbox-orch.service"  /etc/systemd/system/sg-devbox-orch.service
install -m 0644 "$(dirname "$0")/systemd/sg-devbox-cleanup.service" /etc/systemd/system/sg-devbox-cleanup.service
install -m 0644 "$(dirname "$0")/systemd/sg-devbox-cleanup.timer"   /etc/systemd/system/sg-devbox-cleanup.timer

systemctl daemon-reload
systemctl enable --now sg-fcc.service sg-devbox-orch.service sg-devbox-cleanup.timer

echo "[10/12] ensure Docker is installed for /create sandbox"
if ! command -v docker >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends docker.io
fi
if ! id -nG sgagent | tr ' ' '\n' | grep -qx docker; then
    usermod -aG docker sgagent
fi
systemctl enable --now docker

echo "[11/12] build sg-sandbox:latest image for /create"
# Build context includes the sandbox Dockerfile + entrypoint + a snapshot of FCC.
# We snapshot FCC into the context so the image is reproducible even if upstream moves.
SANDBOX_CTX=/var/lib/sg-devbox/sandbox-build
install -d -o sgagent -g sgagent /var/lib/sg-devbox/sessions /var/lib/sg-devbox/artifacts
rm -rf "$SANDBOX_CTX"
install -d -o sgagent -g sgagent "$SANDBOX_CTX"
install -m 0644 -o sgagent -g sgagent \
    "$(dirname "$0")/sandbox/Dockerfile"    "$SANDBOX_CTX/Dockerfile"
install -m 0755 -o sgagent -g sgagent \
    "$(dirname "$0")/sandbox/entrypoint.sh" "$SANDBOX_CTX/entrypoint.sh"
# Copy the already-cloned FCC source into the build context (skip .git + .venv to keep it small).
# tar is always available; --exclude keeps the context lean.
install -d -o sgagent -g sgagent "$SANDBOX_CTX/fcc"
sudo -u sgagent bash -c "cd '$FCC_SRC' && tar --exclude='.git' --exclude='.venv' --exclude='__pycache__' -cf - . | tar -C '$SANDBOX_CTX/fcc' -xf -"
docker build -t sg-sandbox:latest "$SANDBOX_CTX" 2>&1 | tail -20

echo "[12/12] smoke test"
# FCC needs ~30s on first boot to fetch Python 3.14 via uv
for i in {1..12}; do
    sleep 5
    if curl -sf -o /dev/null http://127.0.0.1:8082/v1/models; then
        echo "FCC ready after ${i} attempts"
        break
    fi
done
curl -sf -o /dev/null -w "fcc-models:%{http_code}\n" http://127.0.0.1:8082/v1/models || true
curl -sf http://127.0.0.1:9090/health && echo

echo "bootstrap complete. Tail logs with:  journalctl -u sg-fcc -u sg-devbox-orch -f"

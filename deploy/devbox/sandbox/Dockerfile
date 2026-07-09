# sg-sandbox — one-shot ephemeral build container for /create
#
# Baked-in tools: free-claude-code (FCC) + Anthropic Claude Code CLI + gh + git + zip
# Runtime contract:
#   * Container is launched with `docker run --rm --name sg-create-<sid>` by the orchestrator.
#   * The orchestrator passes NVIDIA_NIM_API_KEY + GITHUB_TOKEN (App install token) + PROMPT
#     via `--env`.
#   * entrypoint.sh starts FCC in the background, waits until :8082/v1/models responds,
#     then pipes PROMPT into `claude -p` with allowedTools including WebSearch/WebFetch
#     and --max-thinking-tokens 32000.
#   * On exit (natural or watchdog-killed), entrypoint.sh zips /workspace into /out/build.zip.
#     The orchestrator mounts /out to a per-session host dir and picks up the artifact.
#
# The image is BUILT ON THE VM (not pushed to a registry) — bootstrap.sh runs `docker build`
# right after installing FCC, using the exact same FCC source it just cloned.

FROM python:3.14-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_MAJOR=22

# ─── system deps ────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl git zip unzip jq gnupg lsb-release \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# ─── gh CLI (for `gh api`, `gh search repos`, `gh repo view`) ───────────────
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
        | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg >/dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
        > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# ─── Node.js 22 + Claude Code CLI ───────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && ln -sf "$(npm root -g)/@anthropic-ai/claude-code/cli.js" /usr/local/bin/claude

# ─── free-claude-code (FCC) — copied in at build time from host clone ───────
# bootstrap.sh copies /tmp/fcc → build context so this stays reproducible.
COPY fcc /opt/fcc
RUN pip install /opt/fcc

# ─── non-root user + workspace ──────────────────────────────────────────────
RUN useradd --create-home --shell /bin/bash --uid 1000 builder \
    && mkdir -p /workspace /out /home/builder/.config/gh \
    && chown -R builder:builder /workspace /out /home/builder

# ─── entrypoint ─────────────────────────────────────────────────────────────
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh

USER builder
WORKDIR /workspace
ENV HOME=/home/builder \
    FCC_HOST=0.0.0.0 \
    FCC_PORT=8082 \
    ANTHROPIC_BASE_URL=http://127.0.0.1:8082 \
    MAX_TURNS=200 \
    MAX_THINKING_TOKENS=32000

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

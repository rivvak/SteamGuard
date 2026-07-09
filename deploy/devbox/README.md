# `sg-devbox` — GCE VM hosting free-claude-code + Claude Code CLI

This directory contains everything the operator needs to stand up the dev-agent VM.

- `bootstrap.sh` — one-shot installer. Copy to the VM as `root`, run once.
- `systemd/sg-fcc.service` — free-claude-code proxy on `127.0.0.1:8787`.
- `systemd/sg-devbox-orch.service` — orchestrator HTTP API on `127.0.0.1:9090`.
- `systemd/sg-devbox-cleanup.timer` — nightly worktree GC.
- `scripts/run-session.sh` — spawn a Claude Code session in a fresh git worktree.
- `scripts/path-guard.py` — enforce allow/deny rules on the resulting diff.
- `scripts/open-pr.sh` — auth as `rivvak-sg-heal` GitHub App, push, open PR.
- `scripts/gc-worktrees.sh` — remove worktrees older than 24 h.

The full deployment runbook (VM create, secret grants, tunnel wiring) is in
[`docs/phase2/RUNBOOK.md`](../../docs/phase2/RUNBOOK.md).

## Environment variables the VM expects

| Var | Source | Notes |
|---|---|---|
| `NVIDIA_NIM_API_KEY` | Secret Manager | mounted by systemd via `EnvironmentFile=/etc/sg-devbox/env` |
| `DEVBOX_TOKEN` | Secret Manager | 32-byte random; SG server presents this as bearer to `:9090` |
| `SG_HEAL_APP_ID` | Secret Manager | GitHub App ID `4249863` |
| `SG_HEAL_INSTALLATION_ID` | Secret Manager | Install ID `145288024` |
| `SG_HEAL_PRIVATE_KEY` | Secret Manager | PEM contents |
| `REPO_SLUG` | env file | `rivvak/SteamGuard` |
| `AI_DEVBOX_MODEL` | env file | default `z-ai/glm-5.2` |

## Ports

| Port | Bind | Purpose |
|---|---|---|
| 8787 | 127.0.0.1 | free-claude-code (Anthropic Messages ↔ NIM) — never public |
| 9090 | 127.0.0.1 | Orchestrator HTTP API — reached via IAP tunnel from Cloud Run only |

## Users

`sg-devbox.service` and `sg-fcc.service` both run as the unprivileged
`sgagent` user. Only `sgagent` has write access to `/var/lib/sg-devbox/`.

# SteamGuard AI — Phase 2 Sub-Plan (dev agent + self-heal)

> **Parent**: [`docs/AI_INTEGRATION_PLAN.md`](../AI_INTEGRATION_PLAN.md) v3.1
>
> **Phase 1 (shipped)**: `/ai/ask` backend, Discord `/ask`, loader `?` chat modal — all live on `main`, deployed to `steamguard` + `sg-litellm` on Cloud Run, RAG ingested to `gs://sg-rag-index`.
>
> **Phase 2 (this doc)**: `sg-devbox` GCE VM running [`free-claude-code`](https://github.com/Alishahryar1/free-claude-code) (Claude Code CLI ↔ NIM proxy). Adds `/develop` slash command and CI self-heal.

---

## 0. Locked decisions

| Key | Value |
|---|---|
| VM | `sg-devbox`, `e2-small` (2 vCPU / 2 GB), `us-central1-a`, Debian 12, 20 GB balanced PD |
| Project | `fabled-mystery-474200-i1` |
| VM service account | `sg-devbox-sa@fabled-mystery-474200-i1.iam.gserviceaccount.com` (new, minimal-scope) |
| FCC container port | `127.0.0.1:8787` (bound to loopback; **never public**) |
| Ingress | Outbound only — SG bot on Cloud Run reaches FCC via **IAP TCP tunnel** on port 8787 |
| Auth to FCC | `Authorization: Bearer $DEVBOX_TOKEN` (32-byte random, stored in Secret Manager as `DEVBOX_TOKEN`) |
| Anthropic proxy | FCC serves Anthropic Messages API on `:8787`, forwards to `NVIDIA_NIM_API_KEY` → `z-ai/glm-5.2` |
| Claude Code CLI | Installed on VM, `ANTHROPIC_BASE_URL=http://127.0.0.1:8787`, `ANTHROPIC_AUTH_TOKEN=<devbox-token>` |
| GitHub App | `rivvak-sg-heal` (App ID `4249863`, Install `145288024`) — used for both `/develop` PRs and CI self-heal commits |
| Bot commit signing | SSH key `sg-heal-bot` on VM, added as a signing key to `rivvak-sg-heal` GitHub App via deploy key |
| Auto-commit-to-main allow-list | `docs/**`, `tests/**`, `**/*.md`, `requirements*.txt`, `pyproject.toml`, `dashboard/**` |
| Never-touch deny-list | `auth/**`, `server/**/license*`, `server/**/entitlement*`, `**/hwid*.py`, `**/cert*.py`, `**/pinning*.py`, `.env*`, `*.pem`, `*.key`, `.github/workflows/**`, `build.bat`, `Dockerfile`, `scripts/build*` |
| `/develop` PR reviewer | Auto-request `@rivvak` |
| Rate limits | `/develop` — 5/hour per user, owner unlimited |
| Session isolation | Each `/develop` invocation runs in a fresh git worktree under `/var/lib/sg-devbox/work/<uuid>/`; cleaned after 24h |

## 1. Architecture

```mermaid
flowchart LR
    subgraph Discord
        DEV["/develop &lt;task&gt;"]
    end
    subgraph CloudRun[Cloud Run]
        BOT[SG Discord bot]
    end
    subgraph GCE[GCE us-central1-a]
        FCC[free-claude-code<br/>:8787 loopback]
        CC[Claude Code CLI]
        GIT[git worktree pool]
        FCC -->|Anthropic Messages| NIM
        CC -->|ANTHROPIC_BASE_URL| FCC
        CC -->|edits| GIT
    end
    subgraph GitHub
        PR[Pull Request]
        GHA[Actions: sg-heal]
    end
    NIM[NIM z-ai/glm-5.2]

    DEV --> BOT
    BOT -->|IAP TCP tunnel :8787| FCC
    BOT -->|invoke session| CC
    GIT -->|git push via GitHub App| PR
    PR -->|auto-request review| RIVVAK[@rivvak]

    GHA -->|test failure| SELFHEAL[Self-heal workflow]
    SELFHEAL -->|IAP tunnel| FCC
    SELFHEAL -->|patch + PR| PR
```

## 2. Repository additions (this PR)

```
deploy/devbox/
├── README.md                    # Human-run bootstrap steps
├── bootstrap.sh                 # One-shot VM install: FCC + Claude Code + gh + guards
├── cloudbuild.yaml              # (future) rebuild FCC image
├── systemd/
│   ├── sg-fcc.service           # free-claude-code proxy
│   └── sg-devbox-cleanup.timer  # gc old worktrees
└── scripts/
    ├── run-session.sh           # SG bot RPC entry: spawn Claude Code in fresh worktree
    ├── path-guard.py            # Deny-list + allow-list check on git diff
    ├── open-pr.sh               # Auth as GitHub App, push branch, open PR w/ reviewer
    └── gc-worktrees.sh          # Prune worktrees > 24h old

ai/guards/
├── __init__.py
├── path_rules.py                # Single source of truth: DENY + ALLOW_DIRECT lists
└── diff_check.py                # Programmatic API used by CI + path-guard.sh

server/bot/cogs/
└── develop_cog.py               # /develop slash command → IAP tunnel → FCC session

.github/workflows/heal/
├── ci-self-heal.yml             # On pytest failure: dispatch patch job to sg-devbox
└── verify-agent-pr.yml          # Runs on rivvak-sg-heal[bot] PRs: enforce path-guard

docs/phase2/
├── README.md                    # ← this file
├── RUNBOOK.md                   # Manual deploy steps (VM create → tunnel → smoke)
└── SECURITY.md                  # Threat model + key rotation
```

## 3. `/develop` command flow

1. User invokes `/develop task:"add a --debug flag to server/main.py that toggles verbose logging"` in guild `1513193335697838181`.
2. `develop_cog.py`:
   - Rate-limits per Discord user (5/hr; owner `1513150836472021074` unlimited).
   - Calls SG server `POST /internal/develop` with `X-Admin-Key`, `{discord_user_id, task, thread_id}`.
3. SG server (Cloud Run):
   - Opens **IAP TCP tunnel** to the local HTTP orchestrator on `sg-devbox:9090` (runtime SA must have `roles/iap.tunnelResourceAccessor`).
   - Calls kickoff `POST /develop` and returns immediately with `{session_id, state:"starting"}`.
   - Bot then polls `GET /develop/status/{sid}` through the same short-lived tunnel path.
   - Follow-ups use `POST /develop/{sid}/message`; history reads from `GET /develop/{sid}/history`.
4. `sg-devbox` orchestrator:
   - Creates a fresh git worktree at `/var/lib/sg-devbox/work/<uuid>/`.
   - Uses free-claude-code with provider-qualified model defaults (for example `nvidia_nim/z-ai/glm-5.2`) and NVIDIA NIM auth flow.
   - Uses model/token fallback for retryable upstream failures (`timeout`, malformed response, `429/5xx`) so long tasks can continue on backup slots.
   - Runs Claude Code in headless mode, then records terminal session result to persisted develop status JSON.
   - When Claude Code finishes, runs `path-guard.py` on the diff.
     - If any file matches DENY → abort, return `refused` to bot.
     - If **all** changed files are inside ALLOW_DIRECT → `git push origin main` directly, return `committed` with commit SHA.
     - Otherwise → `git push origin sg-heal/<uuid>`, `gh pr create --reviewer rivvak`, return `pr_opened` with PR URL.
5. SG bot sends heartbeat updates while polling, then posts terminal result embed (commit URL / PR URL / refused / error / planned).
   - `plan_only` requests return a structured plan without committing changes.
   - Follow-up turns can continue an existing session id for back-and-forth iteration.

## 4. CI self-heal flow

1. `.github/workflows/heal/ci-self-heal.yml` triggers on `workflow_run` completion of the main test workflow (`.github/workflows/tests.yml` etc.) when `conclusion == "failure"`.
2. The workflow:
   - Downloads the failed job logs via `gh run view --log-failed`.
   - Extracts the pytest failure summary.
   - Calls SG server `POST /internal/heal` with `X-Admin-Key`, `{run_id, failure_log, head_sha}`.
3. SG server → IAP tunnel → sg-devbox orchestrator → `run-session.sh` with task: *"Fix the failing tests. Failure log: ..."*
4. Same path-guard logic. Because most self-heal changes will touch `tests/**` or code files, it typically opens a PR rather than committing directly.
5. `verify-agent-pr.yml` runs on every PR from `rivvak-sg-heal[bot]`:
   - Re-runs `ai/guards/diff_check.py` on the PR diff.
   - Fails the check if any DENY path is touched (defense in depth — the VM path-guard is the primary gate, this is a belt-and-suspenders check that runs in GitHub's sandbox).

## 5. Security

See [`docs/phase2/SECURITY.md`](./SECURITY.md) for the full threat model. Key points:

- **FCC is never publicly reachable**. Bound to `127.0.0.1:8787`, VM has `--no-address` (no external IP), all access via IAP tunnel from an authenticated GCP identity.
- **Path-guard has two layers**: the VM script (fast, blocks before push) and the GitHub Actions check (verifies at PR-time, cannot be bypassed by a compromised VM).
- **GitHub App token is short-lived** (1h JWT → installation token). Fetched fresh per session by `open-pr.sh`.
- **`DEVBOX_TOKEN` rotates monthly** via a scheduled Cloud Function (Phase 3 add-on; manual rotation for now).
- **VM has no interactive SSH** except via `gcloud compute ssh --tunnel-through-iap` (documented in RUNBOOK).
- **Never-touch deny-list** protects all license/auth code, cert pins, workflows, build scripts.

## 6. Phased rollout

1. **Land this PR** (code only, no infra changes).
2. Create `sg-devbox-sa` service account, grant minimum roles.
3. Create the VM: `gcloud compute instances create sg-devbox --no-address ...`
4. Run `bootstrap.sh` on the VM (installs FCC, Claude Code, gh, guards, systemd units).
5. Add `DEVBOX_TOKEN`, `SG_DEVBOX_ZONE`, `SG_DEVBOX_NAME` secrets to `steamguard` Cloud Run service.
6. Add `AI_DEVELOP_ENABLED=false` env var (safe default).
7. Deploy `steamguard` + bot.
8. Smoke test via IAP tunnel: `curl` the orchestrator's `/health` from Cloud Run.
9. Flip `AI_DEVELOP_ENABLED=true`.
10. Enable CI self-heal by installing the workflows (they're gated by the same flag via a repo secret `AI_HEAL_ENABLED`).

## 7. Cost estimate

| Resource | Monthly |
|---|---|
| `sg-devbox` e2-small, 24×7 | ~$14 |
| Balanced PD 20 GB | ~$1.60 |
| IAP tunnel egress | negligible |
| NIM tokens (glm-5.2, ~50 sessions/mo × ~30k tokens) | included in existing NIM quota |
| **Total added** | **~$16/mo** |

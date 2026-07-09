# Phase 2 — Security & Threat Model

## Trust boundaries

```
┌──────────────┐   Discord API   ┌──────────────┐
│ Discord user │───────────────▶│ SG bot (CR)  │
└──────────────┘                 └──────┬───────┘
                                        │ X-Admin-Key
                                        ▼
                                 ┌──────────────┐
                                 │ steamguard   │
                                 │ (Cloud Run)  │
                                 └──────┬───────┘
                                        │ gcloud IAP tunnel
                                        │  (SA identity)
                                        ▼
                                 ┌──────────────┐   Anthropic Msgs   ┌───────┐
                                 │ sg-devbox    │───────────────────▶│  FCC  │
                                 │ orchestrator │◀───────────────────│ :8787 │
                                 │  :9090 loop  │                    └───┬───┘
                                 └──────┬───────┘                        │
                                        │ git push                       │ NIM API
                                        │  (App token, 1h)              ▼
                                        ▼                          ┌────────┐
                                 ┌──────────────┐                  │  NIM   │
                                 │  GitHub      │                  └────────┘
                                 └──────────────┘
```

Every arrow that leaves a box requires authentication:

| From → To | Auth |
|---|---|
| User → Discord | Discord OAuth (user's own account) |
| Bot → SG server | `X-Admin-Key` header (constant-time compare) |
| SG server → sg-devbox | IAP tunnel (Cloud Run runtime SA has `roles/iap.tunnelResourceAccessor`) + `Authorization: Bearer $DEVBOX_TOKEN` |
| Orchestrator → FCC | Same VM, loopback only, bearer token |
| Orchestrator → GitHub | Short-lived GitHub App installation token (1h) |
| FCC → NIM | `NVIDIA_NIM_API_KEY` from Secret Manager |

## Threats and mitigations

### 1. Prompt injection into `/develop` task

**Threat.** A user (or hijacked content in a repo file the agent reads) tries to make the agent modify a deny-listed file.

**Mitigation.**
- **Deny-list is a hard filter** in `ai/guards/path_rules.py`. The agent can *ask* for `auth/hwid.py`; the orchestrator will refuse the diff before it ever touches the remote.
- The GitHub Actions `ai-agent-pr-guard` job re-runs the same classification in GitHub's sandbox. Even if the VM were compromised, a PR touching deny-listed paths fails CI.
- CODEOWNERS additionally requires `@rivvak` on every sensitive path, so a compromised repo webhook (or a merge attempt by another bot) still can't land the change.

### 2. Leaked `DEVBOX_TOKEN` or `ADMIN_KEY`

**Threat.** Attacker who obtains one of these can call the orchestrator or the SG server directly.

**Mitigation.**
- `DEVBOX_TOKEN` never leaves Secret Manager / the VM. The Cloud Run service pulls it via `--set-secrets`; the SG server code holds it only in memory.
- `ADMIN_KEY` is already used by the existing bot, so its blast radius is scoped to what the SG server admin routes allow. `/internal/develop` and `/internal/heal` don't accept a Discord user id or run id under the attacker's control — they only proxy to the VM, which still enforces the path-guard.
- IAP tunnel additionally requires a valid GCP identity with `iap.tunnelResourceAccessor` on the VM — pure token theft is not sufficient without stealing a GCP OAuth token as well.

### 3. Compromised NIM key

**Threat.** Anyone with `NVIDIA_NIM_API_KEY` can burn quota.

**Mitigation.**
- The plan doc already flags this key as "compromised, do not rotate per user direction". It is documented in `docs/AI_INTEGRATION_PLAN.md` and only stored in Secret Manager on GCP.
- FCC container doesn't log the key. `PROXY_AUTH_TOKEN=${DEVBOX_TOKEN}` on FCC means an attacker inside the VM still needs the devbox token to make FCC forward to NIM.

### 4. Runaway Claude Code session

**Threat.** Agent gets stuck in a long loop, consumes tokens and CPU.

**Mitigation.**
- `--max-turns 30` in orchestrator's `claude-code` invocation.
- 20-minute wallclock timeout on the subprocess call.
- Rate limit: 5 `/develop` sessions per Discord user per hour (owner exempt).
- CI self-heal is gated by `concurrency` group on `head_sha`, so a rapid burst of retries can't stack sessions.

### 5. Git worktree bloat / disk fill

**Threat.** Worktrees accumulate on the VM, disk fills.

**Mitigation.**
- Each session removes its worktree on exit.
- Nightly `sg-devbox-cleanup.timer` (04:30 UTC) prunes worktrees > 24 h old.
- Disk usage alert should be added (Cloud Monitoring: `disk.utilization > 0.85` on `sg-devbox` → Discord alert via the existing bot).

### 6. Bot-signed commit forgery

**Threat.** Someone with SSH key access forges a commit as `rivvak-sg-heal[bot]`.

**Mitigation.**
- SSH signing key `sg-heal-bot` lives only in `/etc/sg-devbox/sg-heal-bot` on the VM (0600, `sgagent`-owned).
- GitHub App installation token (used for pushes) is minted per session and expires in 1 h.
- All bot PRs require CODEOWNERS review. No "auto-merge" is enabled anywhere in the repo.

## Key rotation

| Secret | Rotation cadence | How |
|---|---|---|
| `DEVBOX_TOKEN` | Monthly | Generate 32-byte random, update Secret Manager, restart `sg-devbox-orch.service` and the `steamguard` Cloud Run service |
| `ADMIN_KEY` | Quarterly | Same, restart bot + SG server |
| `SG_HEAL_PRIVATE_KEY` | On compromise only | Regenerate in GitHub App settings, update Secret Manager |
| `NVIDIA_NIM_API_KEY` | User-declared "do not rotate" | n/a |
| SSH signing key | Yearly | Rotate on VM, update GitHub App signing key list |

## Incident response

If the agent lands a bad change:

1. **Revert immediately**: `git revert <sha> && git push`.
2. **Flip the flag**: `AI_DEVELOP_ENABLED=false` and `AI_HEAL_ENABLED=false` on the SG server. Bot stops dispatching new sessions on next request.
3. **Stop the VM**: `gcloud compute instances stop sg-devbox --zone us-central1-a`. Orchestrator becomes unreachable; SG server returns 504.
4. **Rotate `DEVBOX_TOKEN` and `ADMIN_KEY`** before restarting.
5. **Post-mortem** required for any denied-file bypass.

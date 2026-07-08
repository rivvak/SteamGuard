# AGENTOWNERS — Rules for AI Coding Agents

**Audience:** any autonomous coding agent operating on this repo
(`rivvak-sg-heal` GitHub App, Claude Code via free-claude-code on
`sg-devbox`, or an E2B `/create` sandbox).

You **must** read this file before making changes. These are hard rules —
CODEOWNERS, branch protection, and the VM-side path guard exist to enforce
them, but you should not require enforcement to comply.

## 1. Never modify these paths

- `auth/**` — client-side license/HWID/cert-pinning code
- `server/**/license*`, `server/**/entitlement*` — server-side gating
- `**/hwid*.py`, `**/cert*.py`, `**/pinning*.py`
- `.env*`, `*.pem`, `*.key`, `hashes.txt` — secrets and pinning material
- `.github/workflows/**` — CI itself (you must never grant yourself broader
  permissions by editing your own pipeline)
- `build.bat`, `Dockerfile`, `scripts/build*` — packaging/signing pipeline
- `SECRET_KEY`, `HMAC_SECRET_KEY`, `ADMIN_KEY`, `DISCORD_BOT_TOKEN`,
  `NVIDIA_NIM_API_KEY`, `SG_HEAL_*` — any reference must come from
  Secret Manager at runtime, never checked in

If a task appears to require touching these paths, stop and open an issue
tagged `needs-human-review` describing what you would have changed and why.

## 2. Auto-commit-to-main allow-list

Direct pushes to `main` (bypassing PR review) are allowed **only** when
every changed path is in this list:

- `docs/**`
- `tests/**`
- `**/*.md`
- `requirements*.txt`, `requirements_client.txt`
- `pyproject.toml` (dependency version bumps only — no build-system edits)
- `dashboard/**`

Anything outside → open a PR on `ai/develop/<slug>` or `ai/heal/<sha>`,
auto-request review from `@rivvak`, do not merge yourself.

## 3. Commit hygiene

- Prefix subject with `[ai-heal]` (CI self-heal) or `[ai-develop]`
  (`/develop` command) or `[ai-create]` (`/create` sandbox output).
- Sign with the VM's SSH signing key (already configured in the bot's git
  identity).
- Keep commits atomic — one logical change per commit.
- Never rewrite public history (no force-push to `main`, no rebase of
  merged branches).

## 4. Testing before push

- Run the project's existing test suite (`pytest` on the server, whatever
  the loader uses on the client side).
- If you added functionality, add tests. If you changed behavior, update
  tests. Never delete a test to make CI pass.

## 5. Scope discipline

- Solve the exact ticket / CI failure / `/develop` prompt. Do not
  opportunistically refactor unrelated code, even if it looks improvable.
- If you notice a real bug outside your scope, open an issue — don't fix it
  in the same commit.

# SteamGuard — Agent Notes / Resume State

This file contains the current state of the project so any future session can pick up quickly.

## Project Overview

SteamGuard desktop app with license key system:
- FastAPI backend running on Google Cloud Run (Firestore database)
- Discord bot for key delivery + membership enforcement
- Client-side auth module (HWID, session caching, cert pinning)
- Tkinter preflight screens (TOS + license activation)

## Current State (last updated by Devin session)

- Local repo: `C:\Users\xiq\Downloads\steamapp`
- Branch: `main`
- Remote: `origin https://github.com/rivvak/SteamGuard.git` (token removed from URL after push)
- GitHub repo: exists, private; local code pushed
- Cloud Run deploy: GitHub Actions workflow now succeeds and redeploys on `server/**` changes

## Recent Fixes

1. **GitHub repo created & code pushed**
   - Repo `rivvak/SteamGuard` exists (private).
   - Local main branch merged with remote README and pushed.
   - `.devin/config.json` (contains live GitHub token) is now gitignored.

2. **Cloud Run auto-deploy fixed**
   - Added `workflow_dispatch` to `.github/workflows/deploy.yml`.
   - Granted `github-deployer` service account `roles/artifactregistry.writer`.
   - Latest workflow run succeeded: `https://github.com/rivvak/SteamGuard/actions/runs/27917841278`.

3. **EXE did nothing when launched**
   - Cause: Nuitka build was missing `--enable-plugin=tk-inter`.
   - Fix: added the flag to `build.bat`; UI now opens the activation window.

4. **TOS updated for liability protection**
   - Added: educational-use-only language, broad liability disclaimer,
     indemnification clause, arbitration/class-action waiver, and
     explicit release of liability.
   - Bumped `TOS_VERSION` to `1.1` to force existing users to re-accept.

5. **UI stuck on "connecting to server"**
   - Cause: Cloud Run TLS certificate rotated; `auth/client.py` had old fingerprint.
   - Fix: updated `_SERVER_CERT_HASHES` to include current + previous fingerprints.
   - Also made cert extraction more robust and improved error messages.

6. **Bot + server feature additions**
   - Bot restricted to `#get-key` channel; DMs redirected to server invite.
   - New commands: `!mykey`, `!listkeys`, `!keyinfo`, `!pausekey`, `!revokekey`, `!sgstatus`.
   - Server: `/pause`, `/unpause`, `/pause-by-discord`, `/admin/list-keys`, `/youtube/store-token`.
   - Server `/verify` auto-pauses keys if Discord role lost or YouTube unsub found.

## Live Deployment

- Cloud Run URL: `https://steamguard-775181381055.us-central1.run.app`
- Server status: updated code deployed (verified `/admin/list-keys` returns 401, meaning the endpoint exists)
- Auto-deploy: GitHub Actions workflow succeeds on push to `server/**`.

## Required Environment Variables

### Server / Cloud Run (set in GCP Secret Manager)
- `SECRET_KEY` — HMAC signing key (must match `auth/client.py` `_HMAC_SECRET`)
- `HMAC_SECRET_KEY` — duplicate, wired through for completeness
- `ADMIN_KEY` — admin endpoint auth
- `DISCORD_BOT_TOKEN` — Discord bot token
- `DISCORD_GUILD_ID` — Discord server ID
- `DISCORD_ROLE_ID` — "Member" role ID
- `YOUTUBE_CHANNEL_ID` — (optional) channel ID to check subscriptions
- `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET` — (optional) Google OAuth credentials

### Bot (set where bot runs, e.g. Railway)
- All server secrets above
- `GETKEY_CHANNEL_ID` — Discord channel ID for `#get-key`
- `DISCORD_INVITE` — server invite link
- `ADMIN_USER_IDS` — comma-separated Discord user IDs with admin access
- `LICENSE_SERVER_URL` — Cloud Run URL

## Next Steps (in order)

1. Add `GCP_SA_KEY` GitHub Actions secret (see earlier Devin notes for gcloud commands). [done]
2. Re-run the GitHub Action in `.github/workflows/deploy.yml` to deploy updated server. [done]
3. Rebuild client after TOS update: `cd C:\Users\xiq\Downloads\steamapp && build.bat`
4. Test that `dist\SteamGuard.exe` shows the updated TOS and connects to the server.
5. Set bot env vars `GETKEY_CHANNEL_ID`, `DISCORD_INVITE`, `ADMIN_USER_IDS`.
6. Restart bot.
7. Test end-to-end: `!getkey` → activate in app → `!linkyoutube` → verify YouTube check.

## Important Files

- `auth/client.py` — server URL, HMAC secret, cert fingerprints
- `auth/screens.py` — TOS and activation UI
- `server/main.py` — FastAPI license backend
- `server/bot.py` — Discord bot
- `.github/workflows/deploy.yml` — Cloud Run auto-deploy
- `build.bat` — Nuitka client build

## Conversation History

Full history of the previous and current Devin sessions is saved locally at:
`C:\Users\xiq\AppData\Roaming\devin\cli\summaries\history_9fcdf7d540904170.md`

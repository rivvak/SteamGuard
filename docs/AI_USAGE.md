# SteamGuard Assistant — User Guide

The SteamGuard Assistant is an in-product AI helper. It's available in the
loader (via the **?** button) and in Discord (via `/ask`), free to all users
with an active SteamGuard license.

## What it's good at

- "How do I link my YouTube account?"
- "What's the referral code system?"
- "How do I reset my HWID?"
- "What does the leaderboard track?"
- "Where do I download the latest version?"

Answers are grounded in the official SteamGuard docs. Each response ends
with the doc file(s) it drew from.

## What it can't help with

- Account-specific problems (a paused key, a lost referral, a stuck YouTube
  link). Use `/support` in Discord and the team will pick it up.
- Anything internal — the assistant is not allowed to discuss license
  validation, HWID computation, admin tooling, or cert pinning.

## Limits

- 30 questions per hour per license, across both the loader and Discord.
- Answers are best-effort; if the assistant can't find the topic in the
  docs it'll say so rather than guess.

## Privacy

- Your question, license key, and HWID are HMAC-signed and sent to the
  SteamGuard backend, then forwarded to the LiteLLM proxy that fronts NVIDIA
  NIM. Nothing is stored beyond the per-hour rate-limit counter.
- The assistant never sees your Discord DMs or messages outside of `/ask`.

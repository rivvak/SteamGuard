# Security Policy

## Reporting a vulnerability

Please report security issues privately to the maintainers (open a private
security advisory on GitHub, or DM a maintainer on Discord). Do **not** open a
public issue for anything that could expose users or infrastructure.

Include: what the issue is, how to reproduce it, and the potential impact.
We aim to acknowledge reports within a few days.

## Never commit secrets

- No tokens, keys, passwords, or service-account files in the repo.
- Use environment variables. `.env` is gitignored; only `.env.example`
  (placeholders) is tracked. Copy it to `.env` and fill in real values locally.
- `credentials.json` and `service-account*.json` are gitignored — keep them out.

## If a secret leaks

1. **Rotate it immediately** — assume any committed secret is compromised.
   - Discord tokens: regenerate in the Discord Developer Portal.
   - `SECRET_KEY` / `JWT_SECRET`: generate a new value and update all consumers.
   - GCP service accounts: revoke the key and issue a new one.
2. Update the value in your secret store (GCP Secret Manager / bot host).
3. Purge the secret from git history if it was committed.

## Pre-commit scanning

This repo ships a `.pre-commit-config.yaml` with `gitleaks`. Install and enable
it to catch secrets before they are committed:

```bash
pip install pre-commit
pre-commit install
```

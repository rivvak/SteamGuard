#!/usr/bin/env bash
# Auth as the rivvak-sg-heal GitHub App, push the current branch, open a PR,
# request review from @rivvak, print the PR URL on the last line of stdout.
#
#   ./open-pr.sh <branch> <title> <source>
#
# Environment (from /etc/sg-devbox/env):
#   SG_HEAL_APP_ID            GitHub App id (e.g. 4249863)
#   SG_HEAL_INSTALLATION_ID   Installation id (e.g. 145288024)
#   SG_HEAL_PRIVATE_KEY_B64   Base64 of the App private key PEM
#   REPO_SLUG                 rivvak/SteamGuard

set -euo pipefail

BRANCH="${1:?branch required}"
TITLE="${2:?title required}"
SOURCE="${3:-develop}"

: "${SG_HEAL_APP_ID:?}"
: "${SG_HEAL_INSTALLATION_ID:?}"
: "${SG_HEAL_PRIVATE_KEY_B64:?}"
: "${REPO_SLUG:?}"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

PEM="$WORK/app.pem"
echo "$SG_HEAL_PRIVATE_KEY_B64" | base64 -d > "$PEM"
chmod 600 "$PEM"

# 1) Build a JWT signed with the App private key
NOW=$(date +%s)
IAT=$((NOW - 30))
EXP=$((NOW + 540))
HEADER=$(printf '{"alg":"RS256","typ":"JWT"}' | openssl base64 -A | tr '+/' '-_' | tr -d '=')
PAYLOAD=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$IAT" "$EXP" "$SG_HEAL_APP_ID" \
    | openssl base64 -A | tr '+/' '-_' | tr -d '=')
SIG=$(printf '%s' "$HEADER.$PAYLOAD" \
    | openssl dgst -sha256 -sign "$PEM" \
    | openssl base64 -A | tr '+/' '-_' | tr -d '=')
JWT="$HEADER.$PAYLOAD.$SIG"

# 2) Exchange for an installation token (valid ~1h)
TOKEN=$(curl -sSf -X POST \
    -H "Authorization: Bearer $JWT" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/app/installations/$SG_HEAL_INSTALLATION_ID/access_tokens" \
    | jq -r .token)

if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
    echo "open-pr: failed to obtain installation token" >&2
    exit 1
fi

# 3) Push using the token as HTTPS creds
PUSH_URL="https://x-access-token:${TOKEN}@github.com/${REPO_SLUG}.git"
git push "$PUSH_URL" "HEAD:refs/heads/${BRANCH}" -f

# 4) Open PR via gh (using the installation token)
export GH_TOKEN="$TOKEN"
BODY_FILE="$WORK/body.md"
cat > "$BODY_FILE" <<EOF
Automated ${SOURCE} PR from **rivvak-sg-heal[bot]**.

Session branch: \`${BRANCH}\`

- Path-guard on sg-devbox classified this diff as **PR-required** (files
  outside the direct-commit allow-list).
- Human review required per CODEOWNERS.

_This PR was opened by an autonomous agent. Do not merge without reading the diff._
EOF

PR_URL=$(gh pr create \
    --repo "$REPO_SLUG" \
    --base main \
    --head "$BRANCH" \
    --title "$TITLE" \
    --body-file "$BODY_FILE" \
    --reviewer rivvak 2>&1 || true)

# `gh pr create` sometimes prints the URL on the last line; if it errored on
# reviewer add, retry without the reviewer flag.
if ! echo "$PR_URL" | grep -q '^https://github.com/'; then
    PR_URL=$(gh pr create \
        --repo "$REPO_SLUG" \
        --base main \
        --head "$BRANCH" \
        --title "$TITLE" \
        --body-file "$BODY_FILE")
fi

# Emit only the URL on the final stdout line (orchestrator parses this)
echo "$PR_URL" | tail -n1

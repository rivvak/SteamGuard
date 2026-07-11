# Phase 2 — Deployment Runbook

Prereqs from Phase 1 that are already in place:
- `steamguard` Cloud Run service is live and has `ADMIN_KEY`, `SECRET_KEY`,
  `HMAC_SECRET_KEY` in Secret Manager.
- `sg-litellm` is live at `https://sg-litellm-775181381055.us-central1.run.app`.
- `sg-heal` GitHub App exists (App ID `4249863`, Install `145288024`).
- `SG_HEAL_APP_ID`, `SG_HEAL_INSTALLATION_ID`, `SG_HEAL_PRIVATE_KEY` are in
  Secret Manager.

Everything below is Phase 2 only.

## 1. Create the VM service account

```bash
PROJECT=fabled-mystery-474200-i1
gcloud iam service-accounts create sg-devbox-sa \
    --display-name "sg-devbox VM identity" \
    --project "$PROJECT"

VM_SA=sg-devbox-sa@${PROJECT}.iam.gserviceaccount.com

# Read-only access to the secrets the VM needs
for S in NVIDIA_NIM_API_KEY DEVBOX_TOKEN SG_HEAL_APP_ID \
         SG_HEAL_INSTALLATION_ID SG_HEAL_PRIVATE_KEY; do
    gcloud secrets add-iam-policy-binding "$S" \
        --member "serviceAccount:${VM_SA}" \
        --role roles/secretmanager.secretAccessor \
        --project "$PROJECT"
done
```

## 2. Create the `DEVBOX_TOKEN`

```bash
python3 -c "import secrets; print(secrets.token_hex(32))" \
    | tr -d '\n' \
    | gcloud secrets create DEVBOX_TOKEN --data-file=- --project "$PROJECT"
```

## 3. Create the VM (no external IP)

```bash
gcloud compute instances create sg-devbox \
    --project "$PROJECT" \
    --zone us-central1-a \
    --machine-type e2-small \
    --image-family debian-12 --image-project debian-cloud \
    --boot-disk-size 20GB --boot-disk-type pd-balanced \
    --no-address \
    --shielded-secure-boot \
    --shielded-vtpm \
    --shielded-integrity-monitoring \
    --service-account "$VM_SA" \
    --scopes cloud-platform \
    --metadata enable-oslogin=TRUE \
    --tags sg-devbox
```

Note the private IP; nothing else needs it.

## 4. Allow IAP tunneling to port 9090

```bash
gcloud compute firewall-rules create sg-devbox-allow-iap \
    --project "$PROJECT" \
    --direction=INGRESS \
    --action=ALLOW \
    --rules=tcp:22,tcp:9090 \
    --source-ranges=35.235.240.0/20 \
    --target-tags=sg-devbox
```

Grant the Cloud Run runtime SA the tunnel access:

```bash
gcloud projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:775181381055-compute@developer.gserviceaccount.com" \
    --role roles/iap.tunnelResourceAccessor
```

(Also grant yourself the same role for interactive SSH.)

## 5. Push secrets to the VM's env file

`bootstrap.sh` expects `/etc/sg-devbox/env` to exist. SSH in via IAP:

```bash
gcloud compute ssh sg-devbox --tunnel-through-iap --zone us-central1-a \
    --project "$PROJECT"
```

On the VM:

```bash
sudo mkdir -p /etc/sg-devbox
sudo tee /etc/sg-devbox/env <<'EOF'
REPO_SLUG=rivvak/SteamGuard
AI_DEVBOX_MODEL=nvidia_nim/z-ai/glm-5.2
EOF

# Pull each secret from Secret Manager and append. The VM's default SA
# has cloud-platform scope, so `gcloud secrets versions access` works.
for S in NVIDIA_NIM_API_KEY DEVBOX_TOKEN SG_HEAL_APP_ID SG_HEAL_INSTALLATION_ID; do
    VAL=$(gcloud secrets versions access latest --secret="$S")
    printf '%s=%s\n' "$S" "$VAL" | sudo tee -a /etc/sg-devbox/env
done

# GitHub App private key: store as base64 (PEM contains newlines that break
# EnvironmentFile parsing).
gcloud secrets versions access latest --secret=SG_HEAL_PRIVATE_KEY \
    | base64 -w0 \
    | (echo -n 'SG_HEAL_PRIVATE_KEY_B64=' && cat) \
    | sudo tee -a /etc/sg-devbox/env

sudo chmod 600 /etc/sg-devbox/env
```

## 6. Generate the bot SSH signing key

On the VM:

```bash
sudo -u sgagent ssh-keygen -t ed25519 -f /etc/sg-devbox/sg-heal-bot -N '' \
    -C "sg-heal-bot signing key"
sudo chmod 600 /etc/sg-devbox/sg-heal-bot
sudo chown sgagent:sgagent /etc/sg-devbox/sg-heal-bot*
```

Take `/etc/sg-devbox/sg-heal-bot.pub` and add it to the `rivvak-sg-heal`
GitHub App as a **signing key** (Settings → Developer settings → GitHub Apps
→ rivvak-sg-heal → SSH signing).

Also add the public key as an **allowed signer** in the repo:

```
# .github/signers (already in repo? if not, add manually)
rivvak-sg-heal[bot] ssh-ed25519 AAAA... sg-heal-bot signing key
```

## 7. Run the bootstrap

Still on the VM:

The SteamGuard repo is private, so the bootstrap needs the GitHub App creds to be
in `/etc/sg-devbox/env` (Step 5). It uses them once to clone `/tmp/sg-bootstrap`;
steady-state runs use per-session tokens.

```bash
# Clone bootstrap via the App token (same pattern bootstrap.sh uses internally)
APP_ID=$(awk -F= '/^SG_HEAL_APP_ID=/{print $2}' /etc/sg-devbox/env)
INSTALL_ID=$(awk -F= '/^SG_HEAL_INSTALLATION_ID=/{print $2}' /etc/sg-devbox/env)
PEM_B64=$(awk -F= '/^SG_HEAL_PRIVATE_KEY_B64=/{print $2}' /etc/sg-devbox/env)
PEM=$(mktemp) && echo "$PEM_B64" | base64 -d > "$PEM" && chmod 600 "$PEM"
JWT=$(APP_ID="$APP_ID" PEM="$PEM" python3 -c 'import jwt,time,os; now=int(time.time()); k=open(os.environ["PEM"]).read(); print(jwt.encode({"iat":now-60,"exp":now+540,"iss":os.environ["APP_ID"]},k,algorithm="RS256"))')
TOKEN=$(curl -sS -X POST -H "Authorization: Bearer $JWT" -H "Accept: application/vnd.github+json" "https://api.github.com/app/installations/$INSTALL_ID/access_tokens" | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
rm -f "$PEM"
sudo git clone https://x-access-token:${TOKEN}@github.com/rivvak/SteamGuard.git /tmp/sg-bootstrap
unset TOKEN JWT

sudo bash /tmp/sg-bootstrap/deploy/devbox/bootstrap.sh
```

FCC needs ~30 s on first boot (uv fetches Python 3.14). The smoke test at the
end prints `fcc-models:200` and a JSON `/health` with `"fcc":"ok"`.

## 8. Wire the SG server for the VM

Back on your laptop:

```bash
gcloud run services update steamguard --region us-central1 \
    --update-env-vars \
DEVBOX_HOST=sg-devbox,\
DEVBOX_ZONE=us-central1-a,\
DEVBOX_PROJECT=fabled-mystery-474200-i1,\
AI_DEVELOP_ENABLED=false,\
AI_HEAL_ENABLED=false \
    --update-secrets DEVBOX_TOKEN=DEVBOX_TOKEN:latest
```

## 9. Add repo secrets + var for CI self-heal

```bash
gh secret set LICENSE_SERVER_URL --repo rivvak/SteamGuard \
    --body "https://rivvak.app"
gh secret set ADMIN_KEY --repo rivvak/SteamGuard \
    --body "$(gcloud secrets versions access latest --secret=ADMIN_KEY --project=fabled-mystery-474200-i1)"
gh variable set AI_HEAL_ENABLED --repo rivvak/SteamGuard --body "false"
```

The workflow `.github/workflows/ai-self-heal.yml` is guarded by
`vars.AI_HEAL_ENABLED == 'true'`, so it's a no-op until you flip the var.

## 10. Smoke test with the flag OFF

```bash
# On the VM
curl -s http://127.0.0.1:9090/health
# → {"ok":true,"fcc":"ok",...}

# From your laptop, tunneled
gcloud compute start-iap-tunnel sg-devbox 9090 \
    --local-host-port=localhost:19090 --zone us-central1-a &
sleep 3
curl -s -H "Authorization: Bearer $DEVBOX_TOKEN" \
    -X POST http://localhost:19090/develop \
    -d '{"task":"echo","source":"develop","initiator":"smoke"}' \
    -H 'Content-Type: application/json'
```

## 11. Flip the flags

Once the smoke test looks clean:

```bash
gcloud run services update steamguard --region us-central1 \
    --update-env-vars AI_DEVELOP_ENABLED=true
# Restart the Discord bot deployment so it registers /develop
gh variable set AI_HEAL_ENABLED --repo rivvak/SteamGuard --body "true"
gcloud run services update steamguard --region us-central1 \
    --update-env-vars AI_HEAL_ENABLED=true
```

Test in Discord:
```
/develop task: add a hello-world sample to docs/samples.md
```

Expect an immediate "starting/running" heartbeat embed, then a terminal result
embed with commit/PR output when the session finishes.

## Rollback

```bash
gcloud run services update steamguard --region us-central1 \
    --update-env-vars AI_DEVELOP_ENABLED=false,AI_HEAL_ENABLED=false
gh variable set AI_HEAL_ENABLED --repo rivvak/SteamGuard --body "false"
gcloud compute instances stop sg-devbox --zone us-central1-a  # hardest cut
```

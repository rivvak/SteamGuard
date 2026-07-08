# sg-litellm — LiteLLM proxy on Cloud Run

Private Cloud Run service that fronts NVIDIA NIM. Only the `steamguard`
service is allowed to invoke it.

## Deploy

Run from repo root. `PROJECT_ID` and `REGION` default to the values in the
integration plan.

```bash
PROJECT_ID=fabled-mystery-474200-i1
REGION=us-central1
SERVICE=sg-litellm
STEAMGUARD_SA=775181381055-compute@developer.gserviceaccount.com

# 1) Build image via Cloud Build (source = this directory)
gcloud builds submit deploy/litellm \
  --tag "${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE}:latest" \
  --project "${PROJECT_ID}"

# 2) Generate a master key locally, store as a Secret Manager secret
python -c "import secrets; print('sk-' + secrets.token_urlsafe(40))" \
  | gcloud secrets create LITELLM_MASTER_KEY --data-file=- --project "${PROJECT_ID}"

# 3) Deploy the service — private ingress, invoker restricted to steamguard SA
gcloud run deploy "${SERVICE}" \
  --image "${REGION}-docker.pkg.dev/${PROJECT_ID}/cloud-run-source-deploy/${SERVICE}:latest" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --ingress internal \
  --no-allow-unauthenticated \
  --min-instances 0 \
  --max-instances 3 \
  --cpu 1 --memory 512Mi \
  --set-secrets "NVIDIA_NIM_API_KEY=NVIDIA_NIM_API_KEY:latest,LITELLM_MASTER_KEY=LITELLM_MASTER_KEY:latest"

# 4) Allow the steamguard service to invoke sg-litellm
gcloud run services add-iam-policy-binding "${SERVICE}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --member "serviceAccount:${STEAMGUARD_SA}" \
  --role roles/run.invoker
```

## Wire into steamguard

After sg-litellm's URL is known, add these env vars to the existing
`steamguard` service (Cloud Run → steamguard → Edit → Variables & Secrets):

```
LITELLM_URL=https://sg-litellm-<hash>-uc.a.run.app
LITELLM_MASTER_KEY=<from Secret Manager: LITELLM_MASTER_KEY:latest>
RAG_BUCKET=sg-rag-index
RAG_INDEX_VERSION=v1
AI_ASK_ENABLED=false     # flip to true after ingest + smoke test
```

Also grant the steamguard SA `roles/storage.objectAdmin` on the
`sg-rag-index` GCS bucket so Chroma can read its persist directory.

## Verify

Once deployed, from a Cloud Shell in the same project:

```bash
TOKEN=$(gcloud auth print-identity-token \
  --audiences=https://sg-litellm-<hash>-uc.a.run.app)
curl -H "Authorization: Bearer ${TOKEN}" \
  -H "x-litellm-master-key: <master>" \
  https://sg-litellm-<hash>-uc.a.run.app/v1/models
```

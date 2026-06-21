import os
import google.auth
from google.cloud import secretmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx
import uvicorn

app = FastAPI(title="SteamGuard API", version="1.0.0")

# Allow CORS for local client
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_secret(secret_id):
    project_id = os.environ.get("GCP_PROJECT", "fabled-mystery-474200-i1")
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")

@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "steamguard"}

@app.get("/api/steam/status")
async def get_steam_status():
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("https://steamstat.us/api/v1/status", timeout=10)
            data = response.json()
            return {"success": True, "data": data}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Steam API error: {str(e)}")

@app.get("/api/config")
async def get_config():
    return {
        "project_id": os.environ.get("GCP_PROJECT"),
        "region": os.environ.get("GCP_REGION", "us-central1"),
        "service": "steamguard"
    }

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

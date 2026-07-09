FROM python:3.11-slim

WORKDIR /app

# gcloud SDK is required at runtime by server/routes/ai_hooks.py to open IAP
# tunnels to sg-devbox. Installed only when AI_DEVELOP_ENABLED / AI_HEAL_ENABLED
# would be true; keeping it in the base image avoids a cold-start install.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg apt-transport-https ca-certificates \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_requirements.txt .
RUN pip install --no-cache-dir -r bot_requirements.txt

COPY . .

RUN adduser --disabled-password --no-create-home appuser
USER appuser

ENV PORT=8080

CMD ["sh", "-c", "python bot.py & uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1"]

FROM python:3.11-slim

WORKDIR /app

# gcloud SDK is required at runtime by server/routes/ai_hooks.py to open IAP
# tunnels to sg-devbox for /develop and /heal. Keep it in the base image so
# there's no cold-start install.
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg apt-transport-https ca-certificates \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
        > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
        | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

# Install server dependencies
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install bot dependencies
COPY server/bot_requirements.txt ./bot_requirements.txt
RUN pip install --no-cache-dir -r bot_requirements.txt

# Install supervisord for process supervision (bot + API must not die silently)
RUN pip install --no-cache-dir supervisor

# Copy application code
COPY server/ ./server/
COPY server/bot.py ./bot.py
COPY supervisord.conf /app/supervisord.conf

# Copy dashboard static files
COPY dashboard/ ./dashboard/

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["supervisord", "-c", "/app/supervisord.conf"]

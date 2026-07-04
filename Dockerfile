FROM python:3.11-slim

WORKDIR /app

# Install server dependencies
COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install bot dependencies
COPY server/bot_requirements.txt ./bot_requirements.txt
RUN pip install --no-cache-dir -r bot_requirements.txt

# Copy application code
COPY server/ ./server/
COPY server/bot.py ./bot.py

# Copy dashboard static files
COPY dashboard/ ./dashboard/

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", "python bot.py & uvicorn server.main:app --host 0.0.0.0 --port ${PORT}"]

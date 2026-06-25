FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/bot_requirements.txt .
RUN pip install --no-cache-dir -r bot_requirements.txt

COPY server/ ./server/
COPY server/bot.py .
COPY dashboard/ ./dashboard/

ENV PORT=8080
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", "python bot.py & uvicorn server.main:app --host 0.0.0.0 --port ${PORT}"]

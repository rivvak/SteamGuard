FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot_requirements.txt .
RUN pip install --no-cache-dir -r bot_requirements.txt

COPY . .

RUN adduser --disabled-password --no-create-home appuser
USER appuser

ENV PORT=8080

CMD sh -c "python bot.py & uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"

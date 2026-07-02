FROM python:3.12-slim

# Install system dependencies (cups-client for lp command, iproute2 for debugging/network details)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cups-client \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENV PYTHONUNBUFFERED=1
ENV DOCKER_ENV=true
ENV EDGE_DB_PATH=/data/edge_queue.db

# Ensure persistence mount directory exists
RUN mkdir -p /data

CMD ["python", "-m", "app.main"]

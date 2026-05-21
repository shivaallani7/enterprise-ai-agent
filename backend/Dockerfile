FROM python:3.12-slim

WORKDIR /app

# build-essential needed by some compiled Python deps (e.g. cryptography)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# Install deps before copying source — better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/       ./app/
COPY scripts/   ./scripts/
COPY ragas_eval/ ./ragas_eval/

# Run as non-root
RUN adduser --disabled-password --gecos '' appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# 1 worker matches the 1 CPU allocated in containerapps.bicep.
# --loop asyncio avoids uvloop install requirement in the slim image.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "asyncio"]

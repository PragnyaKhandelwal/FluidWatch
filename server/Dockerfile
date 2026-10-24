# Smart IV Drip System - backend (FastAPI + MobileNetV3 fill-level model).
# CPU-only torch wheels via requirements.txt's extra index, so the image
# stays a few hundred MB instead of several GB of CUDA libraries.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# SQLite DB and the latest frame per device live here - mount a volume
# over it to keep them across container restarts (see docker-compose.yml).
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data/frames \
    && chown -R appuser:appuser /app
USER appuser
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status == 200 else 1)"

# Render (and similar hosts) inject $PORT; default to 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm test && npm run build

FROM python:3.12-slim-bookworm AS python-dependencies
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /build
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.12-slim-bookworm AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/var/lib/colabpad/colabpad.db \
    CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000 \
    FORWARDED_ALLOW_IPS=127.0.0.1

RUN groupadd --system --gid 10001 colabpad \
    && useradd --system --uid 10001 --gid colabpad --no-create-home colabpad \
    && mkdir -p /srv/colabpad /var/lib/colabpad \
    && chown colabpad:colabpad /srv/colabpad /var/lib/colabpad
WORKDIR /srv/colabpad
COPY --from=python-dependencies /opt/venv /opt/venv
COPY --chown=colabpad:colabpad app/ ./app/
COPY --chown=colabpad:colabpad deploy/backup.py ./deploy/backup.py
COPY --from=frontend --chown=colabpad:colabpad /build/app/static/ ./app/static/

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import json, urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2))['ok'] is True"]
CMD ["uvicorn", "app.main:asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--no-access-log", "--log-level", "warning", "--timeout-graceful-shutdown", "20"]

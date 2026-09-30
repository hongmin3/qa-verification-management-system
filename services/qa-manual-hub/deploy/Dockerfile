# QA Manual Hub -- images for deploy/docker-compose.yml.
#
# Build context is the service root (services/qa-manual-hub), so both the
# backend and the frontend are visible:
#
#   docker compose -f deploy/docker-compose.yml up -d --build
#
# Two final targets:
#   backend -- FastAPI/uvicorn on :24358, runs `alembic upgrade head` on start
#   web     -- nginx serving the built SPA and proxying /api to backend
#
# LibreOffice is not installed, so doc/xls/ppt preview falls back to
# download-only (same as SKIP_OFFICE_PREVIEW=1 on a native install).

# --- frontend build ---------------------------------------------------------- #
FROM node:22-alpine AS frontend-build
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- backend ----------------------------------------------------------------- #
FROM python:3.12-slim AS backend
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt
COPY backend/ ./
RUN rm -rf tests \
    && useradd --system --uid 10001 --home-dir /app qamh \
    && mkdir -p /srv/qa-manual-hub/storage \
    && chown -R qamh:qamh /srv/qa-manual-hub
USER qamh
EXPOSE 24358
# Only the web container can reach this port (compose `expose`, not `ports`),
# so the proxy headers it sets are the only ones that arrive here.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 24358 --workers 2 --proxy-headers --forwarded-allow-ips '*' --timeout-keep-alive 65"]

# --- web (nginx + SPA) ------------------------------------------------------- #
FROM nginx:1.27-alpine AS web
COPY --from=frontend-build /src/frontend/dist/ /usr/share/nginx/html/

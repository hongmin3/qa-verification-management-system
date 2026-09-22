#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
# 포트·호스트는 여기 적지 않는다. `config.yaml` 의 app.host / app.port 가 단일 원본이고
# `app/serve.py` 가 그 값을 읽어 uvicorn 에 넘긴다.
exec "$PROJECT_DIR/.venv/bin/python" -m app.serve

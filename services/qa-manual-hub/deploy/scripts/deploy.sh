#!/usr/bin/env bash
# QA Manual Hub -- deploy from a development machine to the server.
#
# Run this on the DEV machine (Git Bash / WSL / Linux), not on the server:
#
#   ./deploy/scripts/deploy.sh user@host                  # 단독 배포 (기본)
#   BUILD_MODE=platform ./deploy/scripts/deploy.sh user@host   # /manual-hub 하위 배포
#
# BUILD_MODE 는 프론트엔드를 어느 base path 로 빌드할지 정한다. 서버가 QA 자동화
# 플랫폼의 nginx(qa-platform.conf) 뒤에서 /manual-hub/ 로 서비스되고 있는데
# standalone 으로 배포하면, asset 이 /assets/ 를 가리켜 화면이 빈 채로 뜬다.
# 어느 쪽인지 모르면 서버에서 확인한다:
#   ls /etc/nginx/sites-enabled/   # qa-platform.conf 가 있으면 platform
#
# It builds the frontend locally, rsyncs the backend and the built SPA, runs
# migrations, and restarts the unit.  The server never needs Node.js.
#
# Nothing outside APP_ROOT is touched: no other service is stopped, no other
# database is opened, and the document storage tree is never written by this
# script.

set -Eeuo pipefail

TARGET="${1:-${DEPLOY_TARGET:-}}"
APP_ROOT="${APP_ROOT:-/opt/qa-manual-hub}"
SERVICE="${SERVICE:-qa-manual-hub}"
BUILD_MODE="${BUILD_MODE:-standalone}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[[ -n "$TARGET" ]] || die "배포 대상을 지정하세요: $0 user@host  (또는 DEPLOY_TARGET 환경변수)"
case "$BUILD_MODE" in
    standalone) NPM_BUILD="build";          BASE_PATH="/";            HEALTH_PATH="/api/health" ;;
    platform)   NPM_BUILD="build:platform"; BASE_PATH="/manual-hub/"; HEALTH_PATH="/manual-hub/api/health" ;;
    *) die "BUILD_MODE 는 standalone 또는 platform 이어야 합니다 (받은 값: $BUILD_MODE)" ;;
esac
command -v rsync >/dev/null || die "rsync 이 필요합니다."
command -v npm   >/dev/null || die "npm 이 필요합니다 (프론트엔드 빌드용)."

# --------------------------------------------------------------------------- #
log "1/6 프론트엔드 빌드 (BUILD_MODE=$BUILD_MODE, base=$BASE_PATH)"
(cd "$REPO_ROOT/frontend" && npm ci --no-audit --no-fund >/dev/null 2>&1 || npm install --no-audit --no-fund >/dev/null)
(cd "$REPO_ROOT/frontend" && npm run "$NPM_BUILD")
[[ -f "$REPO_ROOT/frontend/dist/index.html" ]] || die "빌드 결과물이 없습니다."
# 잘못된 base 로 빌드된 산출물을 서버에 올리면 화면이 빈 채로 뜨고 원인을 찾기 어렵다.
# 전송 전에 산출물이 실제로 그 base 를 쓰는지 확인한다.
grep -q "\"${BASE_PATH}assets/" "$REPO_ROOT/frontend/dist/index.html"     || die "빌드 산출물의 asset 경로가 base '$BASE_PATH' 와 다릅니다. BUILD_MODE 를 확인하세요."

# --------------------------------------------------------------------------- #
log "2/6 백엔드 전송  ->  $TARGET:$APP_ROOT/app/backend"
rsync -az --delete \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.pytest_cache/' \
    --exclude 'tests/' \
    --exclude '.venv/' \
    "$REPO_ROOT/backend/" "$TARGET:$APP_ROOT/app/backend/"

log "3/6 프론트엔드 전송  ->  $TARGET:$APP_ROOT/app/frontend"
rsync -az --delete "$REPO_ROOT/frontend/dist/" "$TARGET:$APP_ROOT/app/frontend/"

log "4/6 운영 스크립트 전송"
# backup.sh and restore.sh source common.sh from their own folder.
rsync -az "$REPO_ROOT/deploy/scripts/backup.sh" "$REPO_ROOT/deploy/scripts/restore.sh" \
    "$REPO_ROOT/deploy/scripts/common.sh" \
    "$REPO_ROOT/deploy/scripts/qamh" "$TARGET:$APP_ROOT/scripts/"

# Record which commit is live, for the backup manifest and for triage.
REVISION="$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
ssh "$TARGET" "printf '%s\n' '$REVISION' > $APP_ROOT/app/REVISION && chmod +x $APP_ROOT/scripts/*.sh"

# --------------------------------------------------------------------------- #
log "5/6 의존성 동기화 + 마이그레이션"
ssh "$TARGET" "bash -se" <<EOF
set -Eeuo pipefail
cd "$APP_ROOT/app/backend"
"$APP_ROOT/venv/bin/pip" install -q --upgrade pip wheel
"$APP_ROOT/venv/bin/pip" install -q -r requirements.txt
# app/config.py resolves ../../.env from here, so no manual sourcing is needed.
"$APP_ROOT/venv/bin/alembic" upgrade head
EOF

# --------------------------------------------------------------------------- #
log "6/6 서비스 재시작"
ssh "$TARGET" "sudo systemctl restart $SERVICE"

log "헬스체크 ($HEALTH_PATH)"
for i in $(seq 1 15); do
    if ssh "$TARGET" "curl -fsS http://127.0.0.1$HEALTH_PATH" >/dev/null 2>&1; then
        log "정상: $(ssh "$TARGET" "curl -fsS http://127.0.0.1$HEALTH_PATH")"
        log "배포 완료 (revision $REVISION)"
        exit 0
    fi
    sleep 2
done

die "헬스체크 실패. 서버에서 확인하세요: journalctl -u $SERVICE -n 50 --no-pager"

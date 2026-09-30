#!/usr/bin/env bash
# QA Manual Hub -- first-time server installation.
#
# Idempotent: safe to re-run.  It only ever ADDS things.  It never stops,
# reconfigures or removes any service it did not create, never touches an
# existing database, and never rewrites the firewall beyond adding one rule.
#
#   sudo ./install.sh
#
# Environment overrides:
#   APP_ROOT       (default /opt/qa-manual-hub)
#   DATA_ROOT      (default /srv/qa-manual-hub)
#   SERVICE_USER   (default ubuntu)
#   DB_NAME        (default qa_manual_hub)
#   DB_USER        (default qamanual)
#   BACKEND_PORT   (default 9180)
#   SERVER_NAME    nginx server_name, e.g. manual.example.internal
#                  (default: none -- the site answers on the host's bare IP)
#   SKIP_UFW=1     do not add the firewall rule
#   SKIP_NGINX=1   do not install/configure nginx
#   SKIP_OFFICE_PREVIEW=1  do not install LibreOffice (doc/xls/ppt preview
#                          then falls back to download-only, same as today)
#   SKIP_BACKUP_CRON=1     do not create /etc/cron.d/qa-manual-hub-backup
#   RESET_DB_PASSWORD=1    the DB role already exists: set its password to the
#                          .env value (or a new random one when .env is gone).
#                          Without it an existing role's password is never
#                          changed, and install.sh stops if .env has none.

set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/qa-manual-hub}"
DATA_ROOT="${DATA_ROOT:-/srv/qa-manual-hub}"
SERVICE_USER="${SERVICE_USER:-ubuntu}"
DB_NAME="${DB_NAME:-qa_manual_hub}"
DB_USER="${DB_USER:-qamanual}"
BACKEND_PORT="${BACKEND_PORT:-9180}"
# Empty by default: `server_name  _;` alone still serves the host's bare IP.
SERVER_NAME_EXPLICIT="${SERVER_NAME:+1}"
SERVER_NAME="${SERVER_NAME:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

log()  { printf '\033[1;34m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "root 권한이 필요합니다: sudo $0"
id "$SERVICE_USER" >/dev/null 2>&1 || die "서비스 사용자가 없습니다: $SERVICE_USER"

# --------------------------------------------------------------------------- #
# 0. pre-flight: refuse to collide with anything already running
# --------------------------------------------------------------------------- #
log "사전 점검"
if ss -tulpn 2>/dev/null | grep -qE "[^0-9]${BACKEND_PORT}[[:space:]]"; then
    die "포트 ${BACKEND_PORT} 가 이미 사용 중입니다. BACKEND_PORT 를 바꿔 다시 실행하세요."
fi
command -v psql >/dev/null || die "psql 이 없습니다. PostgreSQL 클라이언트를 먼저 설치하세요."
sudo -u postgres psql -Atc 'SELECT 1' >/dev/null 2>&1 \
    || die "PostgreSQL 에 접속할 수 없습니다. 서비스가 실행 중인지 확인하세요."

# --------------------------------------------------------------------------- #
# 1. packages
# --------------------------------------------------------------------------- #
log "필요 패키지 확인"
NEEDED=()
for pkg in python3-venv python3-pip rsync; do
    dpkg -s "$pkg" >/dev/null 2>&1 || NEEDED+=("$pkg")
done
if [[ "${SKIP_NGINX:-0}" != "1" ]]; then
    dpkg -s nginx >/dev/null 2>&1 || NEEDED+=(nginx)
fi
if [[ "${SKIP_OFFICE_PREVIEW:-0}" != "1" ]]; then
    # Writer/Calc/Impress cover doc(x)/xls(x)/ppt(x); the full `libreoffice`
    # meta-package pulls in far more (Draw, Base, ...) than preview needs.
    for pkg in libreoffice-writer libreoffice-calc libreoffice-impress; do
        dpkg -s "$pkg" >/dev/null 2>&1 || NEEDED+=("$pkg")
    done
fi
if ((${#NEEDED[@]})); then
    log "설치: ${NEEDED[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEEDED[@]}"
else
    log "추가 설치할 패키지 없음"
fi

# --------------------------------------------------------------------------- #
# 2. directories
# --------------------------------------------------------------------------- #
log "디렉터리 생성: $APP_ROOT, $DATA_ROOT"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 755 \
    "$APP_ROOT" "$APP_ROOT/app" "$APP_ROOT/logs" "$APP_ROOT/scripts"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 \
    "$DATA_ROOT" "$DATA_ROOT/storage" "$DATA_ROOT/backup"

# --------------------------------------------------------------------------- #
# 3. database role + database (never touches an existing one)
# --------------------------------------------------------------------------- #
log "데이터베이스 준비: $DB_NAME / role $DB_USER"
DB_PASSWORD=""
ENV_FILE="$APP_ROOT/.env"

role_exists=$(sudo -u postgres psql -Atc \
    "SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER'")
db_exists=$(sudo -u postgres psql -Atc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'")

if [[ -f "$ENV_FILE" ]]; then
    # Re-run: keep the password already in use.
    DB_PASSWORD="$(sed -n 's|^DATABASE_URL=postgresql+psycopg://[^:]*:\([^@]*\)@.*|\1|p' "$ENV_FILE" | head -1)"
fi
env_has_password=0
[[ -n "$DB_PASSWORD" ]] && env_has_password=1

# An existing role's password is only changed when you ask for it with
# RESET_DB_PASSWORD=1 (for example after losing .env).  Otherwise install.sh
# only adds things: it never changes a password something else may be using.
plan="$(qamh_role_password_plan "${role_exists:-0}" "$env_has_password" "${RESET_DB_PASSWORD:-0}")"
if [[ "$plan" == "create" && -z "$DB_PASSWORD" ]] || [[ "$plan" == "reset" && -z "$DB_PASSWORD" ]]; then
    DB_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
fi

case "$plan" in
    create)
        sudo -u postgres psql -qc \
            "CREATE ROLE \"$DB_USER\" WITH LOGIN PASSWORD '$DB_PASSWORD'"
        log "role $DB_USER 생성"
        ;;
    keep)
        log "role $DB_USER 이 이미 있습니다. 비밀번호는 바꾸지 않습니다 (.env 값을 그대로 씁니다)."
        ;;
    reset)
        warn "RESET_DB_PASSWORD=1 -- role $DB_USER 의 비밀번호를 .env 값으로 바꿉니다."
        sudo -u postgres psql -qc \
            "ALTER ROLE \"$DB_USER\" WITH LOGIN PASSWORD '$DB_PASSWORD'"
        ;;
    refuse)
        die "role $DB_USER 이 이미 있는데 $ENV_FILE 에 비밀번호가 없습니다. 기존 비밀번호를 바꾸지 않으려고 멈춥니다. 그 role 을 이 서비스만 쓴다면 RESET_DB_PASSWORD=1 을 붙여 다시 실행하세요."
        ;;
    *)
        die "알 수 없는 role 처리 방식: $plan"
        ;;
esac

if [[ "$db_exists" != "1" ]]; then
    sudo -u postgres createdb -O "$DB_USER" -E UTF8 "$DB_NAME"
    log "database $DB_NAME 생성"
else
    warn "database $DB_NAME 이 이미 있습니다. 그대로 사용합니다 (초기화하지 않음)."
fi
sudo -u postgres psql -qd "$DB_NAME" -c \
    "GRANT ALL ON SCHEMA public TO \"$DB_USER\""

# --------------------------------------------------------------------------- #
# 4. .env
# --------------------------------------------------------------------------- #
if [[ -f "$ENV_FILE" ]]; then
    log ".env 이 이미 있습니다. 유지합니다."
else
    log ".env 생성"
    sed \
        -e "s|^DATABASE_URL=.*|DATABASE_URL=postgresql+psycopg://$DB_USER:$DB_PASSWORD@127.0.0.1:5432/$DB_NAME|" \
        -e "s|^STORAGE_ROOT=.*|STORAGE_ROOT=$DATA_ROOT/storage|" \
        "$REPO_ROOT/deploy/.env.example" > "$ENV_FILE"
fi
chown "$SERVICE_USER:$SERVICE_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

# --------------------------------------------------------------------------- #
# 5. python virtualenv
# --------------------------------------------------------------------------- #
if [[ ! -x "$APP_ROOT/venv/bin/python" ]]; then
    log "virtualenv 생성"
    sudo -u "$SERVICE_USER" python3 -m venv "$APP_ROOT/venv"
fi
log "python 의존성 설치"
sudo -u "$SERVICE_USER" "$APP_ROOT/venv/bin/pip" install -q --upgrade pip wheel
sudo -u "$SERVICE_USER" "$APP_ROOT/venv/bin/pip" install -q \
    -r "$REPO_ROOT/backend/requirements.txt"

# --------------------------------------------------------------------------- #
# 6. systemd unit
# --------------------------------------------------------------------------- #
log "systemd 유닛 설치"
UNIT=/etc/systemd/system/qa-manual-hub.service
sed \
    -e "s|/opt/qa-manual-hub|$APP_ROOT|g" \
    -e "s|/srv/qa-manual-hub|$DATA_ROOT|g" \
    -e "s|--port 9180|--port $BACKEND_PORT|" \
    -e "s|^User=.*|User=$SERVICE_USER|" \
    -e "s|^Group=.*|Group=$SERVICE_USER|" \
    "$REPO_ROOT/deploy/systemd/qa-manual-hub.service" > "$UNIT"
systemctl daemon-reload
systemctl enable qa-manual-hub.service >/dev/null

# --------------------------------------------------------------------------- #
# 7. nginx
# --------------------------------------------------------------------------- #
if [[ "${SKIP_NGINX:-0}" == "1" ]]; then
    warn "SKIP_NGINX=1 -- nginx 설정을 건너뜁니다."
else
    log "nginx 사이트 설치 (server_name: $SERVER_NAME)"
    SITE=/etc/nginx/sites-available/qa-manual-hub.conf
    # Re-run: keep the hostname already configured unless SERVER_NAME was given
    # explicitly, so a later install.sh never silently drops the DNS name.
    if [[ -f "$SITE" && -z "${SERVER_NAME_EXPLICIT:-}" ]]; then
        existing=$(sed -n 's|^[[:space:]]*server_name[[:space:]]\+\(.*\)[[:space:]]*_;|\1|p' \
                   "$SITE" | head -1 | xargs || true)
        if [[ -n "$existing" && "$existing" != "__SERVER_NAME__" ]]; then
            SERVER_NAME="$existing"
            log "기존 server_name 을 유지합니다: $SERVER_NAME"
        fi
    fi
    sed \
        -e "s|/opt/qa-manual-hub|$APP_ROOT|g" \
        -e "s|127.0.0.1:9180|127.0.0.1:$BACKEND_PORT|" \
        -e "s|__SERVER_NAME__|$SERVER_NAME|" \
        "$REPO_ROOT/deploy/nginx/qa-manual-hub.conf" > "$SITE"
    ln -sfn "$SITE" /etc/nginx/sites-enabled/qa-manual-hub.conf

    # The stock default site would otherwise answer for the bare IP.
    if [[ -e /etc/nginx/sites-enabled/default ]]; then
        warn "기존 nginx default 사이트를 비활성화합니다 (파일은 보존)."
        rm -f /etc/nginx/sites-enabled/default
    fi

    # nginx needs +x on each directory down to the SPA files.
    chmod o+x "$APP_ROOT" "$APP_ROOT/app" 2>/dev/null || true

    nginx -t || die "nginx 설정 검증 실패. 위 오류를 확인하세요."
    systemctl enable nginx >/dev/null
    systemctl reload nginx 2>/dev/null || systemctl start nginx
fi

# --------------------------------------------------------------------------- #
# 8. firewall (single additive rule)
# --------------------------------------------------------------------------- #
if [[ "${SKIP_UFW:-0}" == "1" ]]; then
    warn "SKIP_UFW=1 -- 방화벽 규칙을 추가하지 않습니다."
elif command -v ufw >/dev/null && ufw status | head -1 | grep -q active; then
    if ufw status | grep -q '^80/tcp'; then
        log "UFW 80/tcp 규칙이 이미 있습니다."
    else
        log "UFW 80/tcp 허용 추가"
        ufw allow 80/tcp comment 'QA Manual Hub Web' >/dev/null
    fi
fi

# --------------------------------------------------------------------------- #
# 9. daily backup (cron)
# --------------------------------------------------------------------------- #
CRON_FILE=/etc/cron.d/qa-manual-hub-backup
if [[ "${SKIP_BACKUP_CRON:-0}" == "1" ]]; then
    warn "SKIP_BACKUP_CRON=1 -- 자동 백업(cron)을 만들지 않습니다."
elif [[ ! -d /etc/cron.d ]]; then
    warn "/etc/cron.d 가 없습니다. 자동 백업을 만들지 못했습니다 (cron 설치 필요)."
else
    case "$(qamh_write_backup_cron "$CRON_FILE" "$SERVICE_USER" "$APP_ROOT" "$DATA_ROOT")" in
        created) log "자동 백업 등록: $CRON_FILE (매일 02:30)" ;;
        exists)  log "자동 백업 파일이 이미 있습니다. 그대로 둡니다: $CRON_FILE" ;;
    esac
fi

# --------------------------------------------------------------------------- #
log "설치 완료"
cat <<EOF

다음 단계:
  1) 코드/프론트엔드 배포 :  deploy/scripts/deploy.sh (개발 PC에서 실행)
                            -- deploy.sh 가 마이그레이션까지 함께 수행합니다.
  2) 최초 관리자 생성      :  sudo -u $SERVICE_USER $APP_ROOT/scripts/qamh bootstrap-admin
  3) 기본 분류/제품 시드   :  sudo -u $SERVICE_USER $APP_ROOT/scripts/qamh seed-catalog --product "Bellalun Viewer"
  4) 서비스 시작          :  sudo systemctl start qa-manual-hub
  5) 상태 확인            :  systemctl status qa-manual-hub --no-pager && curl -s localhost/api/health

경로:
  APP_ROOT   $APP_ROOT
  DATA_ROOT  $DATA_ROOT
  DB         $DB_NAME (role: $DB_USER)
  PORT       127.0.0.1:$BACKEND_PORT  (외부는 nginx 80만 노출)

오피스 문서(doc/xls/ppt) 미리보기: $(command -v soffice >/dev/null && echo "사용 가능 (LibreOffice 설치됨)" || echo "사용 불가 -- 다운로드로만 확인 가능")
EOF

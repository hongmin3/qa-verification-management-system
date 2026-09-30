#!/usr/bin/env bash
# QA Manual Hub -- restore from a backup directory.
#
#   sudo ./restore.sh /srv/qa-manual-hub/backup/20260827-023000
#
# This script OVERWRITES the live database and document storage.  It therefore:
#   * prints exactly what it will replace,
#   * requires you to type RESTORE to continue (or pass --yes),
#   * checks every file against the SHA-256 lines in the backup's manifest.txt
#     and refuses a folder with a missing manifest or a mismatching file,
#   * takes a safety backup of the current state first and stops if that fails,
#   * stops the service before, and starts it after.
#
# Nothing runs until you confirm.  The document storage is the folder the
# application uses: STORAGE_ROOT from .env, or $DATA_ROOT/storage.

set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/qa-manual-hub}"
DATA_ROOT="${DATA_ROOT:-/srv/qa-manual-hub}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env}"
SERVICE="${SERVICE:-qa-manual-hub}"

log()  { printf '\033[1;34m[restore]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -r "$SCRIPT_DIR/common.sh" ]] || die "common.sh 가 없습니다: $SCRIPT_DIR/common.sh (deploy.sh 로 다시 배포하세요)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

SOURCE="${1:-}"
ASSUME_YES=0
[[ "${2:-}" == "--yes" || "${1:-}" == "--yes" ]] && ASSUME_YES=1
[[ "$SOURCE" == "--yes" ]] && SOURCE="${2:-}"

[[ $EUID -eq 0 ]] || die "root 권한이 필요합니다: sudo $0 <backup_dir>"
[[ -n "$SOURCE" ]] || die "사용법: $0 <backup_dir> [--yes]"
[[ -d "$SOURCE" ]] || die "백업 디렉터리가 없습니다: $SOURCE"
[[ -f "$SOURCE/database.dump" ]] || die "database.dump 가 없습니다: $SOURCE"
[[ -r "$ENV_FILE" ]] || die ".env 를 읽을 수 없습니다: $ENV_FILE"

DB_URL="$(qamh_env_value "$ENV_FILE" DATABASE_URL)"
[[ -n "$DB_URL" ]] || die "DATABASE_URL 을 찾을 수 없습니다."
qamh_parse_db_url "$DB_URL"
STORAGE_ROOT="$(qamh_storage_root "$ENV_FILE" "$DATA_ROOT")"

# Before anything is shown or asked: is this backup whole and one set?
log "백업 manifest 의 SHA-256 확인"
problem="$(qamh_verify_manifest "$SOURCE")" || die "${problem}  복원을 중단합니다. (데이터는 바뀌지 않았습니다)"

echo
cat <<EOF
==========================================================
  복원 대상 확인
----------------------------------------------------------
  백업 소스   : $SOURCE
  $( [[ -f "$SOURCE/manifest.txt" ]] && grep -E '^(backup_at|storage_file_count)' "$SOURCE/manifest.txt" | sed 's/^/  /' )

  덮어쓸 데이터베이스 : $DB_NAME @ $DB_HOST:$DB_PORT
  덮어쓸 저장소       : $STORAGE_ROOT
                        (현재 파일 $(find "$STORAGE_ROOT" -type f 2>/dev/null | wc -l)개)

  이 작업은 현재 데이터를 대체합니다.
  진행 전 현재 상태를 $DATA_ROOT/backup/pre-restore-* 에 백업합니다.
==========================================================
EOF

if (( ! ASSUME_YES )); then
    read -rp "계속하려면 RESTORE 를 입력하세요: " answer
    [[ "$answer" == "RESTORE" ]] || die "취소되었습니다."
fi

# --- 0. safety backup of the CURRENT state --------------------------------- #
SAFETY="$DATA_ROOT/backup/pre-restore-$(date '+%Y%m%d-%H%M%S')"
log "현재 상태 안전 백업: $SAFETY"
# Without a safety backup there is no way back from a wrong restore, so any
# failure here stops the script before the service or the data is touched.
qamh_safety_backup "$SAFETY" "$DB_NAME" "$STORAGE_ROOT" "$APP_ROOT" \
    || die "안전 백업에 실패해 복원을 중단합니다. 서비스와 데이터는 그대로입니다. ($SAFETY)"
qamh_verify_manifest "$SAFETY" >/dev/null || die "안전 백업의 SHA-256 확인에 실패해 복원을 중단합니다. ($SAFETY)"
# This script runs as root, so the safety backup would otherwise be root-owned
# and the service account could not prune it afterwards.
chown -R "$(stat -c '%U:%G' "$DATA_ROOT")" "$SAFETY"

# --- 1. stop the service --------------------------------------------------- #
log "서비스 정지: $SERVICE"
systemctl stop "$SERVICE" || warn "$SERVICE 가 실행 중이 아니었습니다."

# --- 2. database ----------------------------------------------------------- #
log "데이터베이스 복원 (public 스키마 재생성)"
PGPASSWORD="$DB_PASS" psql --host="$DB_HOST" --port="$DB_PORT" \
    --username="$DB_USER" --dbname="$DB_NAME" -q \
    -c 'DROP SCHEMA IF EXISTS public CASCADE' \
    -c 'CREATE SCHEMA public' \
    || die "스키마 재생성 실패"

PGPASSWORD="$DB_PASS" pg_restore --host="$DB_HOST" --port="$DB_PORT" \
    --username="$DB_USER" --dbname="$DB_NAME" \
    --no-owner --no-privileges --exit-on-error \
    "$SOURCE/database.dump" \
    || die "pg_restore 실패. 안전 백업으로 되돌리세요: $SAFETY"
unset PGPASSWORD
log "데이터베이스 복원 완료"

# --- 3. storage ------------------------------------------------------------ #
if [[ -f "$SOURCE/storage.tar.gz" ]]; then
    log "문서 저장소 복원 ($STORAGE_ROOT)"
    replaced="$(qamh_swap_storage "$SOURCE/storage.tar.gz" "$STORAGE_ROOT" "$(date '+%Y%m%d-%H%M%S')")" \
        || die "저장소 복원 실패. 안전 백업으로 되돌리세요: $SAFETY"
    chown -R "$(stat -c '%U:%G' "$DATA_ROOT")" "$STORAGE_ROOT"
    chmod 750 "$STORAGE_ROOT"
    log "저장소 복원 완료${replaced:+ (기존 폴더는 $replaced 로 보존)}"
else
    warn "storage.tar.gz 가 없습니다. 파일은 복원하지 않았습니다."
fi

# --- 4. schema version + restart ------------------------------------------- #
# The dump may predate the current code, so bring the schema forward.  Both this
# and the integrity check below need the deployment environment, which is why
# they go through `sudo -u ... env DATABASE_URL=... STORAGE_ROOT=...` rather than
# relying on the caller's shell.
SERVICE_USER="$(stat -c '%U' "$APP_ROOT")"
BACKEND_DIR="$APP_ROOT/app/backend"
STORAGE_ROOT_VALUE="$STORAGE_ROOT"

run_as_service() {
    sudo -u "$SERVICE_USER" env \
        DATABASE_URL="$DB_URL" \
        STORAGE_ROOT="$STORAGE_ROOT_VALUE" \
        "$@"
}

log "마이그레이션 상태 확인"
if [[ -f "$BACKEND_DIR/alembic.ini" ]]; then
    (cd "$BACKEND_DIR" && run_as_service "$APP_ROOT/venv/bin/alembic" upgrade head) \
        || warn "alembic upgrade 실패. 수동으로 확인하세요."
fi

log "서비스 시작"
systemctl start "$SERVICE"
sleep 3
systemctl is-active --quiet "$SERVICE" \
    && log "서비스 정상 동작" \
    || die "서비스가 시작되지 않았습니다: journalctl -u $SERVICE -n 50"

log "파일 무결성 점검 (존재·크기·SHA-256)"
(cd "$BACKEND_DIR" && run_as_service "$APP_ROOT/venv/bin/python" -m app.cli check-storage --verify-sha256) \
    || warn "일부 파일이 없거나 내용이 다릅니다. 위 목록을 확인하세요."

echo
log "복원 완료. 안전 백업 위치: $SAFETY"
log "문제가 없으면 ${STORAGE_ROOT}.replaced-* 와 안전 백업을 정리하세요."

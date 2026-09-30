#!/usr/bin/env bash
# QA Manual Hub -- backup.
#
# Backs up the two things that cannot be rebuilt from the repository:
#   1. the PostgreSQL database  (pg_dump, custom format)
#   2. the document storage tree (tar.gz)
#
# Both land in $BACKUP_ROOT/<YYYYmmdd-HHMMSS>/ together with a manifest that
# holds one SHA-256 line per file.  restore.sh refuses a folder whose files do
# not match those lines, so a database dump is never paired with the wrong file
# set.
#
#   sudo -u ubuntu /opt/qa-manual-hub/scripts/backup.sh
#
# Cron (daily 02:30): install.sh writes /etc/cron.d/qa-manual-hub-backup with
#   30 2 * * *  ubuntu  /opt/qa-manual-hub/scripts/backup.sh >> /opt/qa-manual-hub/logs/backup.log 2>&1
#
# The document storage is the folder the application uses: STORAGE_ROOT from
# .env, or $DATA_ROOT/storage when .env does not set it.

set -Eeuo pipefail

APP_ROOT="${APP_ROOT:-/opt/qa-manual-hub}"
DATA_ROOT="${DATA_ROOT:-/srv/qa-manual-hub}"
BACKUP_ROOT="${BACKUP_ROOT:-$DATA_ROOT/backup}"
ENV_FILE="${ENV_FILE:-$APP_ROOT/.env}"

# Retention (spec section 47).  0 disables that tier.
KEEP_DAILY="${KEEP_DAILY:-7}"
KEEP_WEEKLY="${KEEP_WEEKLY:-4}"
KEEP_MONTHLY="${KEEP_MONTHLY:-3}"

log() { printf '[backup %s] %s\n' "$(date '+%F %T')" "$*"; }
die() { printf '[backup %s] ERROR: %s\n' "$(date '+%F %T')" "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -r "$SCRIPT_DIR/common.sh" ]] || die "common.sh 가 없습니다: $SCRIPT_DIR/common.sh (deploy.sh 로 다시 배포하세요)"
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

[[ -r "$ENV_FILE" ]] || die ".env 를 읽을 수 없습니다: $ENV_FILE"

# Parse the connection string without echoing it anywhere.
DB_URL="$(qamh_env_value "$ENV_FILE" DATABASE_URL)"
[[ -n "$DB_URL" ]] || die "DATABASE_URL 을 찾을 수 없습니다."
qamh_parse_db_url "$DB_URL"

STORAGE_ROOT="$(qamh_storage_root "$ENV_FILE" "$DATA_ROOT")"

STAMP="$(date '+%Y%m%d-%H%M%S')"
DEST="$BACKUP_ROOT/$STAMP"
mkdir -p "$DEST"

log "대상 디렉터리: $DEST"

# --- 1. database ----------------------------------------------------------- #
log "PostgreSQL 덤프 시작 ($DB_NAME)"
qamh_pg_dump "$DEST/database.dump" || die "pg_dump 실패"
log "덤프 완료: $(du -h "$DEST/database.dump" | cut -f1)"

# --- 2. storage ------------------------------------------------------------ #
if [[ -d "$STORAGE_ROOT" ]]; then
    log "문서 저장소 아카이브 시작 ($STORAGE_ROOT)"
    qamh_archive_storage "$STORAGE_ROOT" "$DEST/storage.tar.gz" \
        || die "storage 아카이브 실패"
    log "아카이브 완료: $(du -h "$DEST/storage.tar.gz" | cut -f1)"
else
    log "경고: $STORAGE_ROOT 가 없습니다. 저장소 백업을 건너뜁니다."
fi

# --- 3. manifest ----------------------------------------------------------- #
qamh_write_manifest "$DEST" "$DB_NAME" "$STORAGE_ROOT" "$APP_ROOT" \
    || die "manifest 작성 실패"
qamh_verify_manifest "$DEST" >/dev/null || die "방금 만든 백업의 SHA-256 확인 실패: $DEST"

chmod -R go-rwx "$DEST"
log "manifest 작성 완료"

# --- 4. retention ---------------------------------------------------------- #
prune() {
    local keep=$1 pattern=$2 label=$3
    (( keep > 0 )) || return 0
    mapfile -t victims < <(
        find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -name "$pattern" \
            | sort -r | tail -n "+$((keep + 1))"
    )
    for dir in "${victims[@]:-}"; do
        [[ -n "$dir" ]] || continue
        log "$label 보존 정책에 따라 삭제: $(basename "$dir")"
        rm -rf -- "$dir"
    done
}

# Daily tier only; weekly/monthly promotion is a documented manual step so the
# script never deletes something a human meant to keep.
prune "$KEEP_DAILY" '20*-*' 'daily'

log "완료. 총 백업 용량: $(du -sh "$BACKUP_ROOT" 2>/dev/null | cut -f1)"

# shellcheck shell=bash
# QA Manual Hub -- helpers shared by install.sh, backup.sh and restore.sh.
#
# Source it; do not run it.  Every function here only reads its arguments and
# the DB_* variables set by qamh_parse_db_url, so the tests in
# backend/tests/test_deploy_scripts.py can call each one on its own with stub
# commands on PATH.  deploy.sh copies this file next to backup.sh/restore.sh.

# qamh_env_value <env_file> <KEY>
#   Print KEY's value from a .env file (surrounding quotes and CR removed), or
#   nothing when the key is absent.
qamh_env_value() {
    local file=$1 key=$2 line
    line="$(grep -m1 "^${key}=" "$file" 2>/dev/null || true)"
    [[ -n "$line" ]] || return 0
    line="${line#*=}"
    line="${line%$'\r'}"
    if [[ "$line" == \"*\" && ${#line} -ge 2 ]]; then
        line="${line:1:${#line}-2}"
    elif [[ "$line" == \'*\' && ${#line} -ge 2 ]]; then
        line="${line:1:${#line}-2}"
    fi
    printf '%s' "$line"
}

# qamh_storage_root <env_file> <data_root>
#   The document storage the application actually uses: STORAGE_ROOT from .env,
#   or <data_root>/storage when .env does not set it.
qamh_storage_root() {
    local value
    value="$(qamh_env_value "$1" STORAGE_ROOT)"
    while [[ "$value" == */ && "$value" != "/" ]]; do value="${value%/}"; done
    printf '%s' "${value:-$2/storage}"
}

# qamh_parse_db_url <url>
#   postgresql+psycopg://user:pass@host:port/dbname?opts -> DB_USER DB_PASS
#   DB_HOST DB_PORT DB_NAME.  Never prints anything.
qamh_parse_db_url() {
    local url=$1 rest credentials hostpath hostport
    rest="${url#*://}"
    credentials="${rest%@*}"
    hostpath="${rest##*@}"
    DB_USER="${credentials%%:*}"
    DB_PASS="${credentials#*:}"
    hostport="${hostpath%%/*}"
    DB_NAME="${hostpath##*/}"
    DB_NAME="${DB_NAME%%\?*}"
    DB_HOST="${hostport%%:*}"
    DB_PORT="${hostport##*:}"
    [[ "$DB_PORT" == "$DB_HOST" ]] && DB_PORT=5432
    return 0
}

# qamh_pg_dump <file>
#   Dump the database named by the DB_* variables in custom format.
qamh_pg_dump() {
    PGPASSWORD="$DB_PASS" pg_dump \
        --host="$DB_HOST" --port="$DB_PORT" --username="$DB_USER" \
        --dbname="$DB_NAME" \
        --format=custom --compress=6 --no-owner --no-privileges \
        --file="$1"
}

# qamh_archive_storage <storage_root> <tarball>
#   tar.gz the storage folder.  The archive holds one top-level folder named
#   like the storage folder itself (usually "storage").
qamh_archive_storage() {
    local root=$1 out=$2
    tar -czf "$out" -C "$(dirname "$root")" "$(basename "$root")"
}

# qamh_write_manifest <dest> <db_name> <storage_root> <app_root>
#   manifest.txt next to the dump: what was backed up, plus one SHA-256 line per
#   artefact.  restore.sh refuses a folder whose files do not match these lines.
qamh_write_manifest() {
    local dest=$1 db_name=$2 storage_root=$3 app_root=$4
    {
        echo "backup_at=$(date -Iseconds)"
        echo "hostname=$(hostname)"
        echo "database=$db_name"
        echo "storage_root=$storage_root"
        echo "storage_file_count=$(find "$storage_root" -type f 2>/dev/null | wc -l | tr -d ' ')"
        echo "app_commit=$(cat "$app_root/app/REVISION" 2>/dev/null || echo unknown)"
        echo "--- sha256 ---"
        (cd "$dest" && for f in ./*; do
            [[ -f "$f" ]] || continue
            case "$f" in ./manifest.txt*) continue ;; esac
            sha256sum "$f"
        done)
    } > "$dest/manifest.txt.tmp" && mv "$dest/manifest.txt.tmp" "$dest/manifest.txt"
}

# qamh_verify_manifest <backup_dir>
#   0 when manifest.txt exists, lists database.dump (and storage.tar.gz if that
#   file is present) and every listed SHA-256 matches.  Otherwise prints why on
#   stdout and returns 1.
qamh_verify_manifest() {
    local dir=$1 manifest="$1/manifest.txt" sums
    if [[ ! -f "$manifest" ]]; then
        echo "manifest.txt 가 없습니다: $dir"
        return 1
    fi
    sums="$(sed -n '/^--- sha256 ---/,$p' "$manifest" | tr -d '\r' \
            | grep -E '^[0-9a-f]{64} [ *]' || true)"
    if [[ -z "$sums" ]]; then
        echo "manifest.txt 에 SHA-256 값이 없습니다: $manifest"
        return 1
    fi
    if ! grep -qE ' [ *]\./database\.dump$' <<<"$sums"; then
        echo "manifest.txt 에 database.dump 의 SHA-256 이 없습니다."
        return 1
    fi
    if [[ -f "$dir/storage.tar.gz" ]] && ! grep -qE ' [ *]\./storage\.tar\.gz$' <<<"$sums"; then
        echo "storage.tar.gz 가 manifest.txt 에 없습니다. 다른 백업의 파일이 섞였을 수 있습니다."
        return 1
    fi
    if ! (cd "$dir" && sha256sum -c --quiet - <<<"$sums"); then
        echo "파일의 SHA-256 이 manifest.txt 와 다릅니다. 손상됐거나 다른 백업의 파일입니다."
        return 1
    fi
    return 0
}

# qamh_safety_backup <dest> <db_name> <storage_root> <app_root>
#   Back up the CURRENT database and storage before a restore overwrites them.
#   Returns non-zero (and leaves the live data untouched) on any failure, so the
#   caller can stop before deleting anything.
qamh_safety_backup() {
    local dest=$1 db_name=$2 storage_root=$3 app_root=$4
    mkdir -p "$dest" || return 1
    if ! qamh_pg_dump "$dest/database.dump"; then
        echo "현재 DB 안전 백업 실패" >&2
        return 1
    fi
    if [[ -d "$storage_root" ]]; then
        if ! qamh_archive_storage "$storage_root" "$dest/storage.tar.gz"; then
            echo "현재 저장소 안전 백업 실패" >&2
            return 1
        fi
    fi
    qamh_write_manifest "$dest" "$db_name" "$storage_root" "$app_root"
}

# qamh_swap_storage <tarball> <storage_root> <stamp>
#   Unpack <tarball> next to <storage_root>, move the current folder aside to
#   <storage_root>.replaced-<stamp>, and put the unpacked folder in its place.
#   The archive must hold exactly one top-level folder (its name may differ from
#   the current storage folder's).  Prints the folder the old data went to.
qamh_swap_storage() {
    local tarball=$1 root=$2 stamp=$3 parent stage extracted count
    parent="$(dirname "$root")"
    mkdir -p "$parent" || return 1
    stage="$(mktemp -d "$parent/.restore-XXXXXX")" || return 1
    if ! tar -xzf "$tarball" -C "$stage"; then
        echo "storage 압축 해제 실패" >&2
        rm -rf -- "$stage"
        return 1
    fi
    count="$(find "$stage" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')"
    extracted="$(find "$stage" -mindepth 1 -maxdepth 1 -type d | head -1)"
    if [[ "$count" != "1" || -z "$extracted" ]]; then
        echo "storage.tar.gz 에는 최상위 폴더가 하나만 있어야 합니다." >&2
        rm -rf -- "$stage"
        return 1
    fi
    if [[ -e "$root" ]]; then
        mv "$root" "$root.replaced-$stamp" || return 1
        echo "$root.replaced-$stamp"
    fi
    mv "$extracted" "$root" || return 1
    rmdir "$stage"
}

# qamh_write_backup_cron <cron_file> <service_user> <app_root> <data_root>
#   Create the daily 02:30 backup entry.  An existing file is left exactly as
#   it is (someone may have changed the time on purpose).
qamh_write_backup_cron() {
    local file=$1 user=$2 app_root=$3 data_root=$4
    if [[ -e "$file" ]]; then
        echo "exists"
        return 0
    fi
    cat > "$file" <<EOF
# QA Manual Hub -- daily backup (created by install.sh; edit freely, install.sh
# never overwrites this file).
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
APP_ROOT=$app_root
DATA_ROOT=$data_root
30 2 * * *  $user  $app_root/scripts/backup.sh >> $app_root/logs/backup.log 2>&1
EOF
    chmod 644 "$file"
    echo "created"
}

# qamh_role_password_plan <role_exists:0|1> <env_has_password:0|1> <reset:0|1>
#   What install.sh does with the database role's password:
#     create  -- no role yet: create it with the .env (or a new random) password
#     keep    -- role exists and .env has a password: change nothing
#     reset   -- RESET_DB_PASSWORD=1: set the role's password to the .env value
#     refuse  -- role exists but there is no .env password to keep; stop
#               rather than silently change a password something else may use
qamh_role_password_plan() {
    local role_exists=$1 env_has_password=$2 reset=$3
    if [[ "$role_exists" != "1" ]]; then
        echo create
    elif [[ "$reset" == "1" ]]; then
        echo reset
    elif [[ "$env_has_password" == "1" ]]; then
        echo keep
    else
        echo refuse
    fi
}

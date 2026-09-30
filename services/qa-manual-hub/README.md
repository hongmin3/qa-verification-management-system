# QA Manual Hub

> **[QA 검증 관리 시스템](../../README.md)의 하위 서비스입니다.**
> 저장소 위치: `services/qa-manual-hub/` · 플랫폼 경로: `/manual-hub/`
> 이 문서의 저장소 상대 경로(`backend/`, `frontend/`, `deploy/`)는 모두 이 디렉터리 기준입니다.
>
> 핵심 앱(Regression 영향 분석 · 매뉴얼 개정 검증)과 **프로세스도 DB도 공유하지 않는 별도
> 배포 단위**입니다. 연결은 홈 화면의 링크와 nginx 라우팅뿐이며, 이 서비스만 따로(자체
> 호스트·포트) 운영할 수도 있습니다.
>
> | 함께 볼 문서 | |
> |---|---|
> | [문서 지도](../../docs/README.md) | 저장소 전체 문서 안내 |
> | [배포 가이드 §8](../../docs/DEPLOYMENT.md) | 같은 서버 `/manual-hub`에 통합 배포하는 절차 |
> | [공용 아키텍처](../../docs/SHARED_PLATFORM_ARCHITECTURE.md) | 하위 서비스의 경계와 추가 규칙 |

제품 매뉴얼과 기술문서를 한 서버에 모아 **Revision 이력을 삭제 없이 보존**하는
사내 문서관리 시스템(Document Management System).

Git 이 소스코드 커밋 이력을 관리하듯, 문서의 개정 이력을 관리합니다.
새 Revision 을 올려도 기존 파일을 **덮어쓰거나 지우지 않습니다.**

```
Product
 └ Document
    ├ Revision A
    ├ Revision B
    ├ Revision C
    └ Revision D  ← CURRENT
```

20명 내외 팀을 위한 단일 서버 애플리케이션입니다. 마이크로서비스도, 검색
클러스터도 쓰지 않습니다.

---

## 무엇을 해결하는가

제품 문서가 여러 공유폴더에 흩어져 있으면 이런 일이 생깁니다.

- 어느 것이 최신본인지 알 수 없다
- 같은 문서의 여러 Revision 이 여러 경로에 존재한다
- 누가 언제 올렸는지 추적할 수 없다
- 과거 Revision 을 찾을 수 없다
- 제품별 문서 현황을 한눈에 볼 수 없다

QA Manual Hub 는 모든 대상 문서를 **중앙 서버에 실제 복사해 보관**하고,
각 문서마다 어느 버전이 현재 최신본(Current)인지 명시하며, 모든 과거 버전과
업로더·업로드 일시를 영구 보존합니다.

---

## 주요 기능

**문서 / 버전**
- 제품 → 문서 → 버전 → 파일의 4계층 구조. 제품은 화면에서 자유롭게 추가
- Revision / Version 은 **형식을 강제하지 않습니다** — `V1.0.12W1`, `Rev.1.3`,
  `R2`, `2026.07`, `1.1` 을 문서에 적힌 그대로 입력
- 새 버전 업로드 시 **자동으로 Current 지정**
- 과거 Legacy 문서를 뒤늦게 올린 경우 **Set as Current** 로 원하는 버전 복구
- Current 를 바꿔도 다른 버전은 삭제되지 않음
- Revision History 타임라인 (업로더, Revision Date, 개정 내용, 파일 크기, SHA-256)
- 문서 / 버전 Archive(Soft delete)와 Restore. Hard delete 없음

**업로더 자동 기록**
- 업로더 이름을 입력하지 않습니다. 로그인 계정에서 자동 기록
- Login ID 와 표시 이름을 **함께** 저장하고, 표시 이름은 업로드 당시 값을
  스냅샷으로 보존 → 사용자가 개명해도 과거 업로더 추적 가능

**파일**
- 중앙 저장, UUID 기반 경로. 원본 파일명은 DB 메타데이터로 보관
- SHA-256 계산 → 동일 내용 파일 업로드 시 **경고(차단하지 않음)**
- 확장자 허용 목록 + 매직 넘버 검사 + 크기 제한 + 실행 권한 없이 저장
- 브라우저에서 PDF·이미지·텍스트 미리보기, doc/xls/ppt 는 서버가 PDF 로 변환해
  미리보기 (LibreOffice 필요, 없으면 다운로드로 대체), 한글 파일명 그대로
  다운로드(RFC 6266)

**인증 / 사용자**
- 로그인하지 않으면 어떤 화면도 볼 수 없습니다
- Argon2id 비밀번호 해시, 서버 세션 + HttpOnly 쿠키
- Admin 은 사용자 생성 / 비밀번호 초기화 / 활성·비활성 / 권한 변경
- 일반 User 는 문서·버전 관련 기능 전체 사용 가능. **사용자 계정과 제품·분류 관리는 Admin 전용**
- 임시 비밀번호 상태에서는 Admin 도 비밀번호를 바꾸기 전까지 문서·버전 변경과 사용자·제품·분류 관리를 할 수 없음
- 브라우저를 계속 쓰고 있으면 세션과 쿠키가 함께 자동 연장됨
- 자기 계정 잠금, 마지막 Admin 강등 등은 구조적으로 차단

**목록 / 정렬 / 검색**
- 모든 표의 머리글 클릭으로 정렬. Documents 는 기본이 **제품 기준**
- 개정 번호는 자연 정렬 (`V1.0.9` < `V1.0.10`), 사람 이름은 한글 가나다순
- 빈 값은 오름차순·내림차순 모두 항상 마지막
- 문서 분류는 알파벳이 아니라 관리자가 지정한 **표시 순서**를 따름
- 제품 검색(이름·코드 부분 일치), 문서 필터(제품·분류·상태·이름)

**감사 / 조회**
- 25종 이벤트를 Audit Log 에 append-only 기록 (수정·삭제 API 없음)
- Dashboard: 제품·문서·버전·Current·저장소 사용량, 최근 업로드·Current 변경·활동
- 제품, 문서명, 분류, Document Number, Revision, Version, 언어, 파일명,
  업로더, 날짜 범위, Current 여부로 부분 검색

---

## 스크린 구성

```
Dashboard        현황 집계 + 최근 업로드 / Current 변경 / 활동
Products         제품 목록 → 제품 상세(문서별 Current Revision 표)
Documents        전 제품 문서 통합 목록 + 필터
  └ 문서 상세     Current 요약 + Revision History + 업로드/다운로드/Set as Current
Search           통합 + 상세 조건 검색
Recent Updates   최근 업로드 100건
Users            (Admin) 계정 관리
Categories       (Admin) 문서 분류 관리
Audit Logs       감사 기록 + before/after 변경내역
Settings         서버에 적용된 설정값 조회
My Account       내 정보 / 비밀번호 변경
```

---

## 기술 스택

| 계층 | 사용 기술 |
|---|---|
| Frontend | React 19, TypeScript, Vite 6 (개발 PC에서 빌드 → 정적 파일 배포) |
| Backend | Python 3.12, FastAPI, uvicorn |
| ORM / Migration | SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL 16 (`psycopg` 3, 바이너리 휠) |
| Password | Argon2id (`argon2-cffi`) |
| Reverse Proxy | nginx |
| 배포 | systemd 유닛 + rsync (Docker Compose 구성도 함께 제공) |

**서버에 Node.js 가 필요하지 않습니다.** 프론트엔드는 개발 PC에서 빌드하고
결과물만 전송합니다.

### 마운트 형태 두 가지

이 서비스는 **단독**으로도, 플랫폼의 **하위 경로**로도 뜹니다. 차이는 프론트엔드 빌드 시점의
base path 하나뿐입니다 — 이 값이 asset URL, react-router basename, API prefix를 모두
결정하므로 다른 곳을 고칠 필요가 없습니다 (`frontend/vite.config.ts`).

| | 단독 | 플랫폼 하위 |
|---|---|---|
| 접속 경로 | `http://<host>/` | `http://<host>/manual-hub/` |
| 빌드 명령 | `npm run build` | `npm run build:platform` |
| 배포 명령 | `./deploy/scripts/deploy.sh user@host` | `BUILD_MODE=platform ./deploy/scripts/deploy.sh user@host` |
| base path | `/` | `/manual-hub/` (`frontend/.env.platform`) |
| nginx 설정 | `deploy/nginx/qa-manual-hub.conf` | 저장소 루트 `deploy/nginx/qa-platform.conf` |
| `SESSION_COOKIE_PATH` | `/` (기본) | `/manual-hub/` |
| 사이드바 "QA 자동화 홈" 링크 | 없음 | 표시됨 |

플랫폼 하위로 붙이는 전체 절차는 [배포 가이드 §8](../../docs/DEPLOYMENT.md)에 있습니다.
아래 설치 절차는 **단독 배포 기준**으로 쓰여 있습니다.

### 배포 방식 두 가지

이 서비스는 두 경로를 모두 제공합니다.

- **네이티브 systemd** (`deploy/systemd`, `deploy/scripts`) — 서버에 이미
  PostgreSQL 이 있거나, 같은 호스트에서 보호해야 할 다른 서비스가 돌고 있을 때.
  Docker 가 iptables 를 조작해 기존 방화벽 정책을 우회하는 문제를 피할 수 있고,
  PostgreSQL 인스턴스가 이중화되지 않습니다.
- **Docker Compose** (`deploy/docker-compose.yml`) — 깨끗한 호스트나 로컬 개발
  환경에서 가장 빠른 경로.

자세한 판단 근거는 `deploy/docker-compose.yml` 상단 주석에 있습니다.

---

## 아키텍처

```
        사용자 (브라우저)
              │  HTTP
              ▼
   ┌──────────────────────────────┐
   │ nginx :80                    │
   ├───────────┬──────────────────┤
   │ /         │ /api/            │
   │ SPA 정적   │ reverse proxy    │
   │           │ (스트리밍, 버퍼 off)│
   └───────────┴────────┬─────────┘
                        │ 127.0.0.1:<backend port>
                        ▼
            ┌────────────────────────┐
            │ uvicorn / FastAPI      │
            │ systemd unit           │
            └──────┬─────────┬───────┘
                   │         │
                   ▼         ▼
        ┌────────────────┐ ┌──────────────────────────┐
        │ PostgreSQL 16  │ │ <DATA_ROOT>/storage/     │
        │ 전용 DB / role  │ │  <product>/<document>/   │
        └────────────────┘ │   <version>/<file>.<ext> │
                 │         └──────────────────────────┘
                 └──────────┬───────────────┘
                            ▼
                <DATA_ROOT>/backup/<timestamp>/
                  database.dump + storage.tar.gz + manifest.txt
```

### 서버 디렉터리

```
<APP_ROOT>/               (기본 /opt/qa-manual-hub)
├── .env                  # 600 — DB 접속정보. 커밋 금지
├── venv/
├── logs/                 # app.log, error.log, backup.log
├── scripts/              # qamh, backup.sh, restore.sh
└── app/
    ├── REVISION          # 배포된 커밋 해시
    ├── backend/
    └── frontend/         # Vite 빌드 산출물

<DATA_ROOT>/              (기본 /srv/qa-manual-hub)
├── storage/              # 750 — 실제 문서 파일
└── backup/               # 750
```

문서 파일과 DB 데이터는 저장소에 포함되지 않습니다 (`services/qa-manual-hub/.gitignore`).

---

## 데이터 모델

11개 테이블. 상세 컬럼은 `backend/app/models.py` 와
`backend/alembic/versions/0001_initial_schema.py` 에 있습니다.

```
users ─────┬──< sessions
           ├──< login_history
           ├──< products ──────┬──< documents ──┬──< document_versions
           ├──< document_categories ────────────┘         │
           ├──< stored_files <────────────────────────────┘
           ├──< audit_logs
           └──< system_settings
```

핵심 설계 결정 몇 가지:

- **Current 는 `documents.current_version_id` 단일 컬럼.** 버전 쪽에
  `is_current` 를 두면 "동시에 두 개가 Current" 상태가 물리적으로 가능해집니다.
  문서 행의 컬럼 하나로 두면 그 상태를 표현할 방법이 없습니다. 변경 시
  `SELECT ... FOR UPDATE` 로 문서 행을 잠그고 감사 로그까지 같은 트랜잭션에서
  커밋합니다.
- **`document_versions.uploaded_by_display_name` 은 스냅샷.** 사용자가 개명해도
  과거 업로더 표기가 그대로 남습니다.
- **`role` 은 PG enum 이 아닌 varchar.** 향후 `viewer` / `editor` / `manager`
  추가 시 타입 재작성 마이그레이션이 필요 없습니다.
- **대소문자 무시 유니크는 `lower(...)` 함수 인덱스.** 애플리케이션 비교와 DB
  제약이 일치합니다.
- **`stored_files` 가 물리 저장을 추상화.** `storage_backend` + `storage_key` 로
  분리해 두었으므로, NAS / S3 / MinIO 전환 시 `app/storage.py` 에 클래스를 추가하고
  팩토리 한 줄만 바꾸면 됩니다.
- **업로드마다 물리 파일을 새로 씁니다.** 내용이 같아도 중복 제거하지 않습니다.
  각 버전이 자기 파일을 소유하므로 한 버전의 보관/복원이 다른 버전에 영향을 주지
  않습니다. SHA-256 은 같은 내용 경고와 무결성 검증(`qamh check-storage --verify-sha256`)에만 씁니다.
- **`audit_logs` 는 append-only.** 애플리케이션에 UPDATE / DELETE 경로가 아예
  없습니다.

---

## 설치

### 사전 조건

- Ubuntu 22.04 / 24.04 (다른 systemd 리눅스도 동작)
- PostgreSQL 14 이상이 이미 구동 중 (`psql` 로 접속 가능)
- Python 3.11 이상
- 개발 PC에 Node.js 20 이상 (프론트엔드 빌드용)

### 1. 서버 설치

소스를 서버로 전송한 뒤:

```bash
sudo ./deploy/scripts/install.sh
```

`install.sh` 가 하는 일 — **모두 추가 작업만 수행합니다.**

- nginx / python3-venv / rsync 중 없는 것만 apt 설치
- `<APP_ROOT>`, `<DATA_ROOT>` 디렉터리 생성
- 전용 DB(`qa_manual_hub`)와 전용 role(`qamanual`) 생성.
  **이미 있으면 그대로 사용하고 절대 초기화하지 않습니다.** 이미 있는 role 의
  비밀번호도 바꾸지 않습니다. role 은 있는데 `.env` 에 비밀번호가 없으면 멈추고,
  그 role 을 이 서비스만 쓴다면 `RESET_DB_PASSWORD=1` 을 붙여 다시 실행합니다
- 무작위 DB 비밀번호로 `.env` 생성 (600)
- virtualenv 생성 + 의존성 설치
- systemd 유닛 설치 및 enable
- nginx 사이트 설치
- UFW 가 active 면 `80/tcp` 규칙 **1개만** 추가
- 자동 백업 `/etc/cron.d/qa-manual-hub-backup` 생성 (매일 02:30). 이미 있으면 그대로 둡니다
  (`SKIP_BACKUP_CRON=1` 로 건너뜀)

멱등이므로 몇 번이든 다시 실행할 수 있습니다. 실행 전 사전 점검에서
백엔드 포트 충돌이나 PostgreSQL 접속 실패를 발견하면 **아무것도 바꾸지 않고
중단**합니다.

환경변수로 조정할 수 있습니다:

```bash
sudo APP_ROOT=/opt/qamh DATA_ROOT=/data/qamh BACKEND_PORT=9190 \
     DB_NAME=mydocs DB_USER=mydocs SERVICE_USER=www-data \
     ./deploy/scripts/install.sh

# nginx 나 방화벽은 직접 관리하겠다면
sudo SKIP_NGINX=1 SKIP_UFW=1 ./deploy/scripts/install.sh
```

### 2. 코드 배포

개발 PC에서 실행합니다:

```bash
./deploy/scripts/deploy.sh user@server
```

프론트엔드 빌드 → 백엔드·SPA 전송 → 의존성 동기화 →
`alembic upgrade head` → 서비스 재시작 → 헬스체크.

`rsync` 가 없는 환경(예: Windows Git Bash)에서는 tar 파이프로 대체할 수 있습니다:

```bash
tar --exclude='__pycache__' -czf - -C backend . \
  | ssh user@server 'tar -xzf - -C <APP_ROOT>/app/backend'
(cd frontend && npm run build)
tar -czf - -C frontend/dist . \
  | ssh user@server 'rm -rf <APP_ROOT>/app/frontend/* && tar -xzf - -C <APP_ROOT>/app/frontend'
ssh user@server 'cd <APP_ROOT>/app/backend && <APP_ROOT>/venv/bin/alembic upgrade head'
ssh user@server 'sudo systemctl restart qa-manual-hub'
```

### 3. 최초 관리자 생성

```bash
sudo -u <SERVICE_USER> <APP_ROOT>/scripts/qamh bootstrap-admin
```

비밀번호를 대화형으로 입력받습니다. 기본적으로 **최초 로그인 시 변경**을
요구합니다. 초기 비밀번호는 소스코드·저장소·설정 파일 어디에도 저장되지 않습니다.

비대화형(자동화)이 필요하면 환경변수로 1회 전달할 수 있습니다:

```bash
BOOTSTRAP_ADMIN_PASSWORD='...' <APP_ROOT>/scripts/qamh bootstrap-admin
```

### 4. 기본 분류·제품 시드

```bash
<APP_ROOT>/scripts/qamh seed-catalog --product "제품명"
```

문서 분류 10종(Operation Manual, Service Manual, QC Manual,
DICOM Conformance Statement, Installation Manual, User Manual, Release Note,
Specification, Technical Manual, Other)을 만들고, `--product` 로 지정한 제품을
생성합니다. 이후에는 화면에서 자유롭게 추가·수정할 수 있습니다.

### 5. 서비스 시작 및 확인

```bash
sudo systemctl start qa-manual-hub
systemctl status qa-manual-hub --no-pager
curl -s http://127.0.0.1/api/health
```

```json
{"status":"ok","app":"QA Manual Hub","version":"1.0.0"}
```

### 6. 접속용 호스트명 (선택)

`deploy/nginx/qa-manual-hub.conf` 의 `server_name` 에 사용할 호스트명을 넣고,
사내 DNS 에 A 레코드를 등록하면 됩니다.

```
manual.example.internal.   IN  A   <서버 IP>
```

DNS 등록 전에는 **서버 IP 로 바로 접속**할 수 있습니다. nginx 설정에 `_`
(기본 서버)가 포함되어 있습니다.

---

## Docker Compose 로 실행

```bash
cd deploy
printf 'POSTGRES_PASSWORD=%s\n' '<강한 비밀번호>' > .env   # compose 가 읽는 deploy/.env
docker compose up -d --build
docker compose exec backend python -m app.cli bootstrap-admin
```

이미지는 `deploy/Dockerfile` 하나에서 만듭니다. `backend` 는 시작할 때
`alembic upgrade head` 를 실행하고, `web` 은 빌드한 화면 파일과
`deploy/nginx/docker.conf` 로 `http://<host>:8080/` 에서 응답합니다.
LibreOffice 는 넣지 않았으므로 doc/xls/ppt 미리보기는 다운로드로만 확인합니다.

> **참고** 이 경로는 Docker 가 없는 이 저장소의 개발 PC에서 실제로 띄워 보지 않았습니다.
> 파일 경로와 기본값은 `tests/test_deploy_scripts.py` 가 확인합니다.

---

## 설정

`<APP_ROOT>/.env` (템플릿: `deploy/.env.example`)

| 키 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | — | `postgresql+psycopg://user:pass@host:5432/db` |
| `STORAGE_ROOT` | `/srv/qa-manual-hub/storage` | 문서 파일 저장 루트. `backup.sh`·`restore.sh` 도 이 값을 읽어 묶고 풉니다 |
| `MAX_UPLOAD_MB` | `500` | 최대 업로드 크기 |
| `ALLOWED_EXTENSIONS` | `pdf,doc,docx,xls,xlsx,ppt,pptx,txt,md,png,jpg,jpeg` | 허용 확장자 |
| `SESSION_LIFETIME_HOURS` | `8` | 세션 유효 시간 |
| `SESSION_COOKIE_SECURE` | `false` | **HTTPS 적용 시 `true`** |
| `SESSION_COOKIE_SAMESITE` | `lax` | |
| `PASSWORD_MIN_LENGTH` | `1` | `1` = 길이 제한 없음(빈 값만 거부). 값을 올리면 API·CLI·웹 폼이 모두 그 값을 따릅니다 |
| `CORS_ORIGINS` | (빈 값) | Vite dev server 용. 운영에서는 비워 둡니다 |

변경 후 `sudo systemctl restart qa-manual-hub` 가 필요합니다.

> `MAX_UPLOAD_MB` 를 올릴 때는 nginx 의 `client_max_body_size` 도 함께 올려야
> 합니다. nginx 가 먼저 거절하면 사용자는 앱의 한국어 메시지 대신 기본 413
> 페이지를 보게 됩니다.

---

## 관리 CLI

```bash
<APP_ROOT>/scripts/qamh <command>
```

| 명령 | 설명 |
|---|---|
| `bootstrap-admin` | 최초 관리자 생성 |
| `seed-catalog [--product NAME]` | 기본 문서 분류(및 제품) 생성 |
| `reset-password <login_id>` | 비밀번호 강제 변경 — **관리자 잠김 복구용** |
| `list-users` | 사용자 목록 |
| `check-storage` | DB에 등록된 모든 버전의 파일 존재·크기 검증 |
| `check-storage --verify-sha256` | 위 검사에 더해 파일 내용을 다시 읽어 업로드 때 저장한 SHA-256 과 비교 (느림) |
| `purge-sessions` | 만료 세션 정리 |

래퍼가 `.env` 를 자동으로 로드하므로 어느 경로에서 실행해도 됩니다.
비밀번호는 대화형 프롬프트로 입력받아 셸 히스토리에 남지 않습니다.

---

## 백업 / 복구

### 백업

```bash
<APP_ROOT>/scripts/backup.sh
```

`<DATA_ROOT>/backup/<YYYYmmdd-HHMMSS>/` 에 세 파일을 만듭니다.

| 파일 | 내용 |
|---|---|
| `database.dump` | `pg_dump --format=custom --compress=6` |
| `storage.tar.gz` | 문서 저장소 전체 (`.env` 의 `STORAGE_ROOT`, 없으면 `<DATA_ROOT>/storage`) |
| `manifest.txt` | 백업 시각, 호스트, DB명, 파일 개수, 배포 커밋, 각 산출물 SHA-256 |

`restore.sh` 는 복원 전에 manifest 의 SHA-256 을 모든 파일과 맞춰 봅니다.
manifest 가 없거나, 값이 다르거나, `storage.tar.gz` 가 manifest 에 없으면 아무것도
바꾸지 않고 멈춥니다. 그래서 **DB 덤프와 파일 세트가 어긋난 조합으로 복구되는 일**이 없습니다.

자동 실행 (`/etc/cron.d/qa-manual-hub-backup`, `install.sh` 가 없을 때만 만듭니다):

```
30 2 * * *  <SERVICE_USER>  <APP_ROOT>/scripts/backup.sh >> <APP_ROOT>/logs/backup.log 2>&1
```

보존 정책은 `backup.sh` 의 `KEEP_DAILY`(기본 7) 변수로 조정합니다.

> 백업이 원본과 같은 디스크에 있으면 디스크 장애 시 동시에 소실됩니다.
> `BACKUP_ROOT` 를 별도 볼륨이나 NAS 로 지정하거나, 백업 디렉터리를 외부로
> 복제하는 것을 권장합니다.

### 복구

```bash
sudo <APP_ROOT>/scripts/restore.sh <DATA_ROOT>/backup/20260827-023000
```

**현재 데이터를 대체하는 작업입니다.** 스크립트는 다음 순서로 동작합니다.

1. 백업 폴더의 `manifest.txt` SHA-256 확인. 맞지 않으면 멈춤
2. 무엇을 덮어쓸지 출력하고 `RESTORE` 타이핑을 요구 (`--yes` 로 생략 가능)
3. **현재 상태를 `backup/pre-restore-<timestamp>/` 에 먼저 백업** — 되돌릴 수 있음.
   DB 덤프나 저장소 묶기가 하나라도 실패하면 서비스와 데이터를 건드리지 않고 멈춤
4. 서비스 정지
5. `public` 스키마 DROP → CREATE → `pg_restore`
6. 저장소(`STORAGE_ROOT`)를 `<STORAGE_ROOT>.replaced-<timestamp>` 로 옮기고 아카이브 전개
7. `alembic upgrade head` (덤프가 구버전 스키마일 수 있으므로)
8. 서비스 시작 및 `is-active` 확인
9. `qamh check-storage --verify-sha256` 로 DB↔파일 일치 검증

문제가 없으면 `<STORAGE_ROOT>.replaced-*` 와 `pre-restore-*` 를 정리합니다.

---

## 개발

```bash
# Backend
cd backend
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp ../deploy/.env.example .env                 # DATABASE_URL, STORAGE_ROOT 수정
alembic upgrade head
uvicorn app.main:app --reload --port 9180

# Frontend (별도 터미널)
cd frontend
npm install
npm run dev        # http://localhost:5173, /api 는 9180 으로 프록시
```

`CORS_ORIGINS=http://localhost:5173` 를 `.env` 에 넣으면 Vite dev server 에서
쿠키 인증이 동작합니다.

### 프론트엔드 빌드

```bash
cd frontend
npm run build            # 단독 배포용   (base '/')
npm run build:platform   # 플랫폼 하위용 (base '/manual-hub/')
```

경로를 하드코딩하지 말고 `import.meta.env.BASE_URL` 을 쓰십시오. 두 빌드 모두 CI 에서
검증합니다 (`.github/workflows/manual-hub.yml`).

### 테스트

PostgreSQL 이 실제로 필요합니다(JSONB, 함수 유니크 인덱스, `FOR UPDATE` 사용).

```bash
createdb qa_manual_hub_test
cd backend
export TEST_DATABASE_URL="postgresql+psycopg://user:pass@127.0.0.1:5432/qa_manual_hub_test"
pytest tests -q
```

모든 테스트가 통과해야 합니다(2026-09-30 기준 114개). PostgreSQL 없이도
`pytest tests/test_security_unit.py tests/test_cli_unit.py tests/test_deploy_scripts.py tests/test_frontend_upload_defaults.py`
는 돌릴 수 있습니다(배포 스크립트 검사는 `bash` 가 필요합니다).

| 파일 | 검증 범위 |
|---|---|
| `test_auth.py` | 로그인 성공/실패, 대소문자 무시 ID, 없는 ID 와 틀린 비밀번호의 응답 동일성, 비활성 차단, 보호 URL 401, 로그아웃, 실패 감사 기록, 본인 비밀번호 변경, 강제 변경 게이트 |
| `test_users.py` | 사용자 생성→로그인, 중복 ID, 짧은 비밀번호, 일반 User 의 관리 API 403, 활성/비활성, 라이브 세션 즉시 종료, 비밀번호 초기화, 본인·마지막 Admin 보호 |
| `test_documents.py` | 문서 생성, 이름 중복 규칙, 업로더 자동 기록(위조 무시), SHA-256, 확장자·매직넘버 검증, 중복 해시 경고, 자동 Current + 이력 보존, Legacy 업로드 후 Set as Current, 다운로드 바이트·한글 파일명, PDF inline preview, Archive/Restore, 검색 10종 |
| `test_audit.py` | 필수 이벤트 전수 기록, Current 변경 before/after, Audit 변경 API 부재, 필터, Dashboard 집계, Settings |
| `test_security_unit.py` | 감사 기록 IP(nginx 가 넣은 값만 믿음), 세션 쿠키 자동 연장, 임시 비밀번호 Admin 의 관리 API 차단, `/api/docs` 로그인 필수 (DB 없음) |
| `test_cli_unit.py`, `test_cli.py` | `seed-catalog` 감사 기록, `check-storage --verify-sha256` |
| `test_deploy_scripts.py` | 백업 manifest SHA-256, 안전 백업 실패 시 중단, `STORAGE_ROOT` 백업·복구, cron 생성, 기존 DB role 비밀번호 보존, Compose 파일 경로 (DB 없음, `bash` 필요) |
| `test_frontend_upload_defaults.py` | 업로드 창 Revision Date 기본값 빈칸 (DB 없음) |

### 코드 구조

```
backend/app/
├── config.py       환경변수 → Settings (pydantic-settings)
├── db.py           엔진 / 세션
├── models.py       SQLAlchemy 엔티티 11개
├── schemas.py      Pydantic 요청·응답 모델
├── security.py     Argon2id, 세션 토큰
├── deps.py         인증 의존성 (get_current_user / require_admin / ...)
├── storage.py      StorageBackend 프로토콜 + LocalDiskStorage
├── audit.py        감사 로그 기록 + action 상수
├── services.py     문서 목록 쿼리 / 중복 조회 헬퍼
├── cli.py          관리 CLI
├── main.py         FastAPI 앱
└── routers/
    ├── auth.py       로그인 / 로그아웃 / 내 계정
    ├── users.py      (Admin) 사용자 관리
    ├── catalog.py    제품 / 문서 분류
    ├── documents.py  문서 / 버전 / Current / 다운로드 / Preview
    ├── search.py     검색
    └── dashboard.py  Dashboard / Recent / Audit / Settings

frontend/src/
├── api.ts          fetch 래퍼 (401 → 로그인 화면 전환)
├── types.ts        백엔드 응답 타입
├── auth.tsx        AuthProvider / useAuth
├── App.tsx         라우팅 (미인증 시 전 경로 로그인 화면)
├── styles.css      단일 스타일시트
├── components/     Layout, ui (Card / Modal / Alert / 포맷 헬퍼)
└── pages/          Login, Dashboard, Products, ProductDetail, Documents,
                    DocumentDetail, Search, Users, Categories, AuditLogs, Misc
```

---

## 보안

| 항목 | 구현 |
|---|---|
| 비밀번호 | Argon2id. 평문 저장·로깅 없음. 파라미터 상향 시 로그인할 때 자동 재해싱. **길이 정책은 `PASSWORD_MIN_LENGTH` 하나로 결정되며 기본값은 1(제한 없음)** |
| 세션 | 서버 세션 테이블 + HttpOnly 쿠키. 쿠키에는 256bit 불투명 토큰, DB에는 SHA-256 만. 사용 중이면 DB 만료 시각과 쿠키 수명을 함께 연장 |
| 임시 비밀번호 | 비밀번호를 바꾸기 전에는 문서·버전 변경과 사용자·제품·분류 관리 API 를 모두 거절 (Admin 포함) |
| 로그인 실패 | 없는 ID / 틀린 비밀번호 / 비활성 계정 **모두 동일 메시지**. 사유는 서버 로그에만 |
| 세션 무효화 | 비활성화·비밀번호 초기화 시 해당 사용자 세션 전부 즉시 revoke |
| 권한 | 라우터 의존성으로 검사. 프론트엔드 라우팅과 별개로 API 가 독립 강제 |
| 업로드 | 확장자 허용목록 + 정규식 + 매직 넘버 + 크기 제한. 저장 파일명은 UUID, 권한 0640 |
| Path traversal | 원본 파일명을 파일시스템에 쓰지 않음. 저장 경로는 UUID 만 조합하고 루트 이탈 검사 |
| 다운로드 | `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox` |
| 감사 | append-only. UPDATE / DELETE 엔드포인트 없음. IP 는 nginx 가 넣은 `X-Real-IP`(없으면 `X-Forwarded-For` 의 마지막 값)만 씀 |
| 로그인 실패 제한 | 두지 않음. 감사 기록의 `로그인 실패` 를 정기 점검하는 운영 규칙으로 대신함 |
| 비밀정보 | `.env` 는 600 이며 `.gitignore` 처리. 저장소에 비밀값 없음 |

HTTPS 는 nginx 에 `listen 443 ssl` 블록을 추가하고 `.env` 의
`SESSION_COOKIE_SECURE=true` 로 바꾸면 됩니다. 그 외 변경은 필요하지 않습니다.

---

## 장애 확인

| 증상 | 확인 | 조치 |
|---|---|---|
| 502 Bad Gateway | `systemctl status qa-manual-hub` | 죽었으면 `journalctl -u qa-manual-hub -n 50`. `.env` 문법 오류가 흔한 원인 |
| 업로드 413 | 파일 크기 vs `MAX_UPLOAD_MB` / `client_max_body_size` | 두 값 모두 올리고 재시작 / 리로드 |
| 다운로드 410 Gone | `qamh check-storage` | DB 행은 있는데 파일 없음 → 백업에서 storage 복구 |
| 목록이 비어 보임 | Status 필터 | Archived 문서는 기본 숨김. `전체` 로 확인 |
| 로그인 직후 튕김 | 쿠키, `SESSION_LIFETIME_HOURS` | 세션 만료거나 관리자가 계정을 잠근 경우(정상 동작) |
| 백업이 안 돎 | `tail <APP_ROOT>/logs/backup.log`, `systemctl status cron` | cron.d 파일 존재·권한(644) 확인 |

로그 위치:

```
<APP_ROOT>/logs/app.log            uvicorn stdout
<APP_ROOT>/logs/error.log          uvicorn stderr
<APP_ROOT>/logs/backup.log         백업
/var/log/nginx/qa-manual-hub.*.log nginx
journalctl -u qa-manual-hub        systemd
```

---

## 한글 파일명 업로드 주의

파일 업로드는 **브라우저로** 하십시오.

스크립트로 대량 업로드할 때, 한국어 Windows 로케일의 **Git Bash `curl` 은
multipart 필드와 파일명을 CP949 로 인코딩**해 전송합니다. 서버는 RFC 에 따라
latin-1 로 디코드하므로 `(¸Å´º¾ó)...` 같은 형태로 저장됩니다.

애플리케이션 결함이 아닙니다. UTF-8 로 전송하면 정상 동작하며, 브라우저는
UTF-8 페이지에서 항상 UTF-8 로 보냅니다. 자동화가 필요하면 Python `urllib` /
`requests`, PowerShell 7 등 UTF-8 로 인코딩하는 클라이언트를 사용하십시오.

---

## 향후 확장

1차 범위에는 없지만 구조적으로 준비된 항목들입니다.

| 항목 | 준비 상태 |
|---|---|
| 권한 고도화 (Viewer / Editor / Manager, 제품·문서별 권한) | `role` 이 varchar, 권한 검사가 의존성 함수로 분리 |
| AD / LDAP / SSO | 인증이 `routers/auth.py` + `deps.py` 로 격리. 세션 발급 지점만 교체 |
| 문서 Workflow (Draft / Review / Approved / Published) | `document_versions.status` 가 varchar |
| Storage 전환 (NAS / S3 / MinIO / Index only) | `StorageBackend` 프로토콜 + `storage_backend` / `storage_key` 컬럼 |
| 공유폴더 자동 스캔 / 신규 문서 탐지 | SHA-256 인덱스로 중복 탐지 즉시 가능 |
| Revision 자동 추출 / 추천 | 업로드 창이 파일명에서 Version·Revision·개정일 등을 짐작해 칸을 채움(사용자가 확인 후 저장). 문서 내용에서 뽑는 기능은 미구현 |
| PDF / DOCX 본문 diff | 미구현 |
| 본문 Full-text Search / OCR | 현재 PostgreSQL ILIKE. 다음 단계는 `tsvector` 컬럼 |
| 알림 (Email / Teams) | 미구현. 감사 로그가 이벤트 소스 역할 가능 |
| HTTPS / MFA | nginx 구조 준비됨 |

---

## 문서

| 문서 | 내용 |
|---|---|
| [docs/USERGUIDE.md](docs/USERGUIDE.md) | 사용자·관리자·운영자 사용 안내 (한국어) |
| [저장소 README](../../README.md) | 상위 플랫폼 소개 |
| [문서 지도](../../docs/README.md) | 저장소 전체 문서 안내 |
| [배포 가이드 §8](../../docs/DEPLOYMENT.md) | 플랫폼 하위(`/manual-hub`) 통합 배포 |
| `../../deploy/nginx/qa-platform.conf` | 통합 배포용 nginx 설정 |
| `deploy/.env.example` | 환경설정 템플릿 |
| `deploy/scripts/install.sh` | 최초 설치 |
| `deploy/scripts/deploy.sh` | 코드 배포 |
| `deploy/scripts/backup.sh` / `restore.sh` | 백업 / 복구 |
| `/api/docs` | 로그인 후 접근 가능한 OpenAPI 문서 |

---

## 라이선스

사내 사용을 위한 코드입니다. 별도 라이선스가 명시되지 않았습니다.

---

## AI 에이전트 Context 관리 (Akela)

이 디렉터리는 독립 Project Root가 아닙니다. Akela Context는 **저장소 루트에서 상속**합니다.

| 대상 | 위치 (저장소 루트 기준) |
|---|---|
| Knowledge | `knowledge/manual-hub-*.md` |
| Protocol | `akela/PROTOCOL.md` |
| 설정 | `akela.json` — activity 5종 (아래) |

| activity | 언제 쓰나 |
|---|---|
| `manual-hub-dev` | 백엔드 코드·데이터 모델 변경 |
| `manual-hub-auth` | 인증·권한·감사 로그 |
| `manual-hub-ui` | React 프론트엔드 |
| `manual-hub-deploy` | 설치·배포·nginx·설정 |
| `manual-hub-backup` | 백업·복구·관리 CLI |

```bash
akela compile --activity manual-hub-dev --task <task-id>
```

이 디렉터리 하위에 별도 `akela.json` / `knowledge/` 를 만들지 않습니다. 에이전트가 지켜야 할
사항은 [`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) 에 있습니다.

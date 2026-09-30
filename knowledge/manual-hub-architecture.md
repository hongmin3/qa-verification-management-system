# QA Manual Hub — Architecture

> 이 문서의 저장소 상대 경로(`backend/`, `frontend/`, `deploy/` 등)는 모두
> `services/qa-manual-hub/` 기준이다. `<APP_ROOT>` / `<DATA_ROOT>` 는 서버의
> 런타임 경로이며 저장소 경로가 아니다.


## 전체 구조
<!-- akela: id=manual-hub-overview scope=manual-hub-dev,manual-hub-deploy tier=must -->

```
사용자 (브라우저)
      │ HTTP
      ▼
nginx :80
 ├─ /       → SPA 정적 파일 (프론트엔드 빌드 산출물)
 └─ /api/   → reverse proxy (스트리밍, 버퍼 off) → 127.0.0.1:<backend port>
                  │
                  ▼
        uvicorn / FastAPI (systemd unit)
             │           │
             ▼           ▼
     PostgreSQL 16   <DATA_ROOT>/storage/
     (전용 DB/role)   <product>/<document>/<version>/<file>.<ext>
             │           │
             └─────┬─────┘
                   ▼
     <DATA_ROOT>/backup/<timestamp>/
       database.dump + storage.tar.gz + manifest.txt
```

위 다이어그램은 이 서비스를 **단독으로** 배포할 때(`nginx :80`을 이 서비스가 전부 차지)
기준이다. `qa-verification-management-system` 운영 서버는 통합 배포를 쓴다 — nginx
하나(`deploy/nginx/qa-platform.conf`, 핵심 앱 저장소 소유)가 `/`는 핵심 앱(:24357)으로,
`/manual-hub/`는 이 서비스의 SPA/API로 나눠 보낸다. 이 경우 프론트엔드는
`npm run build:platform`(base `/manual-hub/`)으로 빌드해야 하고, 백엔드 `.env`에
`SESSION_COOKIE_PATH=/manual-hub/`를 넣어 세션 쿠키가 핵심 앱 요청까지 따라가지 않게
좁힌다. 2026-09-02부터 nginx가 80→443(self-signed 인증서)으로 강제 리다이렉트한다 —
자세한 통합 배포 구조와 HTTPS 설정은 핵심 앱 지식의
`core-deployment.md#platform-nginx`를 참고한다(이 파일 밖, 핵심 앱이 소유).

- 20명 내외 팀을 위한 단일 서버 애플리케이션. 마이크로서비스나 검색 클러스터를 쓰지 않는다.
- 서버에 Node.js 가 필요하지 않다. 프론트엔드는 개발 PC에서 빌드하고 결과물(정적 파일)만 서버로 전송한다.
- 백엔드 포트는 127.0.0.1 로만 바인딩되고, nginx 가 외부 진입점 역할을 한다.

## 계층별 역할 분담
<!-- akela: id=manual-hub-layer-roles scope=manual-hub-dev tier=should -->

| 계층 | 기술 | 역할 |
|---|---|---|
| nginx | nginx | `/` 는 SPA 정적 파일 서빙, `/api/` 는 uvicorn 으로 reverse proxy. 업로드 스트리밍을 위해 버퍼링 off |
| Frontend | React 19, TypeScript, Vite 6 | 개발 PC에서 빌드해 정적 파일로만 배포. 라우팅은 미인증 시 전 경로를 로그인 화면으로 전환 |
| Backend | Python 3.12, FastAPI, uvicorn | API 전체, 인증·권한 강제, 업로드 검증, 감사 로그 기록 |
| ORM / Migration | SQLAlchemy 2.0, Alembic | 스키마 정의와 마이그레이션 |
| Database | PostgreSQL 16 (`psycopg` 3) | 전용 DB / 전용 role. 다른 서비스와 공유하지 않음 |
| Storage | 로컬 디스크 (`StorageBackend` 프로토콜) | 실제 문서 파일. UUID 기반 경로로 저장 |

## 서버 디렉터리 구조
<!-- akela: id=manual-hub-server-directories scope=manual-hub-deploy,manual-hub-backup tier=should -->

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

문서 파일과 DB 데이터는 저장소(git)에 포함되지 않는다(`.gitignore`).

## 백엔드 코드 구조
<!-- akela: id=manual-hub-backend-code-structure scope=manual-hub-dev tier=must -->

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
```

## 프론트엔드 코드 구조
<!-- akela: id=manual-hub-frontend-code-structure scope=manual-hub-ui tier=should -->

```
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

## 확장 지점 (구조적으로 준비된 항목)
<!-- akela: id=manual-hub-extension-points scope=manual-hub-dev tier=should -->

| 항목 | 준비 상태 |
|---|---|
| 권한 고도화 (Viewer / Editor / Manager, 제품·문서별 권한) | `role` 이 varchar, 권한 검사가 의존성 함수로 분리 |
| AD / LDAP / SSO | 인증이 `routers/auth.py` + `deps.py` 로 격리. 세션 발급 지점만 교체 |
| 문서 Workflow (Draft / Review / Approved / Published) | `document_versions.status` 가 varchar |
| Storage 전환 (NAS / S3 / MinIO / Index only) | `StorageBackend` 프로토콜 + `storage_backend` / `storage_key` 컬럼 |
| 본문 Full-text Search / OCR | 현재 PostgreSQL ILIKE. 다음 단계는 `tsvector` 컬럼 |

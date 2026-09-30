# QA Manual Hub — Agent Instructions

**이 디렉터리는 독립 Project Root가 아니다.** QA 검증 관리 시스템 저장소의 하위 서비스이며,
Context는 저장소 루트에서 상속한다.

| 대상 | 위치 (저장소 루트 기준) |
|---|---|
| Protocol | `akela/PROTOCOL.md` |
| Knowledge | `knowledge/manual-hub-*.md` (이 서비스), `knowledge/*.md` (플랫폼 공통) |
| Akela 설정 | `akela.json` — 이 서비스 작업은 아래 activity 중 하나를 쓴다 |
| 저장소 전체 규칙 | 루트 `CLAUDE.md` |

| activity | 언제 쓰나 |
|---|---|
| `manual-hub-dev` | 백엔드 코드·데이터 모델 변경 |
| `manual-hub-auth` | 인증·권한·감사 로그 |
| `manual-hub-ui` | React 프론트엔드 |
| `manual-hub-deploy` | 설치·배포·nginx·설정 |
| `manual-hub-backup` | 백업·복구·관리 CLI |

작업 하나가 여러 영역에 걸치면 가장 많이 건드리는 쪽을 고른다. 필요한 섹션이 slice 에
없으면 `akela log outcome --status NEEDS_CONTEXT` 로 남긴다 — 그 기록이 scope 를 넓히는 근거다.

```bash
akela compile --activity manual-hub-dev --task <task-id>
```

여기(`backend/`, `frontend/`, `deploy/`)에 별도 `akela.json` / `knowledge/` 를 다시 만들지
않는다. 상위로 `akela.json` 을 탐색하면 저장소 루트가 나오며, 그것이 유일한 Project Root다.
경로 확인이 필요하면 루트의 `scripts/find-project-root.ps1` 을 쓴다.

## 이 서비스를 건드릴 때 유의할 점

- 핵심 앱(루트 `app/`)과 **프로세스도 DB도 공유하지 않는다.** 코드를 직접 import 하거나
  같은 DB를 읽는 방식으로 결합하지 않는다. 연결은 URL 링크, nginx 라우팅, 그리고 핵심 앱이
  이 서비스의 HTTP API 를 부르는 방식(REQ-HUB-014)까지다. 루트
  `docs/SHARED_PLATFORM_ARCHITECTURE.md` 7번 항목이 기준이다.
- 테스트는 실제 PostgreSQL 이 필요하다. 루트에서 `pytest` 를 돌려도 이 서비스 테스트는
  수집되지 않는다(`pytest.ini` 의 `testpaths`). `services/qa-manual-hub/backend` 에서
  따로 실행한다.
- 프론트엔드는 단독 배포(`npm run build`)와 플랫폼 서브패스 배포(`npm run build:platform`)
  두 가지를 모두 지원해야 한다. 경로를 하드코딩하지 말고 `import.meta.env.BASE_URL` 을 쓴다.

# AGENTS.md — qa-verification-management-system (QA 검증 관리 시스템)

SW 변경사항과 제품 사양서 및 Test Case를 분석하여 Regression 검증 대상을 자동 추천하는 QA 업무자동화 서비스

## Akela Context

Follow `akela/PROTOCOL.md` for every task.

## 저장소 구성 — 배포 단위 두 개

작업을 시작하기 전에 **어느 배포 단위를 건드리는지** 먼저 확인한다.

| | ① 핵심 앱 | ② 하위 서비스 |
|---|---|---|
| 위치 | `app/` (기능은 `app/modules/<name>/`) | `services/<name>/` |
| 스택 | FastAPI + Jinja2 + SQLite, 단일 uvicorn | 자체 스택 (예: React SPA + PostgreSQL) |
| 테스트 | 루트 `pytest` (`pytest.ini` testpaths=tests) | 자기 디렉터리에서 따로 실행 |
| CI | `.github/workflows/ci.yml` | `.github/workflows/<name>.yml` |

`services/*` 는 핵심 앱과 프로세스도 DB도 공유하지 않는다. 코드를 import 하거나 같은 DB를
읽는 방식으로 결합하지 않는다 — 연결은 URL 링크(`config.yaml` `services.*`)와 nginx
라우팅까지다. 어느 쪽에 기능을 추가할지 판단하는 기준과 체크리스트는
`docs/SHARED_PLATFORM_ARCHITECTURE.md` 에 있다.

Knowledge 는 하위 서비스도 루트 `knowledge/` 에 둔다 (`<name>-*.md`, `scope=<name>`).
`services/` 하위에 `akela.json` / `knowledge/` 를 새로 만들지 않는다.

## 문서

문서를 고치거나 추가할 때는 `docs/README.md`(문서 지도)의 표도 함께 갱신한다. 기능별 사용법은
문서가 아니라 앱 화면 안(`/impact-analyzer/guide`, `/manual-review/guide`)에 둔다.

## Project Root 탐색

이 프로젝트 하위 어디에서 작업하든(예: `src/`, `scripts/`, `tests/`) 먼저 현재 위치에서 상위로 `akela.json`을 탐색해 가장 가까운 Project Root를 식별하고, 그 Root의 `knowledge/`·`akela/PROTOCOL.md`를 사용한다. 하위 디렉터리에 별도 `akela.json`/`knowledge/`를 새로 만들지 않는다. 필요하면 `scripts/find-project-root.ps1`을 사용한다.

이 프로젝트를 Workspace 밖에서 단독으로 Clone해도 이 파일과 `akela.json`/`knowledge/`만으로 동일하게 동작해야 한다 (상위 Workspace 경로에 대한 의존성 없음).

<!-- readme-guidance: start -->
## README 작성 기준

- 처음 보는 사람이 **무엇을 해주는 프로젝트인지, 왜 필요한지, 어떻게 시작하는지** 이해할 수 있게 쓴다.
- 첫 부분은 쉬운 한두 문장과 실제 사용 예로 설명한다. 전문 용어는 필요한 곳에서 풀어 쓴다.
- 준비 사항 → 설치 → 첫 실행 → 기대 결과 순서의 **빠른 시작**을 앞에 둔다. 확인한 명령만 적는다.
- 자세한 운영 규칙, 내부 구조, 검증 기록은 `docs/` 등 별도 문서에 두고 README에서 연결한다.
- README는 현재 기능과 사용법을 설명한다. 세션별 작업 경과나 수정 내역을 길게 나열하지 않는다.
- 구현과 README가 어긋나지 않게 함께 갱신한다. 미검증 기능·플랫폼·제한사항은 분명히 구분한다.
- 기존 프로젝트의 기능·설정·민감정보·고유 문서 체계를 보존한다. 문서 정리를 이유로 실행 동작을 바꾸지 않는다.
<!-- readme-guidance: end -->

<!-- project-spec-guidance: start -->
## 사양 기반 개발 (SPEC)

<!-- spec-workflow: v2 -->

`SPEC.md`가 이 프로젝트가 **어떻게 동작해야 하는가**의 기준이다. 코드의 현재 동작은 사양이
아니다. 아래는 상위 기준이고, 실제 절차(계획·테스트·검증)는 기존 Skill을 그대로 쓴다.

**작업 시작 전** — 기능 추가·변경·버그 수정이면 먼저 `SPEC.md`에서 ① 관련 Requirement,
② 그 Requirement의 구현, ③ 관련 테스트, ④ 변경 영향 범위를 확인한다. 해당 Requirement가
없는 신규 기능은 **구현 전에** SPEC에 추가한다. typo, 주석, 문서만 바꾸는 작업은 예외다.

**구현** — 사양에 없는 기능을 임의로 추가하지 않는다. 요구사항이 불충분하거나 모순되면
추측으로 확정하지 말고 SPEC의 "13. 미확정 사항"에 `(TBD)` 또는 `확인 필요`로 남기고
사용자에게 알린다.

**변경 요청 판별** — 요청을 받으면 먼저 현재 SPEC · 요청 · 기존 구현 · 변경 의도를 대조해
둘 중 무엇인지 정한다.

- **사양 변경**: `SPEC.md` 수정 → 테스트 수정·추가 → 구현 → 검증 → `CHANGELOG.md`
- **기존 사양 미충족(버그)**: SPEC 유지 → 재현 테스트 → 코드 수정 → regression 확인 → `CHANGELOG.md`

버그를 고치려고 올바른 기존 사양을 바꾸지 않는다.

**완료 전** — `Requirement → 구현 → 테스트 → 실제 실행 결과`를 모두 확인한다. 테스트 PASS는
실제 동작 검증을 대체하지 않는다. 실행 가능한 프로젝트는 대표 실행 경로를 실제로 돌리고
출력을 읽는다.

**완료 시** — 검증을 통과하면 관련 문서(`CHANGELOG.md`, `progress.md`, 필요하면 `README.md`와
`SPEC.md`)를 갱신하고 저장소를 커밋·푸시한다. 사용자 요청을 기다리지 않으며 하위 프로젝트
세션에도 동일하게 적용한다. 게이트·CI·라이브 세션 충돌 회피는 설치된 키트의 공통 `AGENTS.md`
push 규칙을 따른다.

**SPEC / CODE 불일치** — 조용히 맞추지 말고 다음 형식으로 보고한다.

```text
SPEC / CODE MISMATCH

Requirement:
Specification:
Current Implementation:
Difference:
Action: SPEC 수정 / CODE 수정 / 사용자 확인 필요
```

코드의 현재 상태를 정당화하려고 SPEC을 고치지 않는다.

**ID 규칙** — `REQ-<CATEGORY>-NNN`, `NFR-<CATEGORY>-NNN`, `TEST-<CATEGORY>-NNN`.
CATEGORY는 대문자·숫자, NNN은 세 자리. 한 번 부여한 ID는 재사용하거나 의미를 바꾸지 않고,
삭제한 ID를 다른 기능에 돌려쓰지 않는다. 추적은 테스트 쪽에 남긴다(예: 테스트 위에
`Validates: REQ-QUERY-001`). 검색용 주석을 모든 함수에 강제로 달지 않는다.

**문서 경계** — 같은 내용을 두 곳에 두지 않는다.

| 파일 | 담는 것 |
|---|---|
| `SPEC.md` | 현재 시스템이 어떻게 동작해야 하는가 |
| `CHANGELOG.md` | 무엇이 변경되었는가 |
| `progress.md` | 현재 작업이 어디까지 진행됐는가 |
| `knowledge/` | AI가 작업할 때 필요한 판단 규칙·맥락 |
| `README.md` | 사람이 설치하고 사용하는 방법 |
| `AGENTS.md` | AI가 따라야 하는 작업 규칙 |

SPEC 내용을 `knowledge/`에 복제하지 않는다. knowledge는 Requirement ID를 **참조**만 한다
(예: "REQ-EXPORT-001을 고칠 때 한글 파일명 encoding 회귀를 항상 확인한다").
<!-- project-spec-guidance: end -->

<!-- project-readiness-command: start -->
## 프로젝트 완료 검사

하위 폴더에서 작업을 시작했더라도 완료 전에는 이 프로젝트 루트로 이동하여 다음 명령을 실행한다.
```text
node .project-check/project-readiness.js .
```
검사 실패를 해결하거나 미완료로 보고한다. 이 검사는 문서와 경로 연결을 확인하며 프로젝트 자체 테스트와 실제 동작 검증을 대신하지 않는다.
<!-- project-readiness-command: end -->

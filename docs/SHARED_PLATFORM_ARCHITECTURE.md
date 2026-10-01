# QA 자동화 플랫폼 공용 아키텍처

> 상위 문서: [README](../README.md) · [문서 지도](README.md)

새 QA 기능을 어디에 붙일 것인가를 결정하는 문서다. 이 저장소에는 배포 단위가 두 종류 있고,
**둘 중 어느 쪽인지 먼저 정한 다음** 구현에 들어간다.

## 두 가지 확장 방식

| | ① In-process 모듈 | ② 하위 서비스 |
|---|---|---|
| 위치 | `app/modules/<name>/` | `services/<name>/` |
| 프로세스 | 핵심 앱과 **같은** uvicorn | **별도** 프로세스 |
| DB | 공유 SQLite (테이블 소유권은 모듈 단위로 분리) | 자기 DB |
| URL | 같은 앱의 URL prefix (`/manual-review` 등) | nginx가 붙이는 경로 (`/manual-hub/`) |
| 연결 방식 | `app/web/router.py`가 라우터를 취합 | URL 링크만 (`config.yaml` `services.*`) |
| 배포 | 핵심 앱 배포에 포함 | 자기 배포 스크립트 |
| 현재 예 | `qa_agent`, `manual_review`, `knowledge`, `cost_dashboard` | `qa-manual-hub` |

**기본값은 ①이다.** 새 QA 프로그램은 별도 웹서비스나 별도 DB부터 만들지 않는다. ②는 스택이
근본적으로 다를 때만 선택한다 — QA Manual Hub가 그 경우다 (React SPA + PostgreSQL + 자체
인증·세션·감사 로그. 핵심 앱의 Jinja2 + SQLite 프로세스에 얹을 수 없다).

②를 고르면 결합은 **URL 링크 하나로 끝난다.** 코드를 import하지 않고, 같은 DB를 읽지 않는다.
공유하는 것은 같은 저장소, 같은 호스트, 같은 nginx뿐이다.

## ① In-process 모듈의 경계

- `app/core/`: 설정, 저장소, 로깅, AI 호출, 스케줄러처럼 도메인에 독립적인 인프라
- `app/web/`: 허브, 공용 HTML 골격·정적 파일, 모듈 라우터 취합
- `app/modules/manual_review/`: [매뉴얼 개정 검증](modules/manual-review.md)
- `app/modules/knowledge/`: 두 기능이 함께 사용하는 제품 사양서·TC 등록·삭제·동기화
- `app/modules/cost_dashboard/`: AI 호출·토큰·캐시 사용량 집계
- `app/prompts/`: 버전 관리되는 기능별 AI prompt

각 모듈은 자신의 라우터·스키마·서비스 로직·템플릿을 소유하며, `app/web/router.py`는 URL
prefix만 결정한다. 이 파일에는 비즈니스 로직을 두지 않는다.

## DB 확장 원칙 (① 전용)

SQLite 파일은 당분간 하나를 공유하되 **테이블 소유권은 모듈 단위로 분리**한다. 공유
식별자(제품, 문서, 분석 실행)만 공용 테이블을 재사용하고, 기능 고유 데이터는 `manual_*`처럼
충돌하지 않는 접두어를 쓴다. 모듈 간 DB 테이블을 직접 조작하지 않고 `app/core/storage.py`의
명시적인 메서드를 통해 접근한다.

1. 공통 제품과 업로드 문서는 기존 `products`/`documents`를 재사용한다.
2. 기능 전용 상태와 결과는 해당 모듈 전용 테이블에 둔다.
3. 테이블 추가·변경은 반복 실행 가능한 migration 방식으로 관리한다. 현재의
   `CREATE TABLE IF NOT EXISTS`/컬럼 보강 방식은 소규모 단일 서버 동안만 유지하고, 운영
   인스턴스나 개발자가 늘어나는 시점에는 Alembic 같은 버전 migration으로 전환한다.
4. SQLite WAL과 단일 프로세스는 현재 부하에 적합하다. 동시 쓰기 증가, 다중 worker, 여러 서버
   배포 중 하나가 필요해지면 PostgreSQL로 이전한다.
5. 비밀정보, 업로드 원본, 생성 결과물은 DB에 본문으로 넣지 않고 설정된 파일 저장소에
   보관하며 DB에는 경로와 metadata만 저장한다.

## 새 In-process 모듈 추가 체크리스트

1. `app/modules/<name>/`에 router/service/schema/template을 추가한다.
2. `app/web/router.py`에서 충돌 없는 prefix로 등록한다.
3. 공용 데이터와 전용 데이터를 구분해 Storage API와 migration을 추가한다.
4. 허브 카드를 갱신하고, 모듈 내부에 전용 내비게이션과 전용 사용법을 둔다. 기능 간 링크를
   공용 레이아웃에 섞지 않는다.
5. `docs/modules/<name>.md`를 추가하고 [문서 지도](README.md)에 등록한다.
6. 라우트·저장소·도메인 단위 테스트와 전체 회귀 테스트를 통과시킨다.
7. 로컬 검증 후 배포 스크립트로 동일 파일을 서버에 반영한다.

## 새 하위 서비스 추가 체크리스트

1. `services/<name>/`에 코드·배포 스크립트·자체 README를 둔다.
2. **Akela Context는 루트에서 상속한다.** 하위에 `akela.json`/`knowledge/`를 새로 만들지
   않는다. Knowledge는 루트 `knowledge/<name>-*.md`로 둔다.
   activity는 서비스 이름 하나로 뭉뚱그리지 말고 **그 서비스에서 실제로 반복되는 작업
   단위**로 나눈다 (`<name>-dev`, `-auth`, `-ui`, `-deploy`, `-backup` 식). 하나로 묶으면
   태깅을 해도 매 작업에 전부 들어와 스코핑이 무의미해진다. 여러 작업에 필요한 섹션은
   `scope`에 쉼표로 여러 activity를 준다. 자세한 근거는
   [Context Engineering](CONTEXT_ENGINEERING.md).
3. `config.yaml`의 `services.<name>`에 표시 이름·설명·URL을 등록한다. URL이 비어 있으면 허브
   카드가 만들어지지 않으므로, 그 서비스를 배포하지 않은 환경에서도 깨진 링크가 생기지 않는다.
4. nginx에 경로를 추가한다 ([`deploy/nginx/qa-platform.conf`](../deploy/nginx/qa-platform.conf)).
   프론트엔드가 있으면 서브패스 배포를 지원해야 한다 — 경로를 하드코딩하지 않는다.
5. 자기 CI 워크플로를 `.github/workflows/<name>.yml`에 두고 `paths` 필터로 자기 디렉터리만
   감시한다. 루트 `pytest.ini`의 `testpaths` 때문에 하위 서비스 테스트는 루트 `pytest`에
   수집되지 않으므로, 반드시 자기 워크플로에서 돌린다.
6. 핵심 앱 코드를 import하거나 같은 DB를 읽지 않는다. 결합은 URL과 nginx 라우팅까지다.
7. 데이터를 주고받아야 하면 **HTTP API로만** 한다. 그때 지킬 것 세 가지:
   설정·자격증명이 없으면 연동이 꺼진 채 기존 동작을 유지할 것, 상대가 죽어도 그 단계만
   건너뛰고 계속 진행할 것, 자격증명은 `secrets.*`에 두고 로그에 남기지 말 것.
   현재 예: `app/core/manual_hub_client.py` (매뉴얼 개정 검증의 Cross-Manual 대조).

## 이 구조를 고른 이유

기능 수가 적은 현재 단계에서 배포·운영을 단순하게 유지하면서도, 향후 부하나 조직 경계가 실제로
생겼을 때 모듈을 별도 서비스와 DB로 분리할 수 있는 경계를 미리 보존한다. ②의 경계를 이미
한 번 실제로 만들어 뒀기 때문에(`services/qa-manual-hub`), ①의 모듈이 ②로 승격되어야 할 때
따라갈 선례가 있다.

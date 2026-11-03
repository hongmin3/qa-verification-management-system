# QA 검증 관리 시스템 ALM 스킬 안내

<!-- spec-feature: v2 -->

| 항목 | 값 |
|---|---|
| 상위 사양 | [../SPEC.md](../SPEC.md) |
| 기능 ID | `alm-skill` |
| Document Version | 0.1.0 |
| Last Updated | 2026-11-03 |
| Status | implemented |

먼저 목적과 범위를 읽고, 5절에서 안내 화면과 자료 연결 기준을 확인한다. 설치와 질문 예시는 앱 안 안내 페이지에서 제공한다.

## 1. 목적

QA 사용자가 ALM 스킬의 활용 범위와 설치 방법을 이해하고, 상세 가이드와 교육 영상 및 원본 슬라이드를 한 화면에서 찾게 한다. 공통 ALM 절차와 제품별 검증 근거의 역할을 구분한다.

## 2. 범위

기존 핵심 앱의 정적 파일 제공 기능으로 `/static/alm-ai/index.html`을 제공한다. QA 홈의 스킬 관리 카드에서 공통 목록과 개별 안내로 들어간다. 별도 서버 프로세스·DB·로그인·AI 호출·Polarion 변경 기능은 추가하지 않는다.

## 5. 기능 요구사항

| 카테고리 | 이름 |
|---|---|
| ALMSKILL | ALM 스킬 안내와 교육 자료 연결 |

### REQ-ALMSKILL-001 활용과 설치 안내

**하는 일** QA 홈의 스킬 관리 카드에서 ALM 상세 안내로 들어간다. 페이지는 활용 범위, Codex와 Claude Code의 준비 사항·ZIP 설치·첫 질문·설치 확인·문제 해결을 설명한다.

**순서** 설치 없이 가이드를 읽는 길과 스킬을 설치해 질문하는 길을 함께 제공한다. VXvue AI 안내 및 QA 홈으로 돌아가는 링크를 둔다. 제품 스킬과 함께 쓰는 질문 예시를 제공한다.

**지킬 것** 제품 사양과 공통 ALM 교육을 구분한다. 영상의 화면 표본 검토와 음성 전체 전사, 현재 운영 ALM 시험 여부를 구분한다. CLI 스킬을 Claude Desktop 전용 확장처럼 안내하지 않는다.

### REQ-ALMSKILL-002 현재 자료 판과 다운로드

**입력** `/static/alm-guide/current.json`의 `release_path`와 해당 판의 `manifest.json`이다. 교육 자료 본문·PPT·영상·설정의 실제 값은 저장소에 넣지 않는다.

**순서** 자료 목록의 release 경로를 영문·숫자·하이픈으로 검증하고 같은 출처 아래의 manifest를 읽는다. 판 이름이 일치하는지 확인한 뒤 가이드·Word·PDF·Skill ZIP 링크와 자료 수량을 표시한다. 자료 수량은 manifest에서 읽고 화면 코드에 고정하지 않는다.

**안 될 때** 목록·manifest 읽기 실패, 잘못된 판 경로 또는 판 불일치는 화면에 알린다. 다운로드 링크를 활성화하지 않고 다시 불러오기를 제공한다. 없는 개별 파일은 해당 링크를 비활성으로 둔다. 등록된 내용은 HTML로 실행하지 않고 글로 표시한다.

**지킬 것** 안내 화면은 자료 목록의 표시 여부만 확인한다. CLI 스킬의 SHA256 검증이나 실제 운영 설정 검증이 끝났다고 표시하지 않는다. 자료 링크는 같은 서버의 정해진 정적 자료 경로 안에서만 만든다.

### REQ-ALMSKILL-003 기존 로그인 브라우저 작업 절차와 스킬 배포

**하는 일** 사용자가 구체적인 ALM 등록·수정·연결·Import를 요청하면 스킬이 현재 실행 환경의 Computer Use 도구와 기존 Edge·Chrome 로그인 세션을 사용하도록 안내한다. 미래의 사용 의향만으로 ALM 기록을 바꾸지 않는다.

**순서** 서버·프로젝트·대상 ID·현재 상태·변경값을 확인하고 허용된 작업을 수행한 뒤 같은 객체의 저장값을 다시 조회한다. 탭·창·실제 화면의 대상이 다르거나 연결이 없으면 입력을 중단한다. 결과 기록은 실제 수행 증거에 따르고 전자서명은 사용자에게 넘긴다.

**결과** 스킬 원본은 `deploy/alm-skill/alm`에서 관리한다. 빌드 도구는 지정한 QA 서버 주소로 설정을 넣은 ZIP을 만들며 기존 출력 파일을 덮어쓰지 않는다. 안내 화면은 필요한 연결과 실제 저장 시험 여부를 구분한다.

**지킬 것** 기존 브라우저 프로필을 복사하거나 쿠키·비밀번호를 추출하지 않는다. 사용자가 지정한 로그인 세션을 새 자동화 전용 브라우저로 대체하지 않는다. 컴퓨터 제어 도구의 별도 확인 정책을 따른다.

### REQ-ALMSKILL-004 스킬 관리와 하위 업데이트 문서

**하는 일** QA 홈의 스킬 관리 카드로 `/static/skills/index.html`을 연다. VXvue·Bellalun·XIPL·ALM 네 상세 페이지와 각 업데이트·개선 문서를 제공한다.

**순서** 상세 페이지는 목적·사용 방법·현재 제공 상태와 기존 안내로 연결한다. Bellalun 작업 세션의 결과가 확인되기 전에는 연결 준비 중으로 표시한다. 기존 VXvue·ALM 안내 주소와 설치·인증 경로는 유지한다.

**지킬 것** 새 안내·개선 문서는 `app/web/static/skills/<스킬>/` 하위에 작성한다. 향후 배포 파일은 같은 하위의 `releases/<버전>/`에 보존하고, 업데이트 문서와 상세 페이지를 같은 변경으로 갱신한다. 완료한 변경과 개선 예정·미검증을 구분한다. 다른 세션이 만드는 스킬 본체를 덮어쓰지 않는다. 파일 감시나 자동 배포 기능으로 설명하지 않는다.

## 11. 테스트 사양

### TEST-ALMSKILL-001 안내 진입과 본문

실제 앱에서 QA 홈과 안내 HTML·CSS·JS가 열린다. 설치 대상·제품 스킬 병행·자료 한계가 안내되고 외부 유료 서비스 호출은 없다.

### TEST-ALMSKILL-002 자료 연결과 실패

브라우저에 합성 목록·manifest를 제공해 링크와 수량이 입력 자료 판으로 바뀌는지 확인한다. 경로 이탈·판 불일치·네트워크 실패는 링크 활성화 없이 표시하고 재시도로 회복한다. 모바일 너비에서 수평 넘침이 없다.

### TEST-ALMSKILL-003 스킬 패키지와 작업 경계

ZIP에 브라우저 작업 절차와 지정 서버 설정이 들어가고 비밀값이 담긴 주소·잘못된 주소·기존 출력 덮어쓰기는 거절한다. 실제 ALM 저장은 이 패키지 시험에 포함하지 않는다.

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-ALMSKILL-001 | `app/web/templates/hub.html`, `app/web/static/alm-ai/index.html`, `app/web/static/alm-ai/style.css` | `tests/test_alm_skill_guide.py` | verified |
| REQ-ALMSKILL-002 | `app/web/static/alm-ai/guide.js` | `tests/test_alm_skill_guide.py` | verified |
| REQ-ALMSKILL-003 | `deploy/alm-skill/alm/SKILL.md`, `deploy/alm-skill/alm/references/browser-operations.md`, `scripts/build_alm_skill.py` | `tests/test_alm_skill_package.py` | implemented |
| REQ-ALMSKILL-004 | `app/web/static/skills/index.html`, `app/web/static/skills/AGENTS.md`, `app/web/templates/hub.html` | `tests/test_skill_management.py` | verified |

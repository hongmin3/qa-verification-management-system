# QA 검증 관리 시스템 ALM 스킬 안내

<!-- spec-feature: v2 -->

| 항목 | 값 |
|---|---|
| 상위 사양 | [../SPEC.md](../SPEC.md) |
| 기능 ID | `alm-skill` |
| Document Version | 0.2.0 |
| Last Updated | 2026-11-05 |
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

**Checklist 전체 등록** 사용자가 전체 등록을 요청하면 최신 원본 대조·허용된 파일 준비·Preview·Import·문서 편입·실제 SRS 관계 연결·미서명 문서 표시·전체 저장 대조를 안내한다. 기존 정상 서비스 API는 승인된 대량 작업에 병행할 수 있으며 브라우저 인증을 추출하지 않는다. 참고 문서의 결재표·공통 매크로와 실제 결재경로 속성을 구분한다. 원본 변경과 불명확한 저장은 기존 결과를 보존한 채 대조하고, 권한 제한과 미완료를 보고한다. 조사·준비 요청에는 쓰기를 추가하지 않는다.

**지킬 것** 기존 브라우저 프로필을 복사하거나 쿠키·비밀번호를 추출하지 않는다. 사용자가 지정한 로그인 세션을 새 자동화 전용 브라우저로 대체하지 않는다. 컴퓨터 제어 도구의 별도 확인 정책을 따른다.

### REQ-ALMSKILL-004 스킬 관리와 하위 업데이트 문서

**하는 일** QA 홈의 스킬 관리 카드로 `/static/skills/index.html`을 연다. VXvue·Bellalun·XIPL·ALM 네 상세 페이지와 각 업데이트·개선 문서를 제공한다.

**순서** 목록의 카드는 바로 상세 사용 안내를 연다. VXvue는 `/vxvue-desktop`, ALM은 `/static/alm-ai/index.html`, XIPL과 Bellalun은 각 스킬 하위의 실제 사용 안내로 연결한다. 링크만 놓은 중간 소개 페이지는 거치지 않는다. 각 업데이트·개선 문서는 목록에서도 별도로 바로 열 수 있다. Bellalun 작업 세션의 결과가 확인되기 전에는 연결 준비 중으로 표시한다. 기존 설치·인증 경로는 유지한다.

**상세 안내** XIPL에는 공용 연결 준비·네 조회 도구·자료 판 유지·업무별 질문 예시·제품 사양 대조·문제 해결·문서 추출 한계를 설명한다. 기존 VXvue·ALM 중간 주소는 실제 안내로 즉시 이동하는 호환 주소로 남긴다.

**지킬 것** 새 안내·개선 문서는 `app/web/static/skills/<스킬>/` 하위에 작성한다. 향후 배포 파일은 같은 하위의 `releases/<버전>/`에 보존하고, 업데이트 문서와 상세 페이지를 같은 변경으로 갱신한다. 완료한 변경과 개선 예정·미검증을 구분한다. 다른 세션이 만드는 스킬 본체를 덮어쓰지 않는다. 파일 감시나 자동 배포 기능으로 설명하지 않는다.

### REQ-ALMSKILL-005 실행 중심 안내와 업무 사례

**하는 일** ALM·PLM은 기존 로그인 브라우저와 Computer Use를 이용한 업무 수행을 먼저 안내한다. 사용법 질문에는 설명·근거 조회로 답한다. 스킬 설치만으로 컴퓨터 제어 연결이나 쓰기 권한이 생긴다고 설명하지 않는다.

**순서** 구체적인 실행 요청은 현재 대상·상태·입력 확인, 허용된 조작, 저장 후 같은 객체 재조회, 결과와 미완료 보고까지 진행한다. 조사 요청에는 쓰기를 추가하지 않는다. 이미 받은 일반 저장 권한을 반복해서 묻지 않는다. 서명과 최종 승인은 사람이 처리하며 현재 도구의 확인 정책을 따른다.

**업무 예시** ALM·PLM의 스킬 참조 MD와 웹 안내에 준비할 입력·복사용 요청·수행 단계·결과 보고 형식·확인 불가 때의 처리를 제공한다. VXvue·Bellalun 웹 안내는 과거 요청을 재구성한 상세 사례를 제공하고 기존 실조회 사례와 구분한다.

**지킬 것** 과거 요청 재구성·일반 요청 예시·교육 절차 기반 예시를 구분한다. 결과의 ID·수량·Pass·메일 전송을 가상 완료 사실로 만들지 않는다. 제품 스킬의 읽기 도구, 파일 산출물, 별도 브라우저 변경, 실제 제품·장비 실행의 범위를 구분한다. 예시의 제품·버전·행 번호는 다음 작업의 고정값이 아니다.

**관리** 업무 사례 원본에서 스킬 MD와 웹 구역을 함께 생성하고 검사한다. 설치·다운로드·기존 링크·과거 실조회 사례를 보존한다. 스킬 지침 변경은 새 ZIP으로 배포하고 웹 문서만 수정된 제품 스킬의 본체가 갱신됐다고 설명하지 않는다.

**Claude 사용** Claude Code는 사용자 스킬 폴더와 `/alm`·`/plm` 호출을 안내한다. Claude Desktop·Cowork의 Skill ZIP 업로드는 로컬 폴더 설치와 구분한다. 업로드만으로 사내망 자료 조회·PC 제어가 연결됐다고 설명하지 않는다. 현재 도구·실행 위치·서버 접근 여부를 확인하고 연결이 없는 범위는 미수행으로 보고한다.

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
| REQ-ALMSKILL-003 | `deploy/alm-skill/alm/SKILL.md`, `deploy/alm-skill/alm/references/browser-operations.md`, `deploy/alm-skill/alm/references/checklist-registration.md`, `scripts/build_alm_skill.py` | `tests/test_alm_skill_package.py` | implemented |
| REQ-ALMSKILL-004 | `app/web/static/skills/index.html`, `app/web/static/skills/AGENTS.md`, `app/web/templates/hub.html` | `tests/test_skill_management.py` | verified |
| REQ-ALMSKILL-005 | `deploy/skill-examples.json`, `scripts/render_skill_examples.py`, `deploy/alm-skill/alm/references/work-examples.md`, `app/web/static/alm-ai/index.html`, `app/web/static/plm-ai/index.html`, `app/modules/vxvue_chatbot/templates/desktop-guide.html`, `app/web/static/skills/bellalun/index.html` | `tests/test_skill_work_examples.py` | verified |

# QA 검증 관리 시스템 PLM 스킬 안내

<!-- spec-feature: v2 -->

| 항목 | 값 |
|---|---|
| 상위 사양 | [../SPEC.md](../SPEC.md) |
| 기능 ID | `plm-skill` |
| Document Version | 0.1.0 |
| Last Updated | 2026-11-05 |
| Status | implemented |

목적과 범위를 읽은 뒤 5절에서 필요한 기능을 찾는다. 설치와 업무별 사용법은 앱의 상세 안내에서 제공한다.

## 1. 목적

QA 담당자가 Teamcenter의 산출물 등록·승인 준비·DHF 연결·WBS 절차를 교육 근거와 함께 찾는다. 별도 `plm` 스킬을 설치해 같은 서버 자료를 조회한다.

| 용어 | 뜻 |
|---|---|
| Teamcenter | 사내 PLM 시스템의 제품 이름 |
| DHF | 프로젝트 설계 이력. Record에 승인 문서를 연결한다 |
| WBS | 담당자에게 배정된 프로젝트 일정 작업 |

## 2. 범위

핵심 앱의 정적 파일 제공 기능을 사용한다. 별도 프로세스·DB·Teamcenter API·인증정보 수집을 추가하지 않는다. 기존 ALM 스킬을 함께 사용한다.

## 5. 기능 요구사항

| 카테고리 | 이름 |
|---|---|
| PLMSKILL | PLM 스킬 안내와 교육 자료 |

### REQ-PLMSKILL-001 활용과 설치 안내

**하는 일** 스킬 관리의 PLM 카드는 `/static/plm-ai/index.html`의 상세 안내를 바로 연다. 설치·첫 질문·문제 해결·업데이트 문서와 ALM 병행 사용법을 제공한다.

**지킬 것** Codex·Claude Code용 Python 스킬과 웹 가이드를 구분한다. 제품 사양은 제품 스킬, ALM 서명과 PDF Export는 ALM 스킬에서 확인한다.

### REQ-PLMSKILL-002 현재 자료 판과 다운로드

**입력** `/static/plm-guide/current.json`과 해당 판의 `manifest.json`이다. 본문·원본 자료·서버 설정의 실제 값은 Git에 넣지 않는다.

**순서** 같은 출처의 영문·숫자·하이픈 경로와 manifest의 판 이름을 확인한다. 가이드 HTML·Word·PDF·스킬 ZIP 및 수량을 manifest에서 읽어 표시한다. 없는 파일은 해당 링크를 활성화하지 않는다. 등록된 글은 HTML로 실행하지 않는다.

**안 될 때** 조회 실패·경로 이탈·판 불일치에는 다운로드를 활성화하지 않고 다시 불러오기를 제공한다.

### REQ-PLMSKILL-003 읽기 도구와 스킬 패키지

**하는 일** `deploy/plm-skill/plm` 원본에서 지정 서버 주소의 ZIP을 만든다. 인증정보가 있는 주소·잘못된 기본 주소·기존 출력 덮어쓰기를 거절한다.

**순서** `server.json`의 catalog 또는 `PLM_KNOWLEDGE_URL`·`--base-url`을 사용한다. status에서 metadata 해시를 확인하고 같은 판을 고정해 검색·장·P 슬라이드·V01 영상·이미지를 조회한다. 잘린 본문은 offset으로 이어 읽는다.

**안 될 때** 해시 불일치·서버 오류에는 조회를 중단하고 알린다. 오래된 로컬 자료로 대신하지 않는다. 조회 도구는 Teamcenter 기록을 변경하지 않는다.

### REQ-PLMSKILL-004 브라우저 작업 경계와 자료 게시

**작업 경계** 구체적인 등록·수정·첨부·DHF 연결 요청에는 기존 로그인된 Edge·Chrome과 현재 Computer Use 도구를 사용한다. 서버·프로젝트·문서 ID·리비전·상태·변경값을 확인하고 저장 후 같은 객체를 다시 조회한다. 응답이 불명확하면 생성 여부부터 확인한다. 조사·준비 요청에는 쓰기를 추가하지 않는다.

**지킬 것** 전자서명·자가승인·최종 승인은 사용자가 직접 한다. WBS의 일반 완료는 구체적인 요청과 산출물·선행 상태를 확인한 범위에서 수행하고 같은 타스크의 결과를 다시 조회한다. 서명·결재가 요구되면 사용자에게 넘긴다. 쿠키·토큰·비밀번호를 추출하거나 새 프로필로 바꾸지 않는다. 샘플 번호와 결과를 실제 값으로 쓰지 않는다.

**업무 예시** 문서번호 등록·파일 첨부·ALM 승인 PDF·DHF 연결·개정·TC 저장 위치·WBS의 상세 요청과 결과 보고 형식을 스킬 MD 및 웹에 제공한다. 실행 중심의 공통 안내는 REQ-ALMSKILL-005를 따른다. 실제 제어 도구 연결과 권한이 없는 작업은 조건부로 안내하고 실행 성공으로 표시하지 않는다.

**자료 판** 최초 `20261105-v1`은 20개 장·P1~P20·V01·28개 화면 표본·Word 샘플 2개·가이드 Word와 PDF·스킬 ZIP을 포함한다. P9·P10·P12를 V01에 연결한다. 원본 노트·자료 차이·미확정 사항·미포함 PPT 2개를 표시한다.

**게시 순서** 모든 파일의 해시·크기를 대조한 뒤 준비 폴더를 새 판으로 바꾸고 current 목록을 원자적으로 교체한다. 원본은 비공개 자료 폴더에 보존한다. 업데이트 문서와 안내를 함께 게시한다.

**검증 범위** HTTP HEAD·영상 Range·health·파일 해시·CLI 조회와 1366·1920px 화면을 확인한다. 현재 운영 Teamcenter 쓰기와 자연어 자동 스킬 선택은 별도 검증이다. 영상은 10초 간격과 끝부분 표본이며 음성 전사와 모든 중간 동작 확인은 포함하지 않는다.

## 11. 테스트 사양

### TEST-PLMSKILL-001 안내와 자료 연결

실제 앱의 안내·CSS·JS와 목록·업데이트를 확인한다. 브라우저에서 합성 자료 판의 수량·링크 변경, 네트워크 실패 후 재시도, 경로 이탈·판 불일치·없는 파일·모바일 너비를 시험한다.

### TEST-PLMSKILL-002 패키지와 읽기 도구

ZIP 구성·서버 설정·덮어쓰기 및 주소 거절을 확인한다. 합성 HTTP 자료에서 P 슬라이드·영상·장·검색·이미지·페이지 나눔을 읽고 변조 파일이 거절되는지 확인한다.

### TEST-PLMSKILL-003 운영 게시 재조회

게시한 모든 파일의 해시·크기, HTTP HEAD·영상 Range·health, ZIP의 읽기 도구와 1366·1920px 화면을 확인한다. Teamcenter 쓰기는 수행하지 않는다.

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-PLMSKILL-001 | `app/web/static/plm-ai/index.html`, `app/web/static/plm-ai/style.css`, `app/web/static/skills/plm/updates.html`, `app/web/static/skills/index.html` | `tests/test_plm_skill_guide.py`, `tests/test_skill_management.py` | verified |
| REQ-PLMSKILL-002 | `app/web/static/plm-ai/guide.js` | `tests/test_plm_skill_guide.py` | verified |
| REQ-PLMSKILL-003 | `deploy/plm-skill/plm/SKILL.md`, `deploy/plm-skill/plm/scripts/knowledge.py`, `scripts/build_plm_skill.py` | `tests/test_plm_skill_package.py` | verified |
| REQ-PLMSKILL-004 | `deploy/plm-skill/plm/references/browser-operations.md` | `tests/test_plm_skill_package.py`, TEST-PLMSKILL-003 | implemented |

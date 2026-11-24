# QA 검증 관리 시스템 — QA 서버 장애 시 로컬 전환 기능 사양

<!-- spec-feature: v2 -->

| 항목 | 값 |
|---|---|
| 상위 사양 | [../SPEC.md](../SPEC.md) |
| 기능 ID | `qa-client-fallback` |
| Document Version | 0.1.0 |
| Last Updated | 2026-11-24 |
| Status | implemented |

이 문서는 `qa-verification-management-system`의 **QA 서버 장애 시 로컬 전환** 기능이 어떻게 동작해야 하는가를 정의한다.

공통 사양(시스템 구성·전체 흐름·비기능·데이터·설정·오류·보안)은 상위 `SPEC.md`에 있고 여기서 되풀이하지 않는다.
절 번호는 상위 문서와 같은 뜻이다. 빠진 번호(3·4·6~10)는 공통 사양이라 상위 문서에 있다.

파일 경로는 모두 프로젝트 루트 기준으로 적는다(`specs/` 기준이 아니다). 검사기도 루트 기준으로 확인한다.

## 1. 목적

QA 서버(10.13.0.222)가 멈춰도 담당자 PC의 Claude Code와 Codex가 ALM·PLM·VXvue·XIPL·Bellalun 업무를 로컬 자료로 이어 간다.
서버가 돌아오면 다음 작업부터 다시 서버 자료를 먼저 쓴다.

### 이 기능은

- QA 담당자가 Claude Code나 Codex에서 다섯 가지 Skill 중 하나로 작업을 시작할 때 쓴다.
- 서버 연결을 확인하고, 서버가 멈췄으면 PC의 지식 폴더·기존 로컬 도구·로그인된 브라우저로 바꿔 답한다. 답에는 사용한 자료의 판·수정일·빠진 자료를 함께 적는다.
- 인증 실패·권한 거부·자료 해시 불일치는 서버 장애로 보지 않는다. 이때는 바꾸지 않고 멈춘 뒤 사람에게 알린다.

### 이 기능에서 쓰는 말

| 용어 | 뜻 |
|---|---|
| 서버 장애 | 연결이 거부되거나, 응답이 제한 시간 안에 오지 않거나, 서버가 5xx 오류를 낸 상태 |
| 로컬 모드 | 서버 대신 PC의 지식 폴더와 기존 로컬 도구로 작업하는 상태 |
| 장애 확인(`probe`) | 서버 연결을 정해진 횟수만 다시 시도해 서버 장애인지 가리는 명령 |
| 덧붙임 블록 | 서버가 배포한 Skill 파일 끝에 붙이는 이 PC 전용 안내. `<!-- qa-server-fallback:begin -->`와 `<!-- qa-server-fallback:end -->` 사이의 글 |
| 공통 Skill | 다섯 Skill이 함께 읽는 `qa-server-fallback` Skill. 장애 판단 규칙과 로컬 명령을 담는다 |

## 2. 범위

### 포함

- 장애 확인 명령과 판정(재시도 횟수·제한 시간 상한 포함)
- VXvue·XIPL 로컬 자료 검색과 원문 조회(기존 Bellalun 검색 엔진 재사용, 제품별 폴더와 캐시 분리)
- Bellalun 로컬 Skill·로컬 MCP로의 전환 안내
- ALM·PLM은 교육자료 서버 장애가 운영 ALM·Teamcenter 작업을 막지 않도록 하는 안내
- 두 CLI에 공통 Skill 설치, 서버 Skill 끝에 덧붙임 블록 추가, 전역 지침에 한 블록 추가, 설치 상태 점검
- 서버 Skill 동기화(`scripts/sync_server_skills.py`)가 덧붙임 블록을 지우지 않게 하는 처리

### 제외

- QA 서버 자체의 복구나 이중화
- 서버 배포 Skill ZIP 내용 변경. 개인 경로는 서버 Skill에 넣지 않는다(REQ-BELLALUNSHARED-003).
- MCP 연결 설정·인증정보·Bellalun 연결 프로그램(`server.py`) 변경
- ALM·PLM 교육자료의 로컬 사본 보관(현재 설치본 지침이 오래된 로컬 사본 사용을 금지한다)

## 5. 기능 요구사항

| 카테고리 | 이름 | 설명 |
|---|---|---|
| QACLIENTFALLBACK | QA 서버 장애 시 로컬 전환 | 장애 판정, 로컬 자료 조회, 두 CLI 설치와 유지 |

### 요구사항 지도

- 작업 시작: 장애 확인(REQ-QACLIENTFALLBACK-001)
- 로컬 모드 작업: 자료 조회와 판 표시(REQ-QACLIENTFALLBACK-002), Skill별 이용 범위(REQ-QACLIENTFALLBACK-003)
- 설치와 유지: 두 CLI 설치(REQ-QACLIENTFALLBACK-004), 서버 동기화 뒤 보존(REQ-QACLIENTFALLBACK-005)

### REQ-QACLIENTFALLBACK-001 장애 확인과 모드 결정

#### 하는 일
새 작업마다 서버 연결을 확인하고 제품별로 서버·로컬·중단 중 하나를 정한다.

#### 언제
Skill의 서버 조회가 실패했거나 서버 도구가 보이지 않을 때 `qa_fallback.py probe`를 실행한다.

#### 순서
1. ALM·PLM 교육자료 목록(`current.json`)과 VXvue AI 연결 주소를 각각 확인한다.
2. 실패하면 1초 간격으로 다시 시도한다. 기본 3회, 시도당 5초, 전체 30초를 넘기지 않는다.
3. 결과를 아래 표로 판정하고 제품별 결정을 낸다.

| 관찰 | 판정 | 결정 |
|---|---|---|
| 정상 응답, 해시 일치 | `ok` | 서버 |
| 연결 거부·제한 시간 초과·주소 찾기 실패·5xx | `outage` | 로컬(묻지 않음) |
| 401·403·407 | `auth_error` | 중단 |
| PC 소켓 접근 권한 거부(PermissionError, Windows 10013) | `auth_error` | 중단 |
| 형식 오류·해시 불일치 | `integrity_error` | 중단 |
| 그 밖의 4xx | `server_error` | 중단 |

#### 결과
JSON으로 확인 시각, 대상별 판정·시도 횟수·걸린 시간, 제품별 결정을 낸다. 종료값은 모두 서버면 0, 로컬이 있으면 10, 중단이 있으면 20이다.

#### 설정
재시도 횟수는 최대 5회, 시도당 제한 시간은 최대 15초까지만 바꿀 수 있다.

#### 지킬 것
- 결정은 그 작업에만 쓴다. 다음 작업은 다시 확인하므로 서버가 돌아오면 서버를 먼저 쓴다.
- 한 작업 안에서는 처음 정한 모드와 그 자료 판만 쓴다. 작업 도중 서버가 돌아와도 서버 자료를 섞지 않는다.
- 사용자가 2026-11-24 요청으로 서버 장애 때 로컬 전환을 미리 승인했다. 장애 판정이면 다시 묻지 않는다.

### REQ-QACLIENTFALLBACK-002 로컬 자료 조회와 판 표시

#### 하는 일
VXvue·XIPL·Bellalun 로컬 자료를 검색하고 원문 위치를 근거로 돌려준다.

#### 순서
1. `local status --product <제품>`이 파일 목록·SHA256·수정일·파일명의 판 표기(날짜·버전)를 보여 준다.
2. 꼭 있어야 할 자료가 없으면 `missing`으로, 로컬에 원래 없는 자료는 `not_local`로 적는다.
3. `local search`·`get`·`page`·`revisions`는 기존 Bellalun 검색 엔진(`knowledge.py`)을 그대로 불러 쓴다.

#### 결과
원본 파일명과 PDF 페이지·DOCX 문단·XLSX 시트와 행을 담은 근거를 돌려준다.

#### 지킬 것
- 제품마다 지식 폴더와 캐시 폴더를 나눈다. Bellalun 캐시는 기존 위치를 그대로 쓴다.
- VXvue의 `_이전판/`처럼 지난 판을 모은 폴더는 검색에서 빼고 `excluded`로 보여 준다.
- XIPL은 지정한 매뉴얼 파일만 색인한다. 같은 폴더의 다른 프로젝트 파일은 넣지 않는다.

> **예시** XIPL.STUDIO 매뉴얼은 로컬에 없으므로 `not_local`에 적고, 답에서 "조회하지 못함"으로 밝힌다.

### REQ-QACLIENTFALLBACK-003 Skill별 로컬 이용 범위

#### 하는 일
각 Skill이 로컬 모드에서 할 수 있는 일과 못 하는 일을 정한다.

| Skill | 로컬 모드에서 하는 일 | 못 하는 일 |
|---|---|---|
| ALM | 개인 PAT 이슈 처리와 로그인 브라우저 작업을 계속한다 | 교육자료 장·슬라이드 근거. "교육자료 미확인"으로 적는다 |
| PLM | 로그인된 Teamcenter 브라우저 작업과 설치본 참조 문서를 쓴다 | 교육자료 근거. 최신 교육자료가 필요한 판단은 미확인으로 남긴다 |
| VXvue | 로컬 사양서·매뉴얼·TC 검색과 로컬 QA 지침 적용 | 서버 경유 Polarion·Redmine·Generator·매뉴얼 검토 도구 |
| XIPL | 로컬 XIPL 사용자 매뉴얼 검색 | XIPL.STUDIO 문서와 서버 개정 이력 |
| Bellalun | 기존 로컬 Skill과 로컬 MCP, 로컬 Redmine 조회 | 서버 경유 Polarion 조회 |

#### 지킬 것
- 로컬 지침으로 확인할 수 없는 절차는 추측하지 않는다.
- XIPL 공용 기능 설명과 VXvue·Bellalun의 제품별 지원·라이선스 조건을 나눠 적는다.

### REQ-QACLIENTFALLBACK-004 두 CLI 설치와 점검

#### 하는 일
공통 Skill을 Codex 폴더에 설치하고 Claude 폴더에는 연결(Junction)을 만든다. 서버 Skill에 덧붙임 블록을, 전역 지침에 안내 블록을 넣는다.

#### 순서
1. 바꿀 파일을 모두 백업한다.
2. 공통 Skill을 복사하고 이 PC의 설정 파일을 만든다.
3. 서버 Skill 6종(`alm`·`plm`·`vxvue-ai`·`bellalun-server-ai`·`bellalun-ai`·`bellalun-local-knowledge`) 끝에 덧붙임 블록을 넣는다. 이미 있으면 내용만 맞춘다.
4. `~/.claude/CLAUDE.md`와 `~/.codex/AGENTS.md`에 같은 안내 블록을 넣는다.
5. 다시 읽어 두 CLI의 Skill·블록·안내가 같은지 확인한다.

#### 결과
`check` 명령이 두 CLI의 파일 일치, 덧붙임 블록, Skill 이름·설치 표시 보존 여부를 보고한다.

#### 지킬 것
- 여러 번 실행해도 결과가 같다.
- Skill의 `name`, 서버 설치 표시 주석, 기존 본문, Junction 구조는 바꾸지 않는다.
- 장애 확인 때 덧붙임 블록이 사라졌으면 다시 넣는다(백업 후).

### REQ-QACLIENTFALLBACK-005 서버 Skill 동기화 뒤 보존

#### 하는 일
매일 서버 Skill을 받는 동기화가 덧붙임 블록을 지우지 않는다.

#### 순서
1. 설치본과 서버본을 비교할 때 덧붙임 블록을 빼고 비교한다.
2. 서버본이 바뀌어 새로 쓸 때는 기존 덧붙임 블록을 끝에 다시 붙인다.

#### 결과
덧붙임 블록만 다르면 `UNCHANGED`로 본다.

## 11. 테스트 사양

### TEST-QACLIENTFALLBACK-001 장애 판정

#### 검증 대상
REQ-QACLIENTFALLBACK-001

#### 절차
127.0.0.1에 실제 HTTP 서버를 띄워 정상·503·401·해시 불일치·늦은 응답을 만들고, 닫힌 포트도 확인한다. 정상으로 돌아온 다음 확인도 본다.

#### Expected Result
판정과 결정이 표와 같고, 재시도 횟수와 전체 시간이 상한을 넘지 않는다. 복구 뒤 결정은 서버다.

### TEST-QACLIENTFALLBACK-002 로컬 자료

#### 검증 대상
REQ-QACLIENTFALLBACK-002

#### 절차
합성 MD·DOCX와 `_이전판/` 폴더로 검색·원문·제외·누락·캐시 분리를 확인한다.

#### Expected Result
현재 판만 검색되고, 근거에 파일과 위치가 있으며, 없는 자료는 `missing`으로 보인다.

### TEST-QACLIENTFALLBACK-003 설치·보존

#### 검증 대상
REQ-QACLIENTFALLBACK-003, REQ-QACLIENTFALLBACK-004, REQ-QACLIENTFALLBACK-005

#### 절차
임시 홈에 두 CLI 구조를 만들고 설치를 두 번 실행한다. 동기화로 서버본이 바뀌는 경우와 블록만 다른 경우를 실행한다.

#### Expected Result
백업이 생기고, 이름·설치 표시가 그대로이며, 블록이 하나만 있고, 동기화 뒤에도 남는다.

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-QACLIENTFALLBACK-001 | `client/qa-server-fallback/scripts/qa_fallback.py` | `tests/test_qa_server_fallback.py` | verified |
| REQ-QACLIENTFALLBACK-002 | `client/qa-server-fallback/scripts/qa_fallback.py` | `tests/test_qa_server_fallback.py` | verified |
| REQ-QACLIENTFALLBACK-003 | `client/qa-server-fallback/SKILL.md`, `client/qa-server-fallback/overlays.json` | `tests/test_qa_server_fallback.py` | verified |
| REQ-QACLIENTFALLBACK-004 | `client/qa-server-fallback/scripts/qa_fallback.py` | `tests/test_qa_server_fallback.py` | verified |
| REQ-QACLIENTFALLBACK-005 | `scripts/sync_server_skills.py` | `tests/test_sync_server_skills.py` | verified |

Status 값: `draft` (사양만 있음) / `implemented` / `verified` (게이트가 실행하는 자동 테스트로 확인) /
`deprecated`. 수동 절차만 있으면 `implemented`로 둔다.

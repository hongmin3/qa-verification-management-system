# QA 검증 관리 시스템 — VXvue 활용 이력과 제작자 지표

<!-- spec-feature: v2 -->

| 항목 | 값 |
|---|---|
| 상위 사양 | [../SPEC.md](../SPEC.md) |
| 기능 ID | `vxvue-usage` |
| Document Version | 0.1.0 |
| Last Updated | 2026-10-27 |
| Status | verified |

목적과 수집 범위를 먼저 읽고, 5절에서 제작자 인증·이력·통계·내보내기를 확인한다. 공통 설정은 상위 사양을 따른다.

## 1. 목적

제작자는 선택한 본인 업무의 질문·최종 답변·활용 요약을 최근 365일 동안 찾아보고 월별 사용과 대표 사례를 검토한다. 팀 도구 실행은 본문 없는 익명 메타데이터로만 집계한다.

### 이 기능은

- 일반 사용자는 기존 VXvue 도구와 웹 채팅을 그대로 사용한다. 도구 실행 결과와 대화에 지표·기록 안내를 추가하지 않는다.
- 제작자는 별도 주소에서 로그인해 본인 원문·통계·대표 사례를 본다. 공개 화면 메뉴에는 이 주소를 추가하지 않는다.
- 실행 성공·답변 저장·사용자 활용 평가는 서로 다른 값이다. 도구 성공을 정확성이나 업무 완료로 표시하지 않는다.

| 용어 | 뜻 |
|---|---|
| 업무 | 서버가 발급한 업무 UUID 또는 실제 웹 작업에 연결된 단위 |
| 호출 | 도구 실행 시도 한 번. 업무 ID가 없으면 호출로만 센다 |
| 활성 이용 연결 | 월별로 가명 처리한 연결. 사람 수가 아니다 |
| 원문 선택 저장 | 제작자가 선택한 업무의 질문·답변을 별도 이력에 보관하는 것 |

## 2. 범위

동일 핵심 앱의 SQLite·MCP·웹 채팅 실행 경로와 제작자 전용 화면을 포함한다. 팀 질문·답변·검색어·도구 인자·문서 본문 수집, 로컬 전체 대화 수집, 과거 자료 자동 이관, 일반 사용자 로그인 요구는 제외한다.

## 5. 기능 요구사항

| 카테고리 | 이름 |
|---|---|
| VXVUEUSAGE | 활용 이력·익명 실행 지표·제작자 조회 |

### REQ-VXVUEUSAGE-001 본문 없는 도구 실행 이벤트

도구 호출 경계에서 이벤트 UUID·시각·고정된 도구/기능/채널/근거 종류·성공 여부·지연·알려진 재시도 횟수·월별 가명 연결만 저장한다. 질문·답변·검색어·인자·문서명·IP·PC 계정·사용자명·비밀값은 이벤트에 넣지 않는다. 알 수 없는 값은 null 또는 고정된 unknown이다. 가명 처리용 전용 서명 키가 없으면 계측하지 않는다.

실제 업무 ID가 없으면 업무 수로 세지 않는다. 같은 이벤트 UUID의 재전송은 한 번만 반영하고, 재시도는 별도 시도 값으로 구분한다. 진단과 시험 이벤트는 기본 업무 활용 집계에서 분리한다.

### REQ-VXVUEUSAGE-002 제작자 기기만 본인 기록

보호된 전송 경로와 서버에 등록된 제작자 기기의 인증 결과로만 본인 기록 도구와 추가 지침을 제공한다. 공용 팀 토큰은 제작자가 될 수 없다. 사용자가 전달한 owner_id·기기 번호로 권한을 얻지 못한다. 다른 사용자의 도구 목록·작업 지침에는 기록 도구와 지표 지침을 추가하지 않는다. 공용 지식 판과 지침 캐시는 수정하지 않는다.

업무 시작 시 UUID를 발급한다. 원문 저장을 선택한 경우에만 질문·최종 답변을 보관한다. 완료 누락은 진행 중이며, 답변 저장·미완료·실패·취소를 구분한다. 사용자는 활용 요약·평가·대표 사례를 따로 남길 수 있다.

같은 업무의 시작 요청을 다시 보내도 기존 원문과 종료 상태를 유지하고 현재 저장된 상태를 반환한다.

### REQ-VXVUEUSAGE-003 제작자 인증과 자료 접근 제한

별도 username·PBKDF2 비밀번호 해시·기기 목록·32바이트 이상 서명 키를 비밀정보로 설정한다. 기본값은 비어 있다. 설정 미완료 때 화면은 준비 필요만 안내하고 자료 API는 403이다.

비밀번호 평문을 저장하지 않는다. 세션은 서명·만료·HttpOnly·SameSite Strict 쿠키로 보호한다. 로그인 실패 빈도를 제한하고 모든 쓰기에 CSRF와 같은 출처 검사를 적용한다. 원문 API는 로그인을 요구하며 다른 소유자의 원문을 조회하거나 위조해 기록하지 못한다.

### REQ-VXVUEUSAGE-004 기존 웹 대화의 선택 연결

기존 웹 채팅 저장·삭제 정책은 유지한다. 제작자 로그인과 현재 브라우저의 기존 대화 소유 쿠키를 함께 확인한 뒤 CSRF 보호 요청으로 연결한다. 제작자 쿠키는 /vxvue-usage 범위다. 로그인·CSRF 확인 후 발급한 60초 일회용 티켓을 헤더로 기존 채팅 경로에 보내 현재 브라우저 소유를 확인하고, 제작자 경로의 서명된 소유 증명 쿠키로 연결한다. 티켓은 URL에 넣지 않는다. 선택한 본인 웹 작업만 별도 이력에 가져온다. 임의 작업 번호나 다른 브라우저의 대화를 열람하지 못한다. 과거 대화를 자동 복사하지 않는다.

웹 도구 실행은 실제 작업 UUID에 연결해 본문 없는 이벤트를 남긴다. 웹 응답 생성 완료는 품질이나 사용자가 활용한 업무 완료를 뜻하지 않는다.

### REQ-VXVUEUSAGE-005 숨겨진 제작자 조회 화면

`/vxvue-usage`에서 로그인·내 이력 검색·기능/상태 필터·원문 상세·대표 사례 책갈피·활용 평가를 제공한다. 질문과 답변은 HTML로 실행하지 않고 글로 표시한다. 원문 검색어는 CSRF 보호 POST JSON으로 보내며 URL에 넣지 않는다. 일반 탐색 메뉴·일반 도구 응답·터미널 출력에 지표나 기록 안내를 추가하지 않는다.

### REQ-VXVUEUSAGE-006 사실에 맞는 월별 지표

저장 시각은 UTC ISO이며 월·가명 연결 교체·업무 묶음과 화면의 월은 한국 시간(UTC+9) 기준이다. 월별 기능·채널·실패·지연·근거 종류를 보여준다. 업무 수·호출 수·재시도·진단을 구분한다.

외부 AI의 토큰·비용을 알 수 없으면 null이다. 입력한 시간 표본의 기준 시간·실제 시간·차이를 표시하고 자동 절약 효과로 단정하지 않는다. 월별 가명 연결은 사람 수로 표시하지 않는다.

익명 웹 업무는 원래 작업 번호로 되짚을 수 없는 UUID와 업무별 연결값만 남긴다. 웹 작업이 시작한 달을 넘으면 다음 달 이벤트의 연결 가명은 null로 두고 연결 미확인 호출 수를 표시한다. 월별 호출 수는 계속 한국 시간으로 집계한다.

### REQ-VXVUEUSAGE-007 익명화 미리보기와 CSV

대표 사례와 월 집계를 미리 본 뒤 CSV로 내보낸다. 원문·사용자/연결 식별자·업무 UUID·검색어·문서명·경로는 내보내지 않는다. 사례 설명은 고정된 기능·상태·평가에서 만들고 원문에서 자유 문장을 복사하지 않는다.

제작자가 별도로 작성한 공개용 한 줄(portfolio_note)은 기본 내보내기에 넣지 않는다. 명시적으로 선택하면 경로·주소·키·메일 마스킹 미리보기 후 포함할 수 있다. 전체 개인정보 익명화를 보장하지 않으므로 공개 전 제작자가 검토한다.

CSV 수식 시작 문자는 글로 처리한다. 미리보기와 내려받는 열·행은 같다.

### REQ-VXVUEUSAGE-008 새 자료의 별도 보관 기간

앱 시작과 매일 03:10 한국 시간에 신규 자료만 정리한다. 새 백업 스냅샷을 만들기 전에도 만료된 신규 자료를 정리한다. 신규 이력·이벤트·본인이 연결한 웹 소유 증명은 365일, 식별자 없는 월 집계는 최근 36개월을 보관한다. 반복 정리로 중복 집계하지 않는다. 정리는 vxusage 자료에만 적용하며 기존 vxchat 대화·메시지·첨부와 다른 업무 자료는 지우지 않는다. 과거 자료 자동 이관은 하지 않는다.

### REQ-VXVUEUSAGE-009 계측 실패의 영향 제한

SQLite 연결은 짧은 잠금 대기로 제한한다. 계측 실패는 서버 로그의 일반 문구와 제작자 상태 확인에만 남기고 정상 도구의 응답·오류·터미널 출력에 덧붙이지 않는다. 오류 로그에는 원문·인자·응답·개인정보를 넣지 않는다. 상태 확인의 누락 수는 해당 실행 프로세스의 범위임을 표시한다.

### REQ-VXVUEUSAGE-010 본인 원문용 보호 연결

제작자 로그인·원문 조회/기록·웹 선택 연결은 HTTPS 또는 본인 PC의 SSH loopback 연결에서만 제공한다. 일반 팀의 기존 HTTP QA 연결과 익명 호출 계측은 유지한다. 보호되지 않은 연결의 본인 기기도 업무 UUID·재시도 지정·열린 업무 연결을 사용하지 않고 호출 통계만 남긴다.

본인 PC 도구는 SSH 로컬 포트 24359에서 서버 loopback 24357로 연결하고 개인 stdio 연결에는 시작·종료 도구 두 개만 제공한다. 기존 QA 클라이언트 주소·기능을 바꾸지 않는다. 기기 키·접속 설정은 본인 로컬 비공개 파일에서 읽고 URL·응답·로그에 키를 넣지 않는다. 보호 연결의 준비·기기 권한 확인이 실패하면 원문을 보내지 않는다.

원문을 보내기 직전에 같은 SSH 연결과 기기 토큰으로 서버 도구 목록을 조회해 본인 기록 도구 두 개가 있는지 확인한다. 목록 조회 실패·권한 거절·일반 팀 도구 목록이면 원문 요청을 보내지 않는다. 확인 후 권한이 바뀔 수 있으므로 서버는 원문 요청을 받을 때 권한을 다시 확인하고, 취소된 기기의 요청을 기록하지 않는다. 사전 확인이 이후 권한 변경까지 막아 주는 것은 아니다.

서버는 실제 연결 상대가 loopback일 때만 단일 X-Forwarded-Proto=https를 신뢰한다. HTTP loopback 예외는 Host가 localhost·127.0.0.1·::1이고 전달된 사용자 주소 헤더가 없을 때만 허용한다. 다른 PC가 Host나 전달 헤더를 위조해 권한을 얻지 못한다.

POSIX 서버의 data·백업 폴더는 0700, DB·새 백업 ZIP은 0600으로 보호한다. SQLite 부속 파일은 0600을 시도하되 0700 부모 폴더 경계가 유효하면 재생성된 부속 파일 모드만으로 기록을 차단하지 않는다. Windows의 파일 접근 제어는 별도 운영 검증 대상으로 두고 POSIX 모드 적용과 혼동하지 않는다.

## 11. 테스트 사양

### TEST-VXVUEUSAGE-001 개인정보와 실행 계수

합성 민감 문자열이 이벤트 DB에 없는지 확인한다. 한 업무·다섯 호출·두 재시도·동일 이벤트 재전송·진단·시험을 넣어 업무와 호출을 구분한다. 미완료·실패와 알려지지 않은 비용을 확인한다.

### TEST-VXVUEUSAGE-002 권한·원문·전달 경계

빈 설정·비밀번호 오류·만료/위조 세션·CSRF·다른 기기·공용 토큰·다른 웹 소유자의 요청을 거절한다. 제작자와 팀의 도구 목록·지침을 번갈아 읽어 공용 캐시 오염이 없는지 확인한다. 계측 DB 잠금/실패 후 도구 결과가 같은지 확인한다.

### TEST-VXVUEUSAGE-003 화면·내보내기·보관

합성 원문으로 로그인부터 저장·검색·평가·책갈피·CSV 미리보기·다운로드까지 실제 화면 없이 브라우저에서 확인한다. 원문 HTML 실행·CSV 수식을 막고 내보낸 자료에 민감 필드가 없는지 확인한다. 새 자료만 정리되고 기존 대화가 유지되는지 확인한다. 유료 AI는 호출하지 않는다.

## 12. 요구사항 추적성

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-VXVUEUSAGE-001 | `app/core/vxusage_storage.py`, `app/modules/vxvue_usage/telemetry.py` | `tests/test_vxvue_usage.py` | verified |
| REQ-VXVUEUSAGE-002 | `app/modules/vxvue_usage/service.py`, `app/modules/vxvue_chatbot/desktop.py` | `tests/test_vxvue_usage.py` | verified |
| REQ-VXVUEUSAGE-003 | `app/modules/vxvue_usage/auth.py`, `app/modules/vxvue_usage/router.py` | `tests/test_vxvue_usage_auth.py` | verified |
| REQ-VXVUEUSAGE-004 | `app/modules/vxvue_usage/router.py`, `app/modules/vxvue_chatbot/router.py` | `tests/test_vxvue_usage_auth.py` | verified |
| REQ-VXVUEUSAGE-005 | `app/modules/vxvue_usage/templates/usage.html`, `app/modules/vxvue_usage/static/usage.js` | `tests/test_vxvue_usage_browser.py` | verified |
| REQ-VXVUEUSAGE-006 | `app/core/vxusage_storage.py`, `app/modules/vxvue_usage/router.py` | `tests/test_vxvue_usage.py` | verified |
| REQ-VXVUEUSAGE-007 | `app/modules/vxvue_usage/export.py` | `tests/test_vxvue_usage_auth.py` | verified |
| REQ-VXVUEUSAGE-008 | `app/core/vxusage_storage.py`, `app/modules/vxvue_usage/scheduled_jobs.py` | `tests/test_vxvue_usage.py` | verified |
| REQ-VXVUEUSAGE-009 | `app/modules/vxvue_usage/telemetry.py` | `tests/test_vxvue_usage.py` | verified |
| REQ-VXVUEUSAGE-010 | `app/modules/vxvue_usage/auth.py`, `app/core/private_files.py`, `scripts/backup_data.py`, `scripts/vxvue_usage_local.py` | `tests/test_vxvue_usage_auth.py`, `tests/test_backup.py`, `tests/test_vxvue_usage_local.py` | verified |

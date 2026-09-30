# 변경 이력

## 2026-09-30

### 화면 기능의 AI 판정을 Claude CLI 로 전환

- REQ-AICALL-005: Regression 영향 분석, QA Agent, 매뉴얼 개정 검증이 기본으로 Claude CLI(`claude -p`)에서 판정을 받는다. 응답 형식은 `--json-schema` 로 고정한다. 도구는 하나도 주지 않고, 저장소 밖 빈 폴더(`~/.qa-ai-workspace`)에서 사용자 설정과 MCP 없이 실행한다. `config.yaml` 의 `ai.provider: gemini` 로 바꾸면 예전처럼 Gemini API 를 쓴다.
- REQ-AICALL-001: 공통 통로가 `ai.provider` 로 AI 를 고른다. 가리기, 응답 저장본, 토큰 합산, 하루 한도는 두 제공자에 똑같이 적용한다. 감사 기록에 AI 제공자를 남긴다.
- REQ-AICALL-003: Claude CLI 실패는 다시 시도하지 않는다. 대신 쓸 모델과 추론 켜기는 Gemini 에만 한다.
- REQ-AICALL-004: 모델 등급별 모델 이름을 제공자별로 읽는다. Claude 기본값은 세 등급 모두 `claude-opus-5-5` 다(`ai.claude.models.*`).
- REQ-USAGE-003: Claude 사용량 한도 도달과 CLI 인증 실패를 알림 메일 종류로 더했다.
- REQ-USAGE-004: Claude 모델의 API 기준 단가를 비교용 추정 단가로 더했다.
- REQ-IMPACT-021: 분석 화면 위쪽 표시줄과 `/config/status` 가 쓰는 AI, CLI 를 찾았는지, 인증 방식, 모델을 보인다. 하루 한도 초과 문구와 감사·대시보드 화면의 "Gemini" 표기를 "AI" 로 바꿨다.
- 일일 QA 점검의 Claude 실행 환경 만들기 코드를 공통 모듈(`app/core/claude_cli.py`)과 같이 쓴다. 동작은 같다.

### HTML 사양서 가로 넘침 개선

- 키트 렌더러 v7을 적용했다. 본문 폭을 넓히고 긴 표·경로·명령을 줄바꿈한다. 카드의 구현·테스트 정보는 줄별로 구분하고 작은 화면의 넓은 표는 항목 이름과 함께 세로로 표시한다. 사양 본문과 기능 동작은 유지했다.

### 자동화 하루 일정과 사양서 읽는 순서 정리

- REQ-SCHED-002, REQ-SYNC-001, REQ-SYNC-002, REQ-DAILY-008, REQ-OPS-002, REQ-HUBOPS-009: SPEC 4절에 예약 시각, 실행 위치, 결과 저장 위치와 알림 경로를 정리했다. 서버 cron 시각은 서버 시간대 기준으로 구분했다.
- 요구사항 카드의 표현과 기능별 소개·용어 표를 정리했다. 요구사항 ID, 설정값, 오류 문구, 테스트 사양과 12절 추적성은 유지했다. 운영 설치 상태는 progress.md 에서 확인하도록 연결했다. 기능 동작은 바뀌지 않았다.

### 사양서에 한눈에 보기 흐름도 추가 · 키트 작업 규칙 v8

- SPEC 1절에 "한눈에 보기" 흐름도를 더했다. 화면 요청, 평일 아침 일일 QA 점검, 담당자 PC 동기화가 각각 어디서 시작해 무엇을 남기는지, 실패하면 어디로 가는지를 한 그림으로 보인다.
- 키트 migration 으로 `AGENTS.md` 의 사양 작업 규칙과 `.project-check/` 사본을 v8 로 바꿨다. 기능 동작은 바뀌지 않았다.

### 사양–코드 불일치 60건 수정 · 결정 대기 18건 반영

- `OPEN_QUESTIONS.md` 8절의 18건을 모두 추천안대로 정해 코드와 SPEC 에 반영했다. `docs/SPEC_CODE_MISMATCH.md` 65건 가운데 60건을 고치고 1건을 일부 고쳤다. 남은 4건은 그 문서에 `해결` 줄 없이 남아 있다.

공통 기반:

- REQ-USAGE-002: 하루 토큰 한도의 "오늘"을 한국 시간 0시부터 센다. 도중에 실패한 분석이 쓴 토큰도 합계에 넣는다. Regression 분석·재실행, QA Agent, 매뉴얼 개정 검증이 같은 계산(`app/core/usage.py`)을 쓴다.
- REQ-COST-001: 비용 대시보드의 날짜와 시각을 한국 시간으로 보인다. QA Agent 를 기능 이름으로 보이고, 최근 분석 표의 열 이름을 "시작 시각"으로 고쳤다. 실패한 분석은 "실패"로 표시한다.
- REQ-COST-004: 사용법 화면의 "분석 1건당 1회" 설명을 기능별 호출 횟수에 맞게 고쳤다.
- REQ-AICALL-003: 네트워크 오류 재시도 횟수와 대기 시간을 `config.yaml` 의 `analysis.max_retries`·`retry_min_seconds`·`retry_max_seconds` 에서 읽는다.
- REQ-AICALL-002: 다른 모델로 대신 받은 답을 저장본에서 다시 쓸 때도 실제로 답한 모델이 감사 기록에 남는다.
- REQ-CONF-001: 쓰이지 않던 설정 `storage.database_url`·`app.locale` 을 지웠다.
- REQ-OPS-002: 백업에 일일 QA 스냅샷을 넣는다. 새 백업을 확인한 뒤 보관 일수(기본 30일)가 지난 백업을 지운다. 가장 새 백업 하나는 늘 남긴다.
- NFR-DAILY-002: 보관 일수가 지난 일일 QA 실행 폴더와 AI 응답 저장본을 백업 작업이 지운다. 일수는 `config.yaml` 의 `retention` 에서 바꾼다.
- REQ-SYNC-001: 사양서 동기화가 일부만 성공하면(PARTIAL) 종료 코드 1 로 끝난다.
- REQ-SYNC-002: 담당자 PC 가 읽지 못한 새 판도 서버로 보내 옛 판이 지워지지 않게 했다. PC 수집 일부 실패도 PARTIAL 로 끝난다. 업로드 결과에 이전 판을 유지한 문서와 읽지 못한 파일을 한 줄씩 보인다.
- REQ-KNOW-007: 이름이 같은 파일의 내용이 바뀌면 저장해 둔 파싱 결과를 버리고 다시 읽는다.
- REQ-KNOW-012: 지식 폴더에서 빠져 서버가 지운 문서의 등록도 함께 지운다.
- REQ-KNOW-016: 앱이 다시 시작할 때 끊긴 동기화 기록을 실패로 닫아 다음 수집이 막히지 않는다.
- REQ-KNOW-001: Knowledge 화면 스크립트의 문법 오류를 고쳐 삭제 확인 창, `지금 수집` 알림, `죽은 등록 정리`, 제품 필터가 다시 동작한다. 사양서 동기화 시각 안내를 평일 09:40 으로, 업로드 명령 안내를 `--upload-to` 로 고쳤다.
- REQ-KNOW-003: 수동 등록 양식 이름을 "사양서 등록"으로 바꿨다. 매뉴얼은 지식 폴더로 올린다.

Regression 영향 분석:

- REQ-IMPACT-005: 읽지 못한 사양서·TC 파일을 "사용한 문서"에서 빼고, 보고서 첫 부분과 분석 상세 화면에 "읽지 못한 문서"로 보여 준다.
- REQ-IMPACT-006: 영어로 쓴 변경 문서에서도 change, add, fix 같은 낱말이 있는 줄을 변경 기능으로 뽑는다.
- REQ-IMPACT-010: AI에게 보내지 않은 TC에 대한 판정은 버린다. 추천 여부는 AI 값을 그대로 쓰기로 정하고 모듈 문서의 "0.80 이상은 추천" 문장을 고쳤다.
- REQ-IMPACT-011: 삭제된 사양(취소선)이 근거인 판정은 검토 상태도 "사람 확인 필요"로 바꾼다.
- REQ-IMPACT-017: 분석 상세 화면의 TC 후보 수 설명이 설정값을 그대로 보인다.
- REQ-IMPACT-018: "같은 입력으로 재실행" 버튼을 누르면 새 분석 화면으로 이동한다. 실행하지 못하면 그 이유를 화면에 보인다.
- NFR-IMPACT-001: 재실행도 하루 토큰 한도를 검사한다.
- REQ-IMPACT-020: 사용법 화면의 진행 단계 목록에 빠져 있던 "신규 TC 초안 검증"을 넣어 실제 8단계와 맞췄다.

QA Agent:

- REQ-ISSUE-004: 연구소 검토 결과로 유형이 정해지지 않는 이슈는 분석 양식의 "이슈 유형"에서 QA 가 골라 다시 실행할 수 있다.
- REQ-QAAGENT-005: Test Data 준비 여부를 "모름"으로 두면 G4 가 확인을 요청한다.
- REQ-QAAGENT-005: 여러 권 사양서 안내 문장을 "근거가 사양서 N건 중 M건에서만 나왔습니다"로 고쳤다. 전체를 검색했는데 일부만 조사했다고 적던 문장이다.
- REQ-QAAGENT-003: 읽지 못한 사양서·TC 를 G2 항목과 결과 화면 위 목록에 보인다. 이 문서들은 준비된 문서 수에서도 뺀다.
- REQ-QAAGENT-010: 사양대로·문서 수정·사양 문의 유형 이슈에 AI 가 신규 TC 를 제안하면 G5 가 주의 항목으로 남긴다.
- REQ-QAAGENT-012: QA 결정을 다시 적어도 이전 결정이 기록으로 남는다. 기록한 뒤에는 보던 탭으로 돌아온다.
- REQ-QAAGENT-001: Export 폴더가 없는 서버에서는 번호 입력칸을 없애고 `backup.json` 첨부를 안내한다. 번호만 보내면 분석을 시작하기 전에 거절한다. 폴더 목록의 이름을 HTML 이스케이프한다.
- REQ-QAAGENT-014: 사용법 화면에 조사 범위 글이 검색 대상을 줄이지 않는다는 것과 이슈 유형 선택 방법을 적었다.

매뉴얼 개정 검증:

- REQ-MANUAL-002: 오늘 쓴 토큰이 하루 한도를 넘으면 매뉴얼 개정 검증을 시작하지 않는다.
- REQ-MANUAL-002: 거절된 검증 요청의 제품 버전이 버전 목록에 남던 결함을 고쳤다.
- REQ-MANUAL-002: 참고 문서를 올리지 않으면 같은 제품 버전 문서만 다시 쓴다. 없으면 Release 대조를 건너뛴다.
- REQ-MANUAL-005: 서식 변경은 뽑지 않기로 정하고 모듈 문서의 "서식 변경" 표현을 지웠다.
- REQ-MANUAL-012: 이미지 변경을 AI가 문제없음으로 판정하면 판정 불가로 바꾸고 Word Comment에도 넣는다.
- REQ-MANUAL-014: QA 판정 변경에서 목록에 없는 값을 거절한다.
- REQ-MANUAL-015: Word Comment 파일을 만들 때 넣은 Comment를 다음 회차 지적사항으로 저장한다.
- REQ-MANUAL-016: Word Comment 파일 이름에 가운뎃점이 남던 결함을 고쳤다.
- REQ-MANUAL-017: 사용법 화면을 지금 동작에 맞추고 Claude 대화로 돌리는 방법을 더했다.
- REQ-MANUAL-018: Claude Code 대화에서 "매뉴얼 분석해 줘"로 개정 매뉴얼을 검증하는 도구와 작업 설명서를 더했다.
- REQ-MANUAL-018: 작업 설명서의 실행 명령에 `PYTHONIOENCODING=utf-8` 을 붙였다. 도구는 Windows 콘솔용 cp949 로 출력해서, Claude 가 읽으면 `결과 폴더:` 줄의 한글이 깨졌다.

VXvue 일일 QA 점검:

- REQ-DAILY-001: Polarion 조회가 실패한 날에는 사양 변경 영향 검토와 이슈 수정확인 초안이 "건너뜀" 대신 "실패"로 보인다.
- REQ-DAILY-001: Claude 토큰이 없어도 예약 점검이 돈다. AI 단계만 건너뛰고, 이유가 메일에 남는다.
- REQ-DAILY-003: 작업 상한 때문에 미뤘거나 실패한 사양 변경을 다음 실행에서 다시 검토한다.
- REQ-DAILY-002: 이슈 수정확인 초안 작업이 일부 실패한 날은 이슈 기준 시각을 옮기지 않는다. 실패한 이슈는 다음 날 다시 읽고, 이미 처리한 이슈는 다시 보내지 않는다.
- REQ-DAILY-018: 같은 SRS 를 가리키는 TC 가 여럿이면 TC 마다 Finding 을 남긴다.
- REQ-DAILY-007: "닫기 버튼", "Closed 상태"처럼 화면 이름에 닫기가 들어간 조치는 더 이상 버리지 않는다. "덮어쓴다"처럼 전에 못 잡던 금지 조치를 잡는다.
- REQ-DAILY-016: 작업이 실패하면 단계 비고와 메일에 첫 실패 이유(예: 사용량 한도)가 한 줄로 붙는다.
- REQ-DAILY-013: 시험 실행은 SRS 스냅샷과 Finding 을 저장하지 않는다. 시험 실행 다음 날의 정식 실행도 그날 변경을 빠짐없이 본다.
- NFR-SEC-001: Claude 는 작업 폴더 안의 파일만 읽고 검색할 수 있다. 서버의 다른 파일을 읽으려 하면 거부된다.

QA Manual Hub:

- REQ-HUBAUTH-003: 화면을 계속 쓰면 브라우저 쿠키도 함께 연장된다. 이전에는 로그인 8시간 뒤 쓰는 중에도 로그인 화면으로 돌아갔다.
- REQ-HUB-016: API 문서 화면(`/api/docs`, `/api/openapi.json`)은 로그인한 사람만 볼 수 있다.
- REQ-HUBAUTH-013: 감사 기록과 로그인 이력의 IP 는 nginx 가 넣은 값만 쓴다. 사용자가 머리글을 직접 넣어 IP 를 바꿀 수 없다.
- REQ-HUBAUTH-013: CLI `seed-catalog` 로 만든 분류와 제품도 Audit Logs 에 생성 기록이 남는다.
- REQ-HUBAUTH-006: 임시 비밀번호 상태의 Admin 은 비밀번호를 바꾸기 전까지 사용자·제품·분류도 관리할 수 없다.
- REQ-HUBOPS-010: 복구 스크립트는 백업 파일이 manifest 의 SHA-256 과 맞지 않거나 안전 백업이 실패하면 데이터를 건드리기 전에 멈춘다.
- REQ-HUBOPS-009: 백업과 복구가 `.env` 의 `STORAGE_ROOT` 를 따른다. 저장소를 다른 곳으로 옮겨도 문서 파일이 백업에서 빠지지 않는다.
- REQ-HUBOPS-009: `install.sh` 가 매일 02:30 자동 백업 파일(`/etc/cron.d/qa-manual-hub-backup`)을 만든다. 이미 있으면 그대로 둔다.
- REQ-HUBOPS-001: `install.sh` 를 다시 실행해도 이미 있는 DB 계정의 비밀번호를 바꾸지 않는다. 바꾸려면 `RESET_DB_PASSWORD=1` 을 붙인다.
- REQ-HUBOPS-006: `qamh check-storage --verify-sha256` 으로 크기는 같고 내용이 손상된 파일도 찾는다.
- REQ-HUBOPS-012: Docker Compose 가 가리키던 없는 파일(`deploy/Dockerfile`, `deploy/nginx/docker.conf`)을 만들고, 비밀번호 최소 길이 기본값을 다른 설치 방식과 같은 1로 맞췄다.
- REQ-HUBDATA-013: 업로드 창의 Revision Date 는 파일명에 날짜가 없으면 빈칸으로 둔다. 업로드한 날이 개정일로 저장되지 않는다.

문서:

- 서버 포트(24357)와 지식 폴더 업로드 일정(평일 10:00)을 `knowledge/core-deployment.md`, `docs/PRODUCT_ONBOARDING.md`, `docs/DEPLOYMENT.md` 에 맞췄다. 이번 구현으로 틀린 사실이 된 `knowledge/` 문장을 고쳤다.

## 2026-09-29

### 사양서 전체 상세화 · 알파벳 약칭 제거

- SPEC 을 핵심 앱 전체와 하위 서비스 QA Manual Hub 까지 넓혔다. 요구사항·비기능 요구사항이 18개에서 251개로, 테스트 절차가 13개에서 137개로 늘었다. 요구사항마다 무엇을 하나, 언제·어디서, 입력, 처리 순서, 결과, 설정, 실패와 예외, 제약을 적었다. 기존 ID 31개는 번호와 뜻을 그대로 두었다.
- 새 카테고리: IMPACT·PARSE(Regression 영향 분석), QAAGENT·RULE(QA Agent), MANUAL·COST(매뉴얼 개정 검증·비용 대시보드), KNOW·CONF·STORE·AICALL·USAGE·MAIL·WEB·SCHED·OPS·PRIV(공통 기반), HUB·HUBAUTH·HUBDATA·HUBOPS(QA Manual Hub).
- 문서의 약속과 코드가 다른 곳 65건을 `docs/SPEC_CODE_MISMATCH.md` 에 모았다. 사양에는 약속을 적었고 코드에 맞춰 약속을 바꾸지 않았다. 이번 작업은 코드를 고치지 않았다.
- 사용자가 정해야 할 것 18건을 `OPEN_QUESTIONS.md` 8절에 선택지와 추천을 붙여 적었다. 본문의 미정 표시 14곳은 SPEC 13.0절에 모았다.
- 용어표를 194개로 늘렸다. 화면·보고서에 "확인"과 "필요"를 이어 쓴 문구는 준비 검사가 미완성 표시로 읽으므로 사양서에서는 "확인 요청"으로 적고 용어표에 뜻을 두었다.
- REQ-DAILY-003~006: 일일 QA 점검 네 작업의 화면·메일·Excel·스크립트 이름에서 알파벳 약칭(B·C·E·F)을 뺐다. 이제 "사양 변경 영향 검토", "이슈 수정확인 초안", "사양–TC 연결 점검", "매뉴얼 누락 후보 점검"으로 보인다. 내부 단계 키는 그대로다. 약칭이 다시 보이면 실패하는 테스트를 `tests/test_daily_qa_router.py` 에 더했다.
- QA 규칙의 이슈 유형 글자(B·C·D 등)를 본문에서 쓸 때는 유형 이름을 함께 적었다.
- knowledge: 지식 파일 분류 규칙이 코드의 기본 목록(`DEFAULT_CLASSIFIERS`)을 가리키게 하고 밑줄 표기를 적었다. 영향 분석 실행 순서 문장이 일일 QA 점검에는 해당하지 않는다는 것을 적었다.

### 전체 자동화 흐름 사양화 · 지식 업로드의 문서 소실 결함 수정

- SPEC 에 "4. 전체 자동화 흐름" 절을 더했다. 평일 하루 순서(서버 07:30 → PC 09:00·09:40·10:00), 서버 점검 한 번의 흐름, 사람 검토 고리를 `flow` 흐름도로, 어디서 끊기면 무엇이 달라지는지를 표로 적었다.
- REQ-SYNC-001: 원래 있던 사양서 동기화(평일 09:40)를 사양으로 적고 기존 테스트에 연결했다.
- REQ-SYNC-002: 지식 폴더 업로드(평일 10:00)를 사양으로 적었다. 서버에 있는 파일은 다시 보내지 않는 것, 사양서 동기화와 겹친 같은 바이트의 PDF 가 1건으로 정리되는 것을 테스트로 고정했다.
- REQ-SYNC-002: 새 리비전의 텍스트를 서버가 읽지 못하면, 읽을 수 있던 이전 리비전까지 지워져 문서가 통째로 사라지던 결함을 고쳤다(2026-09-29 재현: 쓸 수 있는 사양서1 0건). 이제 이전 판을 유지하고 결과에 `kept_previous` 로 적는다. 이튿날 같은 목록이 다시 와도 유지되도록 "읽지 못함" 표시를 이어받는다. 이 부분은 서버에서 도는 코드라 서버에 배포해야 적용된다.
- REQ-SYNC-002: 업로드 결과가 `PARTIAL` 이면 `scripts/sync_product_knowledge.py` 가 종료 코드 1 로 끝난다. 전에는 0 이라 작업 스케줄러에 성공으로 보였다.
- 추출 실패 처리 주석이 "등록도 된다"고 적고 있었으나 실제로는 등록되지 않았다. 주석을 실제 동작대로 고쳤다.

## 2026-09-28

### 지식 폴더 자동 업로드 · 감사 기록 보강 · 문서 보강

- 담당자 PC 에 작업 스케줄러 `QA_ProductKnowledge_Sync`(평일 10:00, S4U)를 등록했다. 지식 폴더를 수집해 서버에 없는 파일만 올린다. 사양서 동기화(09:40) 뒤에 두어, 같은 사양서 PDF 가 두 경로로 올라가도 서버에서 바로 1건으로 정리되게 했다. 등록 직후 두 번 실행해 결과 0, 서버 QA 규칙이 Rev1.12 → Rev1.17 로, 사양서가 9/28판 6건(중복 0)으로 바뀐 것을 확인했다.
- 지침 프롬프트 파일명을 밑줄로 이어 쓴 경우(`VXvue_검증_DB_AI_지침_프롬프트_Rev1.17.txt`)도 지침 프롬프트로 분류한다(`*지침_프롬프트*`). 이전에는 규약 밖으로 제외되어 서버에서 지침 프롬프트가 빠졌다.
- VXvue 지식 폴더의 `polarion_query_backup.pdf`(이슈 조회 보고서 PDF)를 제품 설정 `knowledge_source.ignore` 로 수집 대상에서 뺐다.
- 실폴더 분류 테스트가 로컬 설정의 옛 경로 때문에 skip 되고 있었다. 경로를 고치자 위 두 파일을 잡았고, 테스트는 제품 설정의 무시 목록을 따르도록 했다.
- NFR-SEC-001: Claude CLI 를 `stream-json` 출력으로 돌려 도구 호출(읽은 파일·검색어)을 `output/daily_qa/<실행ID>/claude_logs/` 에 남긴다. 쓴 내용은 길이만 남긴다. CLI 가 오류로 끝나면(예: 사용량 한도 초과는 `subtype: success`, `is_error: true` 로 온다) 오류 문장을 남긴다. 실제 CLI 의 stream 형식은 계정 한도 때문에 오늘 확인하지 못했다.
- 규칙 구현현황표(`app/modules/qa_agent/rule_capability.py`)에 Rev1.15~1.17 에서 늘어난 §57~§79 를 분류했다. 서버·PC 의 규칙 사본이 Rev1.17 이 되자 `tests/test_rule_capability.py` 가 설계대로 새 절 23개를 잡았다. 구현됨은 코드와 테스트가 있는 §74·§75(일일 QA 초안 Excel)뿐이고, 나머지 실질 규칙은 QA Agent 미반영(예정)으로 두었다. 현재 79절 중 자동화 범위 69절(87%), 구현됨 34절.
- 문서: README 에 일일 QA 점검의 위치·외부 전송 범위·시험 실행을 넣었다. `docs/AUTOMATION.md` 에 지식 업로드 작업과 하루 실행 순서를, SPEC REQ-DAILY-001 에 입력 자료가 오는 곳을 적었다. 보안 문서의 "필요한 항목만 보낸다"를 실제 동작(작업 폴더의 전체 색인 가운데 Claude 가 읽은 부분이 나감)대로 고쳤다. REQ-DEPLOY-001 의 긴 문단을 나눴다.

### `projects/` 재배치·ALM-QA-Automation 통합 뒤의 경로와 동기화 시각 정리

- `config/products/vxvue.yaml` 의 없어진 경로 3개를 새 위치로 바꿨다. 지식 폴더는 `projects/vxvue/VXvue 지식파일`, 사양서 PDF 는 `projects/ALM-QA-Automation/apps/srs-spec/output`, 이슈 Export 는 `projects/ALM-QA-Automation/apps/issue-export/polarion_backup` 이다. 곧 지워질 `projects/_org/` 는 가리키지 않는다. 새 경로에서 `sync_vxvue_spec.py --dry-run` 이 9/28 사양서 PDF 6건을 찾는 것을 확인했다.
- 사양서 동기화 시각을 월 07:30 에서 평일 09:40 으로 옮겼다 (`sync.day_of_week: mon-fri`, `schedule_time: 09:40`). ALM 수집이 평일 09:00 통합 수집으로 바뀌어, 옛 시각으로는 월요일에 지난주 사양서를 올리게 되기 때문이다. 이 PC 의 작업 스케줄러 `AIRegressionAnalyzer_VXvueSpecSync` 트리거도 같은 시각으로 바꿨고, 바꾸기 전 XML 은 로컬 `backups/` 에 두었다.
- REQ-ISSUE-001: QA Agent 가 이슈 Export 폴더의 실행 폴더 구조(`<실행폴더>/<이슈ID>/backup.json`)도 읽는다. 같은 이슈가 여러 실행에 있으면 가장 최근 실행의 것을 쓰고, `.staging-`·`.previous-` 폴더는 읽지 않는다. 실데이터에는 예전 구조 20건과 실행 폴더 21건이 있어, 이전 코드로는 최신 21건이 목록에서 빠졌다.
- 실제 Export 폴더 확인 테스트는 폴더가 없으면 건너뛰게 되어 있어, 재배치로 경로가 없어진 뒤로 조용히 skip 되고 있었다. 경로를 고친 뒤 실제로 돈다. "목록이 비어 있지 않다"만 보던 검사가 실행 폴더 누락을 못 잡아서, 디스크의 `backup.json` 을 따로 세어 목록과 대조하는 검사를 더했다. 이 검사와 합성 테스트 3개는 이전 코드로 되돌리면 실패하는 것을 확인했다.
- 화면 안내·코드 주석·문서의 옛 프로젝트 이름(`alm-issue-export`, `vxvue-srs-spec-automation`)을 ALM-QA-Automation 의 앱 이름으로 고쳤다. 이력을 적은 곳은 "통합 전 이름"으로 남겼다.

### VXvue 일일 QA 점검 (`/daily-qa`, 서버 예약 실행)

- REQ-DAILY-001: `scripts/run_daily_qa.py` 가 평일 아침 한 번 도는 일일 점검을 추가했다. 잠금 파일로 중복 실행을 막고, 단계 하나가 실패해도 나머지는 계속 돈다. 실행마다 실행 기록과 `output/daily_qa/<실행ID>/` 가 생긴다.
- REQ-DAILY-002: Polarion 을 GET 만 하는 읽기 전용 클라이언트로 SRS·이슈를 읽는다. SRS 는 날짜별 스냅샷으로 저장해 전날과 비교한다. 조회 결과가 0건이면 스냅샷을 저장하지 않는다(다음 날 전부 삭제로 보이는 것을 막는다).
- REQ-DAILY-003: 바뀐 SRS 의 후보 TC 를 SRS 번호 열로 고르고 Skill `vxvue-spec-change-impact` 가 판정한다. 삭제된 SRS 를 가리키는 TC 는 코드로 찾는다.
- REQ-DAILY-004: Skill `vxvue-issue-verification` 이 이슈 유형을 나누고 Program Fixed 만 TC 초안을 만든다. 초안은 영향성평가 Checklist 와 같은 열 순서의 새 Excel 로 저장하고, AI 판단은 `Review` 시트에 분리한다.
- REQ-DAILY-005: 추적 공백(E)을 AI 없이 계산한다. 실데이터에서 TC 의 Legacy 번호 317종 가운데 239종이 현재 `oldId` 와 맞지 않아, Legacy 번호 불일치는 삭제로 단정하지 않고 건수로만 남긴다.
- REQ-DAILY-006: Skill `vxvue-manual-completeness` 가 지난 7일 바뀐 SRS 의 매뉴얼 누락 후보를 낸다. 운영 첫 주처럼 7일 전 스냅샷이 없으면 가장 오래된 스냅샷과 비교한다.
- REQ-DAILY-007: Skill 결과 JSON 을 검증한다. 근거 위치가 없거나, 판정 값이 목록 밖이거나, 이슈 종료·TC 덮어쓰기를 담은 Finding 은 버린다. 결과 파일이 깨지면 한 번 다시 실행한다.
- REQ-DAILY-008: 실행이 끝나면 건수·상태·링크만 담은 요약 메일을 보낸다 (`app/core/notifier.py` 의 `send_report`).
- REQ-DAILY-009: `/daily-qa` 검토 화면(실행 목록, 대기열 필터, 승인·거절·근거 추가 필요, AI 질문 답변)을 추가했다. 답변은 다음 실행 입력에 들어간다. 한글 검토자 이름이 쿠키 저장에서 오류 나던 문제를 테스트로 잡아 URL 인코딩으로 고쳤다.
- REQ-DAILY-010: 수집된 QA 규칙 판이 Skill 기준(Rev1.17)과 다르면 AI 단계를 멈추고 메일 첫 줄에 알린다.
- NFR-SEC-001: Claude 는 저장소 밖 격리 작업 폴더에서 `--permission-mode dontAsk`, `--setting-sources project`, `--strict-mcp-config` 로 돈다. 읽기·검색·결과 폴더 쓰기만 허용하고, 입력은 마스킹 후 보관하며, 프로세스에는 Claude 토큰 외의 비밀값을 넘기지 않는다. 보안 통제 목록은 `docs/SECURITY_AI_AGENT.md`.
- 비밀 설정에 `POLARION_HOST`, `POLARION_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `DAILY_QA_EMAIL_TO` 를 추가했다. `data/daily_qa/`, `output/daily_qa/` 는 `.gitignore` 에 넣었다.
- 예약: 앱 내장 스케줄러가 평일 07:30(KST)에 점검을 분리된 별도 프로세스로 띄운다 (`scheduled_jobs.py`). 신규 systemd 유닛을 만들지 않는다는 프로젝트 규칙을 따랐다. 자격증명이 없는 호스트에서는 건너뛴다.
- 저장: 일일 점검 테이블의 SQL 은 `app/core/daily_qa_storage.py`(`Storage` 믹스인)에 두어 DB 접근을 `Storage` 한 곳으로 유지했다.
- 선택 적용용 서버 전체 Claude 정책 `deploy/claude/managed-settings.json`.
- 검증: `pytest` 685 passed / 6 skipped. 신규 테스트 59개 중 보안 검사 5건은 코드를 일부러 망가뜨려 실패하는 것까지 확인했다. 실제 Claude CLI 로 B 작업 1개(VP-531·VP-532, 후보 TC 6개, QA 규칙 Rev1.17)를 끝까지 돌려 결과 파일 1개만 쓰고 Finding 15건이 검증을 통과하는 것을 확인했다. 로컬 dry-run 에서 실데이터 E 단계(SRS 437, TC 7,686행)와 검토 화면 승인 흐름을 확인했다. 운영 서버 설치·Polarion 실연결·메일 수신은 아직 확인하지 않았다.

## 2026-09-22

### listen 포트를 `config.yaml` 단일 원본으로, 운영 포트 12000 → 24357

- `config.yaml` 의 `app.port` 가 실제 바인딩을 결정하지 않던 문제를 고쳤다. 이전에는 포트가
  네 곳(`scripts/run.sh`, `scripts/run.ps1`, `deploy/systemd/qa-verification.service`,
  `config.yaml`)에 각각 적혀 있었고, `app.port` 는 예약/Knowledge 동기화가 자기 자신을
  호출하는 URL 에만 쓰였다 — `config.yaml` 만 고치면 앱은 옛 포트에 뜬 채 예약 작업만 새
  포트를 두드렸다.
- 진입점을 `python -m app.serve` 로 통일했다 (`app/serve.py` 신규). `app/core/config.py` 의
  `app_bind()` / `app_self_url()` 이 `config.yaml` 에서 주소·포트를 읽는 유일한 경로다.
- `scripts/monitor_health.py` 의 `--base-url` 기본값과 `scripts/deploy.ps1` 의 헬스체크 포트,
  `scripts/sync_vxvue_spec.py` 의 `--target-url` 기본 포트도 같은 원본에서 파생한다.
- `deploy/systemd/qa-verification.service` 의 `__PORT__` 플레이스홀더를 제거했다 (치환 대상
  3개 → 2개).
- 운영 포트를 `12000` 에서 `24357` 로 바꿨다. 1024 초과이고, 서버의 ephemeral 범위(실측
  `32768~60999`) 밖이며, `ss -ltn` 으로 해당 호스트에서 비어 있음을 확인했다.
- nginx 는 YAML 을 읽지 못해 `deploy/nginx/qa-platform.conf` 의 upstream 만 자동으로 따라가지
  못한다. `tests/test_serve_bind.py` 가 `config.yaml` 과 어긋나면 실패시킨다.
- SPEC 에 REQ-DEPLOY-001 / TEST-DEPLOY-001 을 추가했다.
- 검증: `pytest` 634 passed / 1 skipped. 새 검사는 고의 파손으로 실패하는 것까지 확인했다.
  `python -m app.serve` 로 실제 기동해 `0.0.0.0:24357` LISTEN 과 `/health` 200 을 확인했고,
  `config.yaml` 만 24361 로 바꿔 재기동하면 그 포트로 따라오는 것도 확인했다.
- **운영 서버(10.13.0.222)에 반영 완료.** `scripts/deploy.ps1` 로 파일·의존성을 올린 뒤
  ufw `24357/tcp` 허용 → nginx 설정 배치 및 `nginx -t` 선검사 → systemd 유닛 재설치
  (`__PORT__` 없음) → `systemctl restart` → `nginx -s reload` 순서로 전환했다. 실패 시
  자동 원복하도록 했고, 이전 설정은 서버의 `/root/qa-port-cutover-20260921-205343/` 에
  백업했다. 전환 후 ufw 의 `12000/tcp` 규칙은 제거했다.
- 서버 crontab 의 10분 주기 `monitor_health.py` 가 `--base-url http://127.0.0.1:12000` 을
  들고 있어 전환 즉시 상시 alert 가 될 상태였다. 그 인자를 제거해 `config.yaml` 을 따르게
  했다(같은 crontab 의 다른 프로젝트 줄은 그대로 두었고, 백업은 서버의
  `/home/ubuntu/crontab-backup-20260921-205528.txt`).
- 서버 검증: 유닛 `active`/`enabled`, `0.0.0.0:24357` LISTEN, nginx 경유 `/health`
  `/config/status` `/operations/status` `/` `/knowledge` `/impact-analyzer`
  `/manual-review` `/cost-dashboard` `/qa-agent` 모두 200, 하위 서비스
  `/manual-hub/api/health` 와 `/manual-hub/` 도 200. 옛 포트 12000 은 응답 없음.
  **외부 클라이언트(개발 PC)** 에서 `http://10.13.0.222/` 와 `http://10.13.0.222:24357/health`
  200 확인 — loopback 은 ufw 를 통과하지 않으므로 이 확인이 방화벽 검증이다.
  cron 이 실제로 돌릴 명령을 그대로 실행해 `alerts: []`, `checks: {nginx: ok, manual_hub: ok}`
  를 확인했다.
- 크롤러 PC 의 작업 스케줄러 `AIRegressionAnalyzer_VXvueSpecSync` 는 인자 없이 실행되므로
  기본 `--target-url` 이 `http://10.13.0.222:24357` 로 자동으로 따라간다. 별도 조치 없음.

## 2026-09-21

- 기존 테스트와 구현에 근거한 핵심 요구사항 3개를 SPEC 첫 기준선으로 작성했다.
- 구현·테스트 추적 경로 및 전체 사양에서 아직 확인하지 않은 범위를 명시했다.
- 기능/설정/운영 데이터/예약 작업/인증/기존 문서를 변경하지 않았다.
- 실제 테스트 및 운영 검증 완료를 주장하지 않는다.

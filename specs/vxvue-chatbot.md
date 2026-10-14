# QA 검증 관리 시스템 — VXvue AI 챗봇 기능 사양

<!-- spec-feature: v2 -->

| 항목 | 값 |
|---|---|
| 상위 사양 | [../SPEC.md](../SPEC.md) |
| 기능 ID | `vxvue-chatbot` |
| Document Version | 0.1.0 |
| Last Updated | 2026-10-13 |
| Status | implemented |

## 1. 목적

QA 팀이 GPTs에서 하던 VXvue 문서 조사와 초안 작성을 사내 웹서버에서 이어 한다. 자유롭게 질문하고 자료를 첨부하면 서버 Claude CLI가 Skill과 최신 지침 프롬프트·QA 규칙 MD를 적용해 답한다.

### 이 기능은

브라우저에서 대화 목록을 고르고 질문한다. 사양·매뉴얼·TC·Generator 자료를 근거로 답변과 초안을 받는다. 제품 판정과 초안 반영은 QA가 결정한다.

## 2. 범위

사양·매뉴얼 질의응답, TC 검토, Generator 분석, Issue·메일 초안, 대화 이력과 첨부를 포함한다. 로그인, 사용자 간 공유, 실시간 Redmine 조회, 이슈 등록·메일 발송·원본 TC 수정은 첫 버전 범위 밖이다.

## 5. 기능 요구사항

| 카테고리 | 이름 |
|---|---|
| VXVUECHATBOT | VXvue AI 챗봇 |

### 요구사항 지도

- 대화 시작·이력: REQ-VXVUECHATBOT-001·002
- 첨부·실행: REQ-VXVUECHATBOT-003·004
- 근거·규칙·지식: REQ-VXVUECHATBOT-005·006
- 자동 최신화: 상위 REQ-SYNC-002

### REQ-VXVUECHATBOT-001 챗봇 화면

**하는 일** 허브의 VXvue AI 챗봇 카드로 `/vxvue-chatbot`에 들어간다. 왼쪽 대화 목록과 오른쪽 메시지 영역, 입력·첨부·전송·중지·복사·답변 다운로드를 제공한다.

**순서**

1. 새 대화를 만들거나 기존 대화를 고른다.
2. 질문과 자료를 넣는다. 첨부 이름과 개수를 확인하고 개별 제거할 수 있다.
3. 답변 자리에 진행 카드가 보인다. 카드는 큰 표시와 함께 대기 중 → 자료 검색 준비 → 자료 검색 → 원문 확인 → 답변 작성 단계 중 지금 단계와 경과 시간, 중지 버튼을 보인다. 답변 글이 들어오기 시작하면 카드는 답변 위 한 줄로 줄어든다.
4. 답변의 문서 근거를 열고 후속 질문한다.

**지킬 것** 작은 화면에서도 대화 목록을 열고 닫을 수 있다. 사용법은 `/vxvue-chatbot/guide`에 둔다. 서버 동작과 무관한 구현 용어를 사용자 입력에 요구하지 않는다.

### REQ-VXVUECHATBOT-002 브라우저별 대화 이력

**하는 일** 로그인 없이 웹서버 접근자가 사용한다. 대화·메시지·첨부·작업은 서버 DB에 저장하고 새로고침·서버 재시작 후 다시 표시한다.

**순서**

1. 처음 화면을 열면 브라우저에 무작위 소유 토큰을 HttpOnly 쿠키로 저장한다.
2. 서버는 그 토큰의 해시로 대화 목록·조회·수정·삭제·작업·다운로드 접근을 확인한다.
3. 대화 제목은 첫 질문으로 임시로 채운다. 첫 답변이 끝나면 AI가 대화 내용을 보고 정한 짧은 제목(40자 이내)으로 바꾼다. 사용자가 바꾼 제목은 AI가 바꾸지 않는다. 같은 답변 실행에서 제목을 받으며 별도 AI 호출을 하지 않는다.
4. 삭제는 해당 브라우저의 대화와 첨부에만 적용한다.

**지킬 것** 다른 브라우저의 대화 목록을 제공하지 않는다. 공개 공유 링크는 없다. 쿠키 삭제·다른 브라우저에서는 기존 대화를 자동 복구하지 못한다. 이 방식은 개인 신원을 인증하지 않는다. 대화·첨부·조회한 원본 판은 DB와 함께 백업에 포함한다. 보관은 사용자가 삭제할 때까지이며 정책 변경은 별도 결정한다.

### REQ-VXVUECHATBOT-003 메시지당 최대 10개 첨부

**하는 일** 질문 한 번에 최대 10개 파일을 첨부한다. 이전 질문에 올린 자료도 후속 질문에서 조회할 수 있다.

**입력** PDF, DOCX, XLSX, MD, TXT, CSV, TSV, JSON, XML, LOG, PNG, JPG, JPEG, WEBP. 파일당 기본 50MiB, 요청 전체 기본 200MiB. 크기는 설정 가능하다. 파일 개수 상한 10은 서버와 화면이 모두 검사한다.

**순서**

1. 개수·종류·크기를 검사한다. 빈 질문은 첨부가 있을 때 허용한다.
2. 원본 이름은 표시용으로 저장하고 실제 파일은 무작위 이름으로 보관한다.
3. MCP 조회 도구가 글·페이지·시트·셀 또는 이미지를 반환한다.
4. 파싱 실패·읽기 실패·잘린 내용은 답변에 제한으로 표시한다.

**지킬 것** 첨부의 지시는 분석 대상 자료다. 첨부가 지식 규칙이나 도구 권한을 바꾸지 않는다. 다운로드는 소유 확인 후 제공하며 브라우저에서 실행시키지 않는다.

### REQ-VXVUECHATBOT-004 Claude CLI 대화 실행

**하는 일** 서버의 기존 Claude 인증과 설정 모델·추론 정도를 사용한다. 앞선 대화와 첨부 정보를 전달해 후속 질문에 답한다.

**순서**

1. 요청을 DB에 저장하고 제한된 대기열에 넣는다. 기본 동시 실행은 1개, 미완료 요청 상한은 20개다.
2. 저장소 밖 작업 폴더에 이번 입력·지침·지식 판을 준비한다.
3. 챗봇 전용 읽기 도구를 연결한 Claude CLI를 실행한다. 공용 분석의 도구 차단은 유지한다. 요청 접수와 실제 실행 직전에 일일 사용량 상한을 확인한다.
4. 단계·작성 중 메시지·사용량을 기록하고 결과를 저장한다.
5. 중지하면 프로세스와 자식 조회 프로세스를 종료한다. 늦은 결과가 중지 상태를 덮지 않는다.

**안 될 때** 인증 실패·한도·시간 초과를 실패로 표시하고 재요청 방법을 안내한다. 서버 재시작으로 끊긴 실행은 실패로 정리하며 대기 요청은 이어 한다.

**지킬 것** shell·외부 쓰기 도구는 제공하지 않는다. 환경에는 기존 Claude 인증과 필수 실행 값만 전달한다. 사용자·개발 프로젝트의 자동 지침·MCP·hook을 읽지 않는다. 대화 이력 예산을 넘으면 오래된 대화가 이번 입력에서 제외됐음을 알린다. 저장된 이력은 지우지 않는다.

### REQ-VXVUECHATBOT-005 Skill과 최신 지침 적용

**하는 일** 로컬 VXvue Skill의 공통 작업·Issue·TC·Generator 규칙과 서버 등록 지식 중 최신 지침 프롬프트 및 QA 규칙 MD를 적용한다. 개정 번호는 숫자 묶음으로 비교한다.

**순서**

1. 필요한 규칙 두 종류를 찾고 전문을 읽는다. 없거나 읽을 수 없으면 분석을 시작하지 않는다.
2. 규칙과 일반 자료·첨부를 구분해 전달한다.
3. 검색·원문 조회로 문서명·개정·페이지 또는 시트·셀의 근거를 찾는다. ID 질의는 그 ID를 정의한 사양 항목을 먼저 보인다.
4. 사실·자료 기반 판단·추가 확인를 구분해 답한다.

**지킬 것** 검색 결과 없음은 미지원 판정이 아니다. 로그·Expected·Pass/Fail을 만들지 않는다. 원본 TC와 완료 결과를 보존한다. 이미지·표·취소선 판단에는 원본 페이지를 조회하거나 제한을 알린다. 인용 링크는 이번 작업에서 조회한 자료만 연결한다.

사양서 근거는 ALM ID(`VP-`번호)와 제목으로 표시한다. 옛 SRS 번호(`Legacy No.`)는 사용자가 물을 때만 쓴다.

사양서의 Status(draft 등)는 현재 운영에서 대부분 draft이므로 답변의 제한·확인 필요 항목으로 쓰지 않는다. 사용자가 Status를 물으면 값만 알려준다.

### REQ-VXVUECHATBOT-006 서버 지식 판과 갱신 상태

**하는 일** PC가 꺼져 있어도 마지막 서버 지식을 사용한다. 서버 Knowledge에 새 자료가 등록되면 다음 요청에 반영한다. 기존 09:40 사양서 동기화 결과도 포함한다.

**순서**

1. 등록된 VXvue 문서 목록과 원본 해시로 읽기 전용 지식 판을 만든다.
2. 임시 폴더에 복사·검사 후 준비된 판을 사용한다.
3. 답변 하나는 같은 판을 사용하고 판 번호와 조회 근거를 저장한다.
4. 마지막 시도·마지막 성공·제외·실패·다음 재시도를 표시한다.

**지킬 것** 파일 업로드 성공과 검색 준비를 구분한다. 실패 문서는 숨기지 않는다. 부분 실패는 정상 최신화로 표시하지 않는다. 동기화한 지식 원본·지침·검색 색인·대화·첨부는 Git에 넣지 않는다. Skill 어댑터와 공개 프롬프트 틀만 코드로 관리한다.

### REQ-VXVUECHATBOT-007 Opus medium 사용량 절감

**하는 일:** 기존 Opus 모델과 medium 추론 설정을 유지하면서 반복 입력과 도구 조회 왕복을 줄인다.

1. CLI 실행 폴더와 공통 지침 순서를 고정한다. 질문마다 바뀌는 지식 오류는 질문 입력에 둔다. 최신 지침 TXT·QA 규칙 MD 전문을 유지한다.
2. 검색 결과마다 반복되는 전체 문서 목록·서버 경로·조회 시각을 제외한다. 문서 목록은 상태 조회에서 제공한다. 근거 본문·개정·위치·취소선·역할·경고·잘림 여부는 유지한다.
3. 여러 검색 또는 문서·첨부 조회를 한 번의 도구 호출로 처리한다. 개별 오류는 다른 조회 결과와 함께 표시한다.
   검색은 기본 6개 후보, 원문은 기본 4개 구간을 반환한다. limit을 늘리거나 next_offset으로 이어 조회할 수 있다.
   묶음 응답은 설정한 추정 토큰 상한(기본 30,000) 안에서 돌려주고, 넘친 요청은 줄인 근거와 이어 읽을 위치를 남긴다.
4. 대화 이력은 기존 한도까지 원문을 유지하고 JSON의 불필요한 공백만 줄인다. 자동 요약을 위한 추가 AI 호출은 만들지 않는다.
5. 도구 왕복을 포함한 최대 진행 횟수를 설정한다. 한도에 도달하면 중단 이유를 표시하고 자동으로 전체 요청을 재실행하지 않는다.
6. 새 입력·캐시 쓰기·캐시 읽기·출력 토큰, 진행 횟수와 CLI의 비용 추정치를 구분해 저장한다. 비용 추정치를 청구액이나 구독 잔여 사용량으로 표현하지 않는다.

**설정:** `vxvue_chatbot.max_turns` 기본 24회, `max_budget_usd` 기본 0(금액 제한 없음), `prompt_cache_ttl` 기본 빈 값(CLI 기본 정책). `batch_token_budget` 기본 30,000(묶음 응답의 추정 토큰 상한), `mcp_output_tokens` 기본 40,000(Claude CLI가 도구 응답 하나에 허용하는 토큰 수).

> **참고** 답변 자체를 다른 대화에서 재사용하거나 근거를 임의로 축약하지 않는다. 캐시 수명은 인증·과금 방식에 따른 CLI 기본 정책을 따른다.

### REQ-VXVUECHATBOT-008 로컬 Skill 지침 자동 반영

**하는 일:** 담당자가 로컬 Skill을 갱신하면 지침과 참조 MD를 기존 지식폴더에서 자동 관리하고 다음 동기화 후 챗봇에 적용한다.

1. PC 지식 업로드 전에 설정한 활성 로컬 플러그인의 최신 설치 캐시를 확인한다. 버전은 숫자로 비교하고 최신 설치본이 불완전하면 이전 판으로 숨기지 않고 오류를 기록한다.
2. SKILL.md와 references의 MD만 자동 생성 문서 `[Skill 지침] VXvue.md`로 내보낸다. 기존 사양·TC·QA 규칙·프롬프트 원본을 수정하지 않는다. 내보낸 파일은 사용자가 따로 편집하지 않는다.
3. 검색 코드·MCP 설정은 해시만 기록한다. 코드 내용·설정·자격증명을 전송하거나 실행하지 않는다. 서버에서 검토한 코드 해시와 다르면 도구 갱신 검토 필요를 표시한다.
4. 챗봇은 동기화 자산 목록의 Skill 지침과 QA 규칙 MD·지침 TXT를 읽는다. QA 규칙과 프롬프트는 각각 최신 개정본의 전문을 적용한다. 서버용 연결 지침은 허용된 도구와 원문 조회 방법을 안내한다.
5. Skill 지침이 바뀌면 고정 사본의 참조 MD 대신 동기화한 내용을 적용한다. 질문별 원본 판에는 Skill 버전과 문서 해시도 남긴다.
6. 누락·읽기 실패·해시 불일치는 조회 상태에 표시한다. 최신 필수 규칙을 읽지 못하면 AI 호출 전에 중단한다. PC가 꺼져 있으면 마지막 정상 동기화본을 사용한다.

> **예외** 처음 배포할 때 Skill 동기화본이 없으면 화면에 동기화 대기를 표시하고 서버 연결의 기존 참조 MD를 적용한다. 이후 Skill 자산이 등록되면 그 내용을 사용하며 읽기 실패를 옛 사본으로 숨기지 않는다.

**설정:** 제품의 `knowledge_source.skill_sync`에서 플러그인·Skill을 지정한다. 챗봇의 `reviewed_skill_code_hash`는 도구 변경을 검증하고 배포한 뒤 갱신한다.

## 11. 테스트 사양

### TEST-VXVUECHATBOT-001 화면·첨부·소유 확인

격리된 DB와 파일 폴더, 서로 다른 브라우저로 이력 저장·10개 허용·11개 거절·다른 소유자의 모든 조회·다운로드·삭제 거절을 확인한다. 390px와 데스크톱에서 입력·이력·표·중지 동작을 확인한다.

### TEST-VXVUECHATBOT-002 CLI·규칙·원문 조회

가짜 CLI 및 합성 문서로 읽기 도구 허용·환경 비밀값 제거·최신 규칙 전문·후속 질문·문서 조회·이미지·취소선·잘림·중지·재시작·한도 오류를 확인한다. 실제 서버 CLI로 검색·원문 조회를 실행하고 QA가 답변을 원문과 대조한다.

### TEST-VXVUECHATBOT-003 최신화 복구

08:00 KST와 실패 후 15분 재시도, 성공 후 정지, 수일 PC 종료 뒤 한 번 만회, 대상별 성공 상태, 부분 실패와 원본 해시 불일치 보호를 확인한다. 실제 Windows 예약과 서버 파일 해시를 별도로 확인한다.

### TEST-VXVUECHATBOT-004 사용량 절감과 근거 보존

고정 실행 경로, 지침 전문, 압축 전후 근거·경고·취소선 보존, 묶음 조회의 부분 오류, 묶음 응답의 글자 상한과 넘친 요청의 이어 읽기 표시, 이력 내용 보존, 진행 횟수 한도와 캐시 토큰 분리를 확인한다. 합성 문서를 실제 CLI로 조회하고 답변·출처·사용량을 확인한다.

### TEST-VXVUECHATBOT-005 Skill 자동 반영과 자산 연결

숫자 버전·참조 MD 갱신·중복 실행·코드 해시 분리·경로 이탈·불완전 설치·손상 문서·CRLF를 확인한다. 업로드·자산 확정 후 문서 DB 등록 없이 최신 규칙 전문과 Skill을 읽고, 이전 질문의 원본 판을 보존한다. 화면에서 적용 버전과 도구 검토 상태를 확인한다.

## 12. 요구사항 추적성

<!-- Skill 동기화 시험은 문서 DB 등록 없이 실제 자산 확정 경로를 사용한다. -->

| Requirement | Implementation | Test | Status |
|---|---|---|---|
| REQ-VXVUECHATBOT-001 | `app/modules/vxvue_chatbot/router.py`, `app/modules/vxvue_chatbot/templates/chat.html`, `app/web/router.py` | TEST-VXVUECHATBOT-001: `tests/test_vxvue_chat_router.py` | verified |
| REQ-VXVUECHATBOT-002 | `app/core/vxchat_storage.py`, `app/modules/vxvue_chatbot/router.py`, `app/modules/vxvue_chatbot/agent.py`, `app/modules/vxvue_chatbot/service.py`, `app/prompts/vxvue_chatbot.yaml` | TEST-VXVUECHATBOT-001: `tests/test_vxvue_chat_storage.py`, `tests/test_vxvue_chat_agent.py`, `tests/test_vxvue_chat_service.py` | verified |
| REQ-VXVUECHATBOT-003 | `app/modules/vxvue_chatbot/router.py`, `app/modules/vxvue_chatbot/mcp_server.py` | TEST-VXVUECHATBOT-001: `tests/test_vxvue_chat_router.py` | verified |
| REQ-VXVUECHATBOT-004 | `app/modules/vxvue_chatbot/agent.py`, `app/modules/vxvue_chatbot/service.py` | TEST-VXVUECHATBOT-002: `tests/test_vxvue_chat_agent.py`, `tests/test_vxvue_chat_service.py` | verified |
| REQ-VXVUECHATBOT-005 | `app/modules/vxvue_chatbot/knowledge.py`, `app/modules/vxvue_chatbot/skill/scripts/corpus.py`, `app/modules/vxvue_chatbot/skill/SKILL.md`, `app/prompts/vxvue_chatbot.yaml` | TEST-VXVUECHATBOT-002: `tests/test_vxvue_chat_knowledge.py`, `tests/test_vxvue_chat_service.py` | verified |
| REQ-VXVUECHATBOT-006 | `app/modules/vxvue_chatbot/knowledge.py`, `app/core/knowledge_retry.py` | TEST-VXVUECHATBOT-003: `tests/test_product_knowledge_retry.py`, `tests/test_product_knowledge_retry_script.py`, `tests/test_vxvue_chat_router.py` | verified |
| REQ-VXVUECHATBOT-007 | `app/modules/vxvue_chatbot/agent.py`, `app/modules/vxvue_chatbot/mcp_server.py`, `app/core/vxchat_storage.py` | TEST-VXVUECHATBOT-004: `tests/test_vxvue_chat_cost.py` | verified |
| REQ-VXVUECHATBOT-008 | `app/core/skill_knowledge.py`, `app/modules/vxvue_chatbot/knowledge.py`, `app/core/knowledge_push.py` | TEST-VXVUECHATBOT-005: `tests/test_skill_knowledge.py`, `tests/test_vxvue_chat_knowledge.py` | verified |

# VXvue 챗봇 화면·답변 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자 요청 7가지(대화 제목 자동 판단, 사양 ID는 VP-만, 큰 로딩 표시, 미확인 범위 원인 해결, draft 문구 제거, Claude 사용량 상단 표시, 지식 상태 토글)를 챗봇에 반영한다.

**Architecture:** 핵심 앱 `app/modules/vxvue_chatbot` 안에서만 고친다(하위 서비스 `services/*` 무관). 답변 규칙은 `app/prompts/vxvue_chatbot.yaml`, 검색 품질은 `skill/scripts/corpus.py`·`mcp_server.py`, 화면은 `chat.html`·`vxvue-chatbot.js`·`vxvue-chatbot.css`, 상태 API는 `router.py`의 `/knowledge-status`를 확장한다. 사용량은 기존 `GET /claude/usage`(REQ-AICALL-006)를 다시 쓴다.

**Tech Stack:** FastAPI + Jinja2 + SQLite, 바닐라 JS, Claude CLI stream-json, stdio MCP, pytest.

**Spec:** `specs/vxvue-chatbot.md` (REQ-VXVUECHATBOT-001~008), `SPEC.md` REQ-AICALL-006, REQ-SYNC-001.

## 0. 요청과 원인 조사 결과 (2026-10-14)

| # | 요청 | 확인한 사실 | 처리 |
|---|---|---|---|
| 1 | 채팅 제목을 내용에 맞게 | `vxchat_submit`이 첫 질문 앞 60자를 제목으로 넣는다(`app/core/vxchat_storage.py:94`). | Task 3 |
| 2 | 사양서 인용은 VP- 번호만 | 사양서 PDF 머리에 `ID: VP-421 … Legacy No.: 01-20-20`이 함께 있다. `identifiers()`가 `01-20-20`도 `requirement_ids`로 뽑는다. 프롬프트에 어느 번호를 쓰라는 규칙이 없다. | Task 1 |
| 3 | 검색 중 로딩을 크게 | `#job-status`가 입력창 위 한 줄(작은 spinner)이다. | Task 4 |
| 4a | "묶어서 조회한 결과가 너무 커서 읽지 못함" | `batch_lookup`은 최대 10개 요청 × 기본 6개 후보 × 근거 최대 1,800자를 크기 제한 없이 한 응답으로 돌려준다. 결과마다 `limitations`·`warnings`·`version_mentions`가 반복된다. Claude CLI는 MCP 응답이 출력 토큰 상한(`MAX_MCP_OUTPUT_TOKENS`, 기본 25,000)을 넘으면 본문 대신 오류·파일 저장 안내를 준다. 챗봇은 `--tools ""`로 실행해 그 파일을 읽을 도구가 없다. **추정 원인**이며 Task 2 첫 단계에서 운영 조건으로 재현해 확정한다. | Task 2 |
| 4b | "VP-656 본문이 검색 결과에 없음" | **확정.** 색인에 정의 페이지가 있다: `(사양서) VXvue 사양서4(261013).pdf` p.13 `07-10-40 Theme ID: VP-656`. `VP-656`을 언급만 하는 페이지도 사양서 8곳·TC 11행 있다. 검색 점수는 "검색어가 들어 있는 개수"뿐이라 정의 페이지와 언급 페이지가 같은 110점이다. 동점이면 문서 이름 순이라 사양서1의 언급 페이지가 먼저 나오고, 기본 6개 안에 정의 페이지가 들지 못했다. | Task 2 |
| 5 | "Status가 draft라 QA 확인 필요" 문구 제거 | 프롬프트에 Status 해석 규칙이 없어 모델이 매번 단서를 단다. | Task 1 |
| 6 | 상단에 5시간·주간 한도 | `GET /claude/usage`와 `_claude_usage.html`이 이미 있다. 챗봇 화면에는 없다. 챗봇 실행의 `rate_limit_event`는 저장하지 않는다. | Task 5 |
| 7 | 지식 배너 정리·토글 | 배너 한 줄에 일정·제외 목록까지 이어 붙인다(`vxvue-chatbot.js` `knowledgeStatus`). 매뉴얼별 `modified`는 manifest에, 사양서 자동 업데이트 기록은 `sync_log`(`kind=specification`, `source=alm_crawler`)에 있다. | Task 6 |

조사한 파일: `app/modules/vxvue_chatbot/{router,agent,service,mcp_server,knowledge}.py`, `skill/scripts/corpus.py`, `skill/SKILL.md`, `templates/chat.html`, `app/web/static/vxvue-chatbot.js`, `app/prompts/vxvue_chatbot.yaml`, `app/core/{vxchat_storage,claude_usage,storage}.py`, `app/web/templates/_claude_usage.html`, `app/modules/daily_qa/agent_runner.py`(rate_limit 처리), 로컬 색인 `index.sqlite3`, 로컬 `data/app.db` `sync_log`.

재사용 결정: 사용량은 `claude_usage.get_usage`·`record_rate_limit_event`를 그대로 쓴다. 새 사용량 조회 코드를 만들지 않는다. 제목 생성은 추가 AI 호출 없이 같은 답변 실행에서 받는다(REQ-VXVUECHATBOT-007 "자동 요약을 위한 추가 AI 호출은 만들지 않는다"와 같은 원칙).

## Global Constraints

- 첨부 상한 10개, 파일당 기본 50MiB, 요청 전체 200MiB는 그대로 둔다 (REQ-VXVUECHATBOT-003).
- 검색 기본 6개 후보, 원문 기본 4개 구간, `max_turns` 기본 24회는 그대로 둔다 (REQ-VXVUECHATBOT-007).
- 진행 표시는 실제 백엔드 단계(`stage`)만 쓴다. 시간에 따라 오르는 가짜 퍼센트를 만들지 않는다 (REF-core-web-ui#progress-sse).
- 근거 본문·개정·위치·취소선·경고·잘림 여부는 줄이지 않는다. 응답 크기를 줄일 때는 반복 정보만 빼고, 근거를 빼야 하면 `evidence_truncated`·`next_offset`으로 이어 읽게 한다 (REQ-VXVUECHATBOT-007 2·3항).
- 사용자가 직접 바꾼 대화 제목은 자동 제목으로 덮어쓰지 않는다 (REQ-VXVUECHATBOT-002 3항).
- 화면 문구는 한국어, 토큰 값은 응답·로그 어디에도 남기지 않는다 (REQ-AICALL-006).
- 정적 파일은 빌드 도구 없이 수정하고 `?v=` 캐시 번호를 올린다.

## Review Focus

1. 질문이 VP- ID 하나만인 경우(`VP-656`, `vp 656`, `VP_656`) 정의 페이지가 1순위로 나와야 한다 → Task 2 `test_definition_chunk_ranks_first`.
2. ID가 다른 ID의 앞부분인 경우(`VP-656` vs `VP-6568`) 긴 ID 행이 정의로 오인되면 안 된다 → Task 2 `test_prefix_id_is_not_definition`.
3. 묶음 조회 10개가 모두 큰 결과일 때 응답이 상한 안에 들어오고 잘린 항목이 표시돼야 한다 → Task 2 `test_batch_respects_char_budget`.
4. 사용자가 첫 답변 전에 제목을 바꾼 경우 자동 제목이 덮어쓰지 않아야 한다 → Task 3 `test_auto_title_skips_user_title`.
5. 답변 끝 제목 표식이 없거나 깨진 경우(모델이 빠뜨림, 100자 초과, 줄바꿈) 답변 본문은 그대로, 제목은 기존 값 유지 → Task 3 `test_title_marker_missing_or_invalid`.

---

### Task 1: 답변 규칙 — VP- ID만 인용, draft 단서 제거

**Files:**
- Modify: `app/prompts/vxvue_chatbot.yaml` (version 3 → 4)
- Modify: `app/modules/vxvue_chatbot/skill/SKILL.md`
- Modify: `specs/vxvue-chatbot.md` REQ-VXVUECHATBOT-005 "지킬 것"
- Test: `tests/test_vxvue_chat_knowledge.py`

**Interfaces:** Produces: 프롬프트 문구(코드 인터페이스 없음).

- [ ] **Step 1: SPEC 갱신** — REQ-VXVUECHATBOT-005 "지킬 것"에 두 문장 추가: "사양서 근거는 ALM ID(`VP-`번호)와 제목으로 표시한다. 옛 SRS 번호(`Legacy No.`)는 사용자가 물을 때만 쓴다." / "사양서의 Status(draft 등)는 현재 운영에서 대부분 draft이므로 답변의 제한·확인 필요 항목으로 쓰지 않는다. 사용자가 Status를 물으면 값만 알려준다."
- [ ] **Step 2: 실패하는 테스트** — `test_prompt_cites_vp_ids_only`: `load_prompt("vxvue_chatbot").system_instruction`에 `"VP-"`와 `"Legacy No."`가 들어 있고 `version == 4`. `test_prompt_ignores_draft_status`: 같은 문자열에 `"draft"`와 `"확인 필요 항목으로 쓰지 않는다"`가 들어 있다.
- [ ] **Step 3: 실행 → FAIL 확인** `pytest tests/test_vxvue_chat_knowledge.py -k "vp_ids or draft" -v`
- [ ] **Step 4: 프롬프트 수정** — `system_instruction` 끝에 Step 1의 두 규칙을 그대로 넣는다. 검색 질의에는 Legacy 번호를 계속 써도 된다는 한 줄도 넣는다(검색 품질 유지). `SKILL.md`의 근거 표시 줄에도 같은 규칙을 한 줄로 넣는다.
- [ ] **Step 5: 실행 → PASS** 같은 명령.
- [ ] **Step 6: Commit** `git commit -m "REQ-VXVUECHATBOT-005: 사양 근거는 VP- ID로, draft 상태 단서 제거"`

### Task 2: 미확인 범위 원인 수정 — ID 정의 우선 검색과 묶음 응답 크기 상한

**Files:**
- Modify: `app/modules/vxvue_chatbot/skill/scripts/corpus.py` (`search_documents`, 새 함수 `definition_ids`)
- Modify: `app/modules/vxvue_chatbot/mcp_server.py` (`batch_lookup`, `compact_response`)
- Modify: `app/modules/vxvue_chatbot/agent.py` (`run`의 `env`)
- Modify: `specs/vxvue-chatbot.md` REQ-VXVUECHATBOT-007 3항, 11절 TEST-VXVUECHATBOT-004
- Test: `tests/test_vxvue_chat_cost.py`, `tests/test_vxvue_chat_knowledge.py`

**Interfaces:**
- Produces: `definition_ids(text: str) -> set[str]` — 청크 텍스트에서 `ID:\s*(VP-\d+)` 형태로 "정의하는" ID만 대문자로 돌려준다.
- Produces: `KnowledgeTools.batch_lookup(requests) -> {"results": [...], "scope": ..., "budget": {"limit_chars": int, "used_chars": int, "trimmed": [int]}}`
- Produces: 설정 `vxvue_chatbot.batch_char_limit` 기본 60000, `vxvue_chatbot.mcp_output_tokens` 기본 40000.

- [ ] **Step 1: 4a 재현(원인 확정)** — 합성 문서 대신 로컬 색인으로 `batch_lookup`에 검색 10개(`limit` 기본)를 넣어 응답 JSON 글자 수와 `claude_cli` 토큰 추정치를 기록한다. 실제 서버 CLI로 같은 묶음을 한 번 호출해 tool_result가 상한 오류인지 확인하고 결과를 `progress.md`에 적는다. 원인이 다르면 이 Task의 Step 6~7을 멈추고 사용자에게 보고한다.
- [ ] **Step 2: 실패하는 테스트(4b)** — `test_definition_chunk_ranks_first`: 합성 색인에 언급 청크 7개(`… (VP-656 - Theme) …`, 문서명이 알파벳 앞)와 정의 청크 1개(`07-10-40 Theme\nID: VP-656 Status: draft`)를 넣고 `search_documents("VP-656", limit=6)["results"][0]["chunk_id"]`가 정의 청크다. 같은 결과에 `"definition_match": True`. `test_prefix_id_is_not_definition`: 정의 청크가 `ID: VP-6568`이면 `VP-656` 검색의 정의로 올리지 않는다. `test_definition_ranks_first_with_extra_words`: `"Setting System Theme VP-656 상세"`에서도 정의 청크가 1순위.
- [ ] **Step 3: 실패하는 테스트(4a)** — `test_batch_respects_char_budget`: 결과마다 근거 1,800자짜리 가짜 corpus로 10개 요청을 묶으면 `len(json.dumps(result, ensure_ascii=False)) <= 60000`, 잘린 요청 번호가 `budget.trimmed`에 있고 그 요청의 각 결과에 `evidence_truncated: True`와 이어 읽을 `chunk_id`가 남는다. `test_batch_drops_repeated_limitations`: 둘째 요청부터 `limitations`가 없고 최상위에 한 번만 있다. `test_cli_env_sets_mcp_output_tokens`: `run`이 만든 `env["MAX_MCP_OUTPUT_TOKENS"] == "40000"`.
- [ ] **Step 4: 실행 → FAIL** `pytest tests/test_vxvue_chat_cost.py tests/test_vxvue_chat_knowledge.py -k "definition or prefix or budget or limitations or output_tokens" -v`
- [ ] **Step 5: 검색 순위 수정** — `search_documents`에서 질의에 VP ID가 있으면(`identifiers(query)`의 VP 값, `_`·공백을 `-`로 정규화) 그 ID를 `definition_ids(row text)`로 정의하는 청크에 `+1000`점, 단어 경계가 맞는 언급에만 기존 점수를 준다(`VP-6568`은 `VP-656` 언급이 아니다). 정의 청크는 카테고리 분산(`chosen`)보다 앞에 둔다. 결과에 `definition_match` 불리언을 넣는다.
- [ ] **Step 6: 묶음 응답 상한** — `batch_lookup`은 결과를 쌓으며 글자 수를 센다. 상한을 넘기 시작하면 남은 요청의 각 결과 `evidence`를 앞 300자로 줄이고 `evidence_truncated: True`로 표시한다. 그래도 넘으면 그 요청은 `{"tool", "is_error": false, "deferred": true, "reason": "응답 크기 상한 — 개별 호출로 다시 조회"}`로 바꾼다. `limitations`는 최상위 한 번만 남긴다. 근거를 지우지 않고 다시 읽는 길(`chunk_id`, `get_document`)을 남긴다.
- [ ] **Step 7: CLI 상한 설정** — `agent.run`에서 `env["MAX_MCP_OUTPUT_TOKENS"] = str(settings.get("vxvue_chatbot.mcp_output_tokens", 40000))`. 프롬프트에 "`deferred`나 `trimmed`가 있으면 그 요청만 개별 호출로 이어 조회한다. 이를 '확인하지 못한 범위'로 바로 보고하지 않는다." 한 줄 추가.
- [ ] **Step 8: 실행 → PASS** Step 4 명령, 이어서 `pytest tests/test_vxvue_chat_cost.py tests/test_vxvue_chat_knowledge.py -v` 전체 PASS.
- [ ] **Step 9: 실제 확인** — 로컬 색인으로 `search_documents("VP-656")` 1순위가 `(사양서) VXvue 사양서4(261013).pdf::page:13`인지 출력으로 확인한다.
- [ ] **Step 10: SPEC·Commit** — REQ-VXVUECHATBOT-007 3항에 "묶음 응답은 설정한 글자 상한(기본 60,000자) 안에서 돌려주고, 넘친 요청은 줄인 근거와 이어 읽을 위치를 남긴다", REQ-VXVUECHATBOT-005 순서 3에 "ID 질의는 그 ID를 정의한 사양 항목을 먼저 보인다"를 추가. `git commit -m "REQ-VXVUECHATBOT-005·007: ID 정의 우선 검색, 묶음 조회 크기 상한"`

### Task 3: AI가 판단한 대화 제목

**Files:**
- Modify: `app/core/vxchat_storage.py` (테이블 열 `title_source`, `vxchat_rename`, `vxchat_submit`, 새 메서드)
- Modify: `app/modules/vxvue_chatbot/agent.py` (`history_prompt`, 새 함수 `split_title`)
- Modify: `app/modules/vxvue_chatbot/service.py` (완료 처리)
- Modify: `app/prompts/vxvue_chatbot.yaml`
- Modify: `specs/vxvue-chatbot.md` REQ-VXVUECHATBOT-002 순서 3
- Test: `tests/test_vxvue_chat_storage.py`, `tests/test_vxvue_chat_agent.py`, `tests/test_vxvue_chat_service.py`

**Interfaces:**
- Produces: `vxchat_conversations.title_source TEXT NOT NULL DEFAULT 'auto'` — 값 `auto` | `ai` | `user`. 기존 DB는 `ALTER TABLE … ADD COLUMN`으로 올리고 기존 행은 `auto`.
- Produces: `Storage.vxchat_set_ai_title(conversation_id: str, title: str) -> bool` — `title_source != 'user'`일 때만 바꾸고 `title_source='ai'`.
- Produces: `split_title(text: str) -> tuple[str, str | None]` — 답변 마지막 줄 `<!-- title: … -->`를 떼어 (본문, 제목)을 돌려준다. 제목은 앞뒤 공백 제거, 1~40자, 줄바꿈 없음일 때만 유효.
- Consumes: `history_prompt`가 넣는 `"title_needed": bool`.

- [ ] **Step 1: SPEC** — REQ-VXVUECHATBOT-002 순서 3을 "대화 제목은 첫 질문으로 임시로 채운다. 첫 답변이 끝나면 AI가 대화 내용을 보고 정한 짧은 제목(40자 이내)으로 바꾼다. 사용자가 바꾼 제목은 AI가 바꾸지 않는다. 같은 답변 실행에서 제목을 받으며 별도 AI 호출을 하지 않는다."로 고친다.
- [ ] **Step 2: 실패하는 테스트** — `test_auto_title_skips_user_title`(rename 뒤 `vxchat_set_ai_title`이 False, 제목 유지), `test_ai_title_replaces_auto_title`, `test_split_title_extracts_marker`(`"답\n<!-- title: Theme 설정 사양 -->"` → `("답", "Theme 설정 사양")`), `test_title_marker_missing_or_invalid`(표식 없음·41자·줄바꿈 → 본문 그대로, 제목 None), `test_history_prompt_requests_title_once`(어시스턴트 답이 없는 첫 질문만 `title_needed: true`), `test_service_stores_ai_title`(가짜 runner 결과에 표식 → 저장된 메시지에 표식 없음, 대화 제목 변경).
- [ ] **Step 3: 실행 → FAIL** `pytest tests/test_vxvue_chat_storage.py tests/test_vxvue_chat_agent.py tests/test_vxvue_chat_service.py -k title -v`
- [ ] **Step 4: 구현** — `vxchat_rename`은 `title_source='user'`도 저장. `agent.run`은 최종 답변에 `split_title`을 적용해 결과 dict에 `"title"`을 넣는다. 스트리밍 중 표식이 화면에 잠깐 보이지 않게 `progress`로 보내는 본문에서도 `<!-- title:` 이후를 잘라 보낸다. `service`는 DONE 저장 뒤 `title`이 있으면 `vxchat_set_ai_title`. 프롬프트: "`title_needed=true`이면 답변 맨 끝 줄에 `<!-- title: 대화 주제 -->`를 한 번 쓴다. 40자 이내, 제품·기능 이름 중심."
- [ ] **Step 5: 실행 → PASS**, 이어서 `pytest tests -k vxvue_chat -q` 전체 PASS.
- [ ] **Step 6: Commit** `git commit -m "REQ-VXVUECHATBOT-002: 첫 답변에서 AI가 정한 대화 제목"`

### Task 4: 크게 보이는 진행 표시

**Files:**
- Modify: `app/modules/vxvue_chatbot/templates/chat.html`, `app/web/static/vxvue-chatbot.js`, `app/web/static/vxvue-chatbot.css`
- Modify: `specs/vxvue-chatbot.md` REQ-VXVUECHATBOT-001 순서 3
- Test: `tests/test_vxvue_chat_router.py`

**Interfaces:** Consumes: 기존 SSE `job.stage`(`접수`·`대기`·`자료 검색`·`원문 확인`·`답변 작성`).

- [ ] **Step 1: SPEC** — 순서 3을 "답변 자리에 진행 카드가 보인다. 카드는 큰 표시와 함께 접수 → 대기 → 자료 검색 → 원문 확인 → 답변 작성 단계 중 지금 단계와 경과 시간, 중지 버튼을 보인다. 답변 글이 들어오기 시작하면 카드는 답변 위 한 줄로 줄어든다."로 고친다.
- [ ] **Step 2: 실패하는 테스트** — `test_chat_page_has_progress_card`: `/vxvue-chatbot` HTML에 `id="job-progress"`와 단계 5개 `data-stage` 항목이 있다.
- [ ] **Step 3: 구현** — 진행 중인 어시스턴트 메시지 자리에 `#job-progress` 카드(spinner 40px 이상, 단계 표시줄, 경과 시간, 중지)를 그린다. 단계 강조는 SSE `stage` 값으로만 바꾼다. 입력창 위 작은 `#job-status`는 없앤다. 390px에서 카드가 화면 폭을 넘지 않는다. `?v=` 번호를 올린다.
- [ ] **Step 4: 실행 → PASS** `pytest tests/test_vxvue_chat_router.py -v`
- [ ] **Step 5: 화면 확인** — 개발 서버에서 가짜 실행으로 질문 하나를 보내 데스크톱·390px 화면 캡처로 카드 크기와 단계 변화를 확인한다.
- [ ] **Step 6: Commit** `git commit -m "REQ-VXVUECHATBOT-001: 답변 자리 진행 카드"`

### Task 5: 상단 Claude 사용량(5시간·주간)

**Files:**
- Modify: `app/modules/vxvue_chatbot/templates/chat.html`, `app/web/static/vxvue-chatbot.js`, `app/web/static/vxvue-chatbot.css`
- Modify: `app/modules/vxvue_chatbot/agent.py` (stream의 `rate_limit_event` 수집)
- Modify: `app/modules/vxvue_chatbot/service.py` (완료 뒤 `claude_usage.record_rate_limit_event`)
- Modify: `SPEC.md` REQ-AICALL-006 "언제"
- Test: `tests/test_vxvue_chat_agent.py`, `tests/test_vxvue_chat_router.py`

**Interfaces:**
- Consumes: `GET /claude/usage` → `{source, five_hour: {used_percent, remaining_percent, resets_at} | None, seven_day: …, measured_at, error}`; `claude_usage.record_rate_limit_event(info: dict)`.
- Produces: `agent.run` 결과에 `"rate_limit": dict | None`.

- [ ] **Step 1: SPEC** — REQ-AICALL-006 "언제"에 "VXvue AI 챗봇(`/vxvue-chatbot`) 상단에서도 같은 값을 작게 보인다. 답변이 끝날 때마다 다시 불러온다."를 추가한다. 챗봇 실행이 받은 `rate_limit_event`도 마지막 점검 값으로 남긴다는 문장을 순서에 추가한다.
- [ ] **Step 2: 실패하는 테스트** — `test_run_returns_rate_limit_event`(가짜 CLI stream에 `{"type":"rate_limit_event","rate_limit_info":{…}}` → 결과 `rate_limit` 동일), `test_chat_page_has_usage_chips`(HTML에 `data-window="five_hour"`, `data-window="seven_day"`).
- [ ] **Step 3: 구현** — 상단 바(`chat-topbar`)의 `Claude` 배지 자리에 사용량 칩 두 개: "5시간 35% · 15:00 초기화", "주간 62% · 10/20 09:00 초기화". 80% 이상 주황, 100% 빨강. 처음 열 때·답변 완료 때·5분마다 `/claude/usage`를 부른다. 값이 없으면 "사용량 알 수 없음"과 `error`를 title로. 칩 표시 규칙은 `_claude_usage.html`의 퍼센트·색 기준을 그대로 따른다.
- [ ] **Step 4: 실행 → PASS** `pytest tests/test_vxvue_chat_agent.py tests/test_vxvue_chat_router.py tests/test_claude_usage.py -v`
- [ ] **Step 5: Commit** `git commit -m "REQ-AICALL-006: 챗봇 상단 Claude 5시간·주간 사용량"`

### Task 6: 지식 상태 토글(매뉴얼·사양서 자동 업데이트·Skill 버전)

**Files:**
- Modify: `app/modules/vxvue_chatbot/router.py` (`knowledge_status`)
- Modify: `app/modules/vxvue_chatbot/templates/chat.html`, `app/web/static/vxvue-chatbot.js`, `app/web/static/vxvue-chatbot.css`
- Modify: `specs/vxvue-chatbot.md` REQ-VXVUECHATBOT-006 순서 4, REQ-VXVUECHATBOT-008 6항
- Test: `tests/test_vxvue_chat_router.py`

**Interfaces:** Produces: `GET /vxvue-chatbot/knowledge-status`에 필드 추가(기존 필드는 유지):
- `manuals: [{"name": str, "status": "ready" | "failed" | "excluded", "modified_at": str | None, "reason": str}]` — manifest `assets`(kind=`manual`)와 `excluded` 중 매뉴얼.
- `specification_sync: {"last_success_at": str | None, "last_attempt_at": str | None, "status": str | None, "schedule": "평일 09:40"}` — `latest_sync("VXvue","specification")`와 마지막 SUCCESS.
- `skill`: 기존 `{version, status, code_review_needed}` + `"synced_at": str | None`(skill 자산 `modified`).

- [ ] **Step 1: SPEC** — REQ-VXVUECHATBOT-006 순서 4를 "상단에는 지식 문서 수와 마지막 정상 동기화만 한 줄로 보인다. 펼치면 매뉴얼별 상태·마지막 업데이트 일시, 사양서 자동 업데이트(평일 09:40) 마지막 성공·시도 일시, 적용 중인 Skill 최신 버전과 동기화 일시를 보인다. 실패·읽기 오류가 있으면 접힌 줄에도 경고로 보인다."로 고친다. 실행 시각 문구(`매일 08:00 / 실패 시 15분 재시도`)와 제외 파일 목록은 접힌 줄에서 뺀다. 제외 파일은 펼친 매뉴얼 목록에 `제외` 상태로만 보인다.
- [ ] **Step 2: 실패하는 테스트** — `test_knowledge_status_lists_manuals`(manifest 매뉴얼 2개+제외 1개 → `manuals` 3개, 제외 항목 status `excluded`, `modified_at` 원본 값), `test_knowledge_status_spec_sync`(`sync_log`에 specification SUCCESS·FAILED 기록 → `last_success_at`은 SUCCESS 시각, `status`는 마지막 FAILED), `test_knowledge_status_skill_synced_at`.
- [ ] **Step 3: 실행 → FAIL** `pytest tests/test_vxvue_chat_router.py -k knowledge_status -v`
- [ ] **Step 4: API 구현** — 위 Interfaces대로 필드를 더한다. 사유 문구는 `mask_text`를 거친다.
- [ ] **Step 5: 화면 구현** — `#knowledge-status`를 `<details>`로 바꾼다. 접힌 줄: "서버 지식 22개 · 마지막 정상 동기화 2026. 10. 13. 오후 5:38" (+ 경고가 있으면 "· 확인 필요"). 펼친 내용: ① 매뉴얼 표(이름 · 상태 · 마지막 업데이트) ② 사양서 자동 업데이트(평일 09:40 · 마지막 성공 · 마지막 시도 결과) ③ Skill 최신 버전 0.3.2 · 동기화 일시 · 도구 검토 필요 여부. 열림 상태는 `localStorage`에 try/catch로 기억한다.
- [ ] **Step 6: 실행 → PASS** `pytest tests/test_vxvue_chat_router.py -v`
- [ ] **Step 7: Commit** `git commit -m "REQ-VXVUECHATBOT-006·008: 지식 상태 토글과 매뉴얼·사양서·Skill 갱신 일시"`

### Task 7: 문서·검증·게시

**Files:** `CHANGELOG.md`, `progress.md`, `app/modules/vxvue_chatbot/templates/guide.html`, `docs/SPEC.html`, `docs/README.md`

- [ ] **Step 1:** `guide.html`에 진행 카드·사용량 칩·지식 토글·자동 제목 설명을 더한다.
- [ ] **Step 2:** `CHANGELOG.md`에 REQ-ID별 항목, `progress.md`에 4a 재현 결과와 실제 확인 결과를 적는다. `docs/README.md` 문서 지도에 이 계획을 연결한다.
- [ ] **Step 3:** `node .project-check/render-spec-html.js .`
- [ ] **Step 4:** 키트 정리 `node C:/Users/2024980/Documents/자동화/tools/cleanup-workspace.js --completed <프로젝트 루트> --apply --auto`
- [ ] **Step 5:** `pytest -q` 전체 PASS, `node .project-check/project-readiness.js .` 통과.
- [ ] **Step 6:** 실제 동작 — 개발 서버에서 `VP-656 Theme 사양 알려줘`를 질문해 ① 답변이 VP-656 정의(사양서4 p.13)를 인용, ② Legacy No.·draft 단서 없음, ③ 제목이 내용 기반으로 바뀜, ④ 진행 카드·사용량 칩·토글 표시를 캡처로 확인한다. 실제 Claude CLI 호출은 사용량을 쓰므로 1회만 한다.
- [ ] **Step 7:** Akela `log applied`(REF-core-web-ui#progress-sse, #static-assets, #template-inheritance), `log outcome`.
- [ ] **Step 8:** 커밋·push (worktree 브랜치 → master 반영은 기존 운영 절차를 따른다).

## 확정된 결정 (2026-10-14 사용자 확인)

1. 제외된 매뉴얼은 접힌 줄에서 빼고 펼친 매뉴얼 목록에 `제외` 상태로 보인다.
2. Skill 최신 버전은 서버가 마지막으로 동기화받은 버전과 동기화 일시로 보인다. PC의 더 새 설치본은 다음 08:00 동기화 뒤 반영된다.
3. 사용량 칩은 서버 Claude 계정 하나의 사용률이다(챗봇과 일일 점검 공용).
4. 실행 방식: Subagent-driven.

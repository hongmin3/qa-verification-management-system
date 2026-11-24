# QA 서버 장애 시 로컬 전환 Implementation Plan

> **For agentic workers:** 사용자가 계획→구현→검증을 한 번에 지시했으므로 이 세션에서 직접(Native) 실행한다.

**Goal:** QA 서버(10.13.0.222:24357) 장애 때 ALM·PLM·VXvue·XIPL·Bellalun Skill이 Claude Code·Codex 양쪽에서 로컬 자료·기존 로컬 경로로 자동 전환되고, 복구되면 서버 우선으로 돌아간다.

**Architecture:** 사용자 PC 전용 공통 Skill `qa-server-fallback` 하나(원본: 이 저장소 `client/qa-server-fallback/`, 설치본: `~/.codex/skills/qa-server-fallback`, Claude는 Junction)가 장애 판정(probe)·로컬 검색(Bellalun 검색 엔진을 라이브러리로 재사용, 제품별 루트·캐시 분리)·설치/점검(overlay)을 맡는다. 서버 배포 Skill에는 짧은 표시 블록(overlay)만 덧붙이고, `sync_server_skills.py`가 그 블록을 보존하며, 전역 지침(CLAUDE.md·Codex AGENTS.md)의 한 블록이 Skill 교체 후에도 공통 Skill을 가리킨다.

**Tech Stack:** Python 3.13 표준 라이브러리(urllib), 기존 qa-client venv(pypdf), pytest.

**Spec:** `specs/qa-client-fallback.md` (REQ-QACLIENTFALLBACK-001~005)

## Preflight 기록

- 읽은 것: 설치본 6종 SKILL.md·server.json·knowledge.py(ALM/PLM), `bellalun-local-knowledge/scripts/knowledge.py`(`--root`/`--cache` 지원, `source_files` 모듈 함수), `scripts/install_qa_client.py`, `scripts/sync_server_skills.py`(예약 `QA_ServerSkills_Sync`가 매일 서버와 바이트 일치로 덮어씀), `specs/bellalun-shared.md` REQ-003(서버 Skill ZIP에 개인 경로 금지)·REQ-006.
- 재사용: Bellalun `knowledge.py`의 refresh/search/get_units/render_pdf_page/docx_revisions를 import해 사용(엔진 미수정). `source_files`만 제품 포함/제외 규칙으로 감싼다.
- 새로 만드는 이유: 장애 판정·제품별 로컬 루트·overlay 보존 기능이 기존 코드에 없음. 서버 배포 Skill에는 개인 경로를 넣을 수 없음(REQ-BELLALUNSHARED-003) → 클라이언트 전용 Skill.
- 바꾸지 않는 것: MCP 설정·인증정보·bellalun-ai bridge(server.py)·Bellalun 엔진·서버 Skill의 기존 본문과 name/설치 표시.
- 검증·게시: `.venv/Scripts/python.exe -m pytest -q`, `node .project-check/project-readiness.js .`, 내 파일만 커밋(다른 세션의 미커밋 변경 제외).

## Global Constraints

- 재시도: 기본 3회, 간격 1초, 시도당 timeout 5초, 전체 상한 30초. 인자 상한 attempts≤5, timeout≤15.
- 판정: 연결 거부·timeout·DNS·호스트 도달 불가·HTTP 5xx → `outage`(자동 로컬 전환). HTTP 401/403/407 → `auth_error`, 해시·형식 불일치 → `integrity_error`, 그 밖 4xx → `server_error`: 모두 전환하지 않고 중단.
- 한 작업은 시작 시 정한 모드(server|local)와 그 판만 사용한다. 새 작업마다 probe를 다시 한다(상태 고정 없음).
- 캐시: `~/.local/state/qa-server-fallback/cache/<product>`; Bellalun은 기존 `.cache` 그대로.
- overlay 표시: `<!-- qa-server-fallback:begin -->` … `<!-- qa-server-fallback:end -->`, 파일 끝에 1개.

## Shared Interfaces

### Contract: probe 결과(JSON)
`{"checked_at", "targets": {"guide:alm"|"guide:plm"|"mcp": {"state": "ok|outage|auth_error|integrity_error|server_error|config_error", "attempts", "elapsed_s", "detail"}}, "decision": {"alm"|"plm"|"vxvue"|"xipl"|"bellalun": "server|local|stop"}}` — 종료값 0(모두 server), 10(local 포함), 20(stop 포함), 1(도구 오류).

### Contract: fallback-config.json
`{"python_executable", "engine", "server_base", "state_dir", "products": {<p>: {"root", "include", "exclude", "cache", "required", "not_local"}}}`

## Review Focus

1. 서버가 느리게 응답 → 시도당 timeout으로 끊기고 outage. (Task 2)
2. 401만 나오는 서버 → 로컬 전환 금지. (Task 2)
3. VXvue `_이전판/` 자료가 검색에 섞임 → 제외 표시·검색 제외. (Task 3)
4. 서버 동기화가 overlay를 지움 → sync가 보존하고 check가 OUTDATED로 보지 않음. (Task 5)
5. 지식 폴더 없음/필수 자료 누락 → `missing`으로 표시, 오류로 숨기지 않음. (Task 3)

---

### Task 1: SPEC 기능 문서 — `specs/qa-client-fallback.md`
### Task 2: probe — `client/qa-server-fallback/scripts/qa_fallback.py`, `tests/test_qa_server_fallback.py` (127.0.0.1 실제 HTTP 서버로 ok/5xx/401/느린 응답/닫힌 포트/해시 불일치, 재시도·상한)
### Task 3: 로컬 자료 — `local status|search|get|page|revisions --product` (합성 지식 폴더로 검색·원문·제외·누락·캐시 분리)
### Task 4: 설치·overlay — `install`/`check`/`overlay` (임시 홈에서 멱등, 식별자 보존, 백업, Junction)
### Task 5: sync 보존 — `scripts/sync_server_skills.py` + 테스트 2건
### Task 6: 실제 설치·실제 장애 검증·CLI 호출·문서·커밋

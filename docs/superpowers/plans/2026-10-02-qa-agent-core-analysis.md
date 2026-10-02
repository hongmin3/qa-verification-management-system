# QA Agent 핵심 분석 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자동 분석은 "무엇이 바뀌었고 QA 가 무엇을 봐야 하나"만 짧게 답하고, TC·Checklist·매뉴얼과 비교하는 분석은 사람이 버튼을 누를 때만 돈다. 화면은 결론 한 단어와 한 줄 요약과 QA 할 일만 앞에 보인다.

**Architecture:** 이벤트 감지·대기열·분석 종류(`SPEC_COVERAGE`, `FIXED_ISSUE` 등)의 경로는 그대로 둔다. 자동 실행이 쓰는 Skill 과 입력만 가볍게 바꾼다(TC 후보·매뉴얼 후보·`context/` 의 TC·매뉴얼 파일 제거). 무거운 Skill(`qa-spec-coverage-analysis`, 검증 TC 초안)은 지우지 않고 새 실행 모드 `--on-demand` 로만 부른다. 화면의 결론 단계는 저장된 판정 값을 코드 표로 바꿔 보인다.

**Tech Stack:** Python 3, FastAPI + Jinja2, SQLite, Claude CLI(Skill), pytest.

**Spec:** `specs/qa-intelligence.md`(REQ-QAINTEL-010~020·030, 새 031~034), `SPEC.md` 5.4절(REQ-DAILY-001·005·006·011·024).

## 사용자 결정 (2026-10-02)

### 재개 후 확인 상태

이하 체크박스는 중단 당시 계획이다. 실제 구현·검증 결과는 `progress.md`의 2026-10-02 항목을 기준으로 확인한다.

| 계획 | 재개 후 상태 |
|---|---|
| Task 1 자동 입력 분리·사양 변경 요약 | 구현. 빈 요약 거절·재사용 폴더 입력 정리도 보완 |
| Task 2 자동 매뉴얼 점검 제거 | 구현. 기존 상태값은 보존 |
| Task 3 요청 실행 | TC 점검·초안 버튼 구현. 매뉴얼 화면 버튼은 최종 사용자 결정에 따라 제거하고 수동 CLI만 유지 |
| Task 4 간결한 화면 | 구현. 과거 Finding 상세도 펼쳐 볼 수 있음 |
| 최종 검증·문서·게시 | 전체 회귀 1180 passed·1 skipped, 마지막 한도 안내 관련 76 passed, 준비 검사 failed=0. 커밋·게시 상태는 Git과 진행 문서 참조 |

AI를 호출하는 시험 실행은 사용자가 의미 설명을 요청해 보류했다. 기존 입력 확인용 시험 실행을 유지한다.

1. 화면은 핵심만. 결론·한 줄 요약·QA 할 일.
2. 변경마다 Checklist TC 분석을 자동으로 하지 않는다. TC·매뉴얼은 사람이 넣는 자료라 틀릴 수 있다.
3. 수정 완료 이슈의 검증 TC 초안은 버튼으로만 만든다.
4. 매뉴얼 점검은 무조건 수동이다. 자동으로 도는 부분을 없앤다.
5. 사양–TC 연결 점검(E)은 사용자 설명 요청 중. 이 계획은 E 를 바꾸지 않는다.

## Project Preflight 기록

- 읽은 것: 프로젝트 `AGENTS.md`, `akela/PROTOCOL.md`, `specs/qa-intelligence.md` REQ-QAINTEL-010~020·028·030, `SPEC.md` REQ-DAILY-005·006, `app/modules/daily_qa/{intelligence,pipeline,issue_audit,schema,workspace,scheduled_jobs}.py`, `app/modules/qa_agent/{dashboard,router}.py`, `templates/finding_detail.html`, Skill 9개 중 공통·FIX·COV·NEW·AUD.
- 재사용: `launch_detached`(버튼 실행), `_Lock`·`is_running`(겹침 방지), `build_item`(입력), `findings_box.save`(저장), 기존 Coverage Skill·FIX Skill 본문(버튼 모드로 옮김).
- 실측 근거: 10-01·10-02 실행의 `audit.json` — COV 작업 176만~516만 토큰·25~48회 왕복, FIX 80만, CMT 16만. 공통 규칙이 `context/tc_index.jsonl`·`context/manuals/` 검색을 허용한다.
- 검증 규칙: `botyard.json` verify(전체 pytest), `node .project-check/project-readiness.js .`, SPEC 수정 뒤 `node .project-check/render-spec-html.js .`, 실제 화면 확인, CHANGELOG·progress 갱신 후 커밋·push.

## Global Constraints

- 자동 실행(예약·따라잡기·[지금 실행]·기간 실행·현재 상태 점검)의 AI 입력에는 TC 후보·매뉴얼 후보를 넣지 않고, 작업 폴더 `context/` 에 `tc_index.jsonl`·`manuals/` 를 쓰지 않는다.
- 결론 단계 이름은 다섯 개다(준비 검사가 `확인 필요` 를 미확정 표시로 읽어 `검토 필요` 를 쓴다): `사양과 다름`, `검토 필요`, `근거 부족`, `참고`, `문제 없음`. 목록 정렬도 이 순서다.
- 카드의 QA 할 일은 3줄까지, 요약은 한두 문장이다.
- 버튼 실행은 실행 잠금을 같이 쓰고, 이미 실행 중이면 409 "QA Agent가 이미 실행 중입니다." 다.
- 기존 Finding·이벤트·DB 표는 지우거나 바꾸지 않는다. 새 값만 더한다.

## Review Focus

1. 개편 전에 저장된 Coverage Finding(21건, 칩·TC 표 구획이 있는 것)이 새 카드·상세에서 깨지지 않고 결론 단계로 보이는가 → Task 4 테스트.
2. 사용량 한도 상태에서 버튼을 누르면 Claude 를 부르지 않고 "한도" 로 끝나며 원래 Finding 은 그대로인가 → Task 3 테스트.
3. 버튼 대상 Finding 의 이벤트가 이미 `done` 이어도 버튼 실행이 이벤트 상태를 바꾸지 않는가 → Task 3 테스트.
4. 자동 실행의 `context/` 에 이전 실행이 남긴 `tc_index.jsonl`·`manuals/` 가 남아 AI 가 읽게 되지 않는가(작업 폴더는 실행 사이에 재사용된다) → Task 1 테스트.
5. 매뉴얼이 바뀐 날·주간 요일·`manual_check_due` 가 남아 있는 DB 에서도 자동 실행이 F 를 돌리지 않는가 → Task 2 테스트.

---

### Task 1: 자동 실행 입력에서 TC·매뉴얼 빼기 + 가벼운 Skill

**Files:**
- Modify: `app/modules/daily_qa/intelligence.py` (`build_item` 에 `with_tc: bool = False`; False 면 `candidates.tcs`·`candidates.manuals` 없음, `regression_axes` 는 유지)
- Modify: `app/modules/daily_qa/issue_audit.py` (`build_tasks` 의 A-수정 TC 후보 제거)
- Modify: `app/modules/daily_qa/workspace.py` (`write_context(run, srs_items, tc_rows, manuals)` 에서 빈 목록이면 `tc_index.jsonl`·`manuals/` 를 지운다)
- Modify: `app/modules/daily_qa/pipeline.py` (자동 실행은 `write_context(..., [], {})`, `build_item(..., with_tc=False)`; Skill 선택 `SPEC_COVERAGE → qa-spec-change-summary`)
- Create: `app/modules/daily_qa/skills/qa-spec-change-summary/SKILL.md`
- Modify: Skill `qa-common-rules`(TC·매뉴얼 검색 허용 문장 삭제, 요약·할 일 길이 규칙), `qa-fixed-issue-analysis`(6·7단계 삭제), `qa-new-issue-analysis`(TC 후보 문장 삭제), `qa-issue-spec-audit`(TC 영향 삭제), `qa-common-rules/references/output-contract.md`
- Modify: `app/modules/daily_qa/schema.py` (`SKILL_SPEC_SUMMARY = "qa-spec-change-summary"`, VERDICTS `("NO_QA_IMPACT", "QA_CHECK_NEEDED", "CONFLICTS_WITH_PAST_DECISION", "SPEC_UNCLEAR")`, 라벨 "사양 변경 분석")
- Modify: `app/modules/daily_qa/evidence_validation.py` (새 Skill 구획 검사: `change.summary` 필수, `related_issues[].issue_id` 는 입력 번호만; FIXED 자동 결과의 `draft_tcs` 는 버리고 메모를 남긴다; AUDIT 의 `tc_impact` 는 버린다)
- Test: `tests/test_qa_intel_core_mode.py` (새), 기존 테스트 중 자동 입력의 `tcs` 를 기대하는 것 수정

**Interfaces:**
- Produces: `build_item(target, corpus, known, cfg, comments_loader=None, with_tc: bool = False) -> dict | None`, `SKILL_SPEC_SUMMARY`, `ANALYSIS_SKILLS[SPEC_COVERAGE] == "qa-spec-change-summary"`

- [ ] Step 1: 실패 테스트 작성 — `test_auto_inputs_have_no_tc_or_manual_candidates`(NEW·FIX·COV·AUD 입력에 `tcs`·`manuals` 키 없음), `test_auto_context_has_no_tc_index_or_manuals`(이전 실행이 남긴 파일도 지워짐), `test_spec_change_uses_summary_skill`, `test_fixed_issue_auto_drafts_are_dropped`.
- [ ] Step 2: `pytest tests/test_qa_intel_core_mode.py -q` → FAIL 확인.
- [ ] Step 3: 구현.
- [ ] Step 4: 새 테스트 PASS, 그다음 전체 pytest 로 깨진 기존 테스트를 SPEC 기준으로 고친다(자동 입력 TC 기대 → 버튼 모드 테스트로 옮김).

### Task 2: 매뉴얼 점검(F) 자동 실행 없애기

**Files:**
- Modify: `app/modules/daily_qa/pipeline.py` (`execute()` 에서 `_manual_check` 호출 제거; 단계 `F` = `not_due` "매뉴얼 점검은 사람이 요청할 때만 돕니다."; `manual_check_due`·매뉴얼 해시 상태를 쓰지 않음)
- Modify: `scripts/run_daily_qa.py` (`--weekly` 는 E 만, 도움말 수정)
- Test: `tests/test_qa_intel_core_mode.py`

- [ ] Step 1: 실패 테스트 — `test_auto_run_never_runs_manual_check`(주간 요일 + 매뉴얼 해시 변경 + `manual_check_due=1` 이어도 F 작업 0, FakeRunner 호출에 F 없음).
- [ ] Step 2: FAIL 확인 → 구현 → PASS.

### Task 3: 버튼 실행 — TC 점검·검증 TC 초안·매뉴얼 점검

**Files:**
- Modify: `app/modules/daily_qa/pipeline.py` (`run_daily(..., on_demand: str = "", finding_id: int | None = None)`; `_Run._execute_on_demand()`: 저장 스냅샷만 쓰고, TC 색인·매뉴얼을 읽어 `context/` 에 쓰고, 대상 하나로 작업 하나를 돈다. 이벤트 상태는 바꾸지 않는다)
- Modify: `scripts/run_daily_qa.py` (`--on-demand {tc-check,tc-draft,manual-check}`, `--finding <id>`)
- Modify: `app/modules/daily_qa/scheduled_jobs.py` (`launch_detached(..., on_demand: str = "", finding_id: int | None = None)`)
- Create: `app/modules/daily_qa/skills/qa-verification-tc-draft/SKILL.md` (FIX Skill 에서 뺀 6·7단계 + 입력 `candidates.tcs`)
- Modify: `app/modules/qa_agent/router.py` (`POST /qa-agent/findings/{id}/tc-check`, `POST /qa-agent/findings/{id}/tc-draft`, `POST /qa-agent/manual-check`; 응답 202/409/400)
- Test: `tests/test_qa_intel_on_demand.py` (새)

**Interfaces:**
- Consumes: `build_item(..., with_tc=True)` (Task 1)
- Produces: 새 Finding `analysis_type` 값 `TC_CHECK`(Skill `qa-spec-coverage-analysis`), `TC_DRAFT`(Skill `qa-verification-tc-draft`), `MANUAL_CHECK`(Skill `qa-manual-completeness`); `sections.source_finding = <원래 Finding id>`

| 버튼 | 원래 Finding 조건 | 없을 때 응답 |
|---|---|---|
| TC 점검 | `analysis_type == SPEC_COVERAGE` | 400 "사양 변경 분석에서만 TC 점검을 할 수 있습니다." |
| 검증 TC 초안 | `analysis_type == FIXED_ISSUE` 이고 이슈의 오늘 연구소 결과가 `FIXED` | 400 "수정 완료 이슈 분석에서만 검증 TC 초안을 만들 수 있습니다." |
| 매뉴얼 점검 | 없음(최근 7일 SRS 변경 전체) | — |

- [ ] Step 1: 실패 테스트 — `test_tc_check_runs_one_coverage_task_with_tc_candidates`, `test_tc_draft_requires_fixed_issue`, `test_on_demand_keeps_event_status`, `test_on_demand_under_limit_calls_no_claude`, `test_manual_check_button_runs_f_only`, 라우터 202/400/409.
- [ ] Step 2: FAIL 확인 → 구현 → PASS.

### Task 4: 화면 — 결론 단계·세 줄 카드·짧은 상세

**Files:**
- Modify: `app/modules/qa_agent/dashboard.py` (`level(finding: dict) -> str` 와 `LEVEL_ORDER`; `card()` 는 `level`, `summary`, `todo: list[str]`(최대 3), 칩 목록 제거; `dashboard()` 는 단계 순서로 정렬)
- Modify: `app/modules/qa_agent/templates/dashboard.html` (카드: 종류·번호·제목 / 단계 배지 / 요약 / 할 일. `문제 없음` 은 접은 묶음)
- Modify: `app/modules/qa_agent/templates/finding_detail.html` (맨 위: 결론·요약 → 바뀐 사양 → 근거 최대 3 → QA 할 일 → 버튼; 기존 종류별 구획은 `<details>자세히</details>` 안으로)
- Test: `tests/test_qa_intel_dashboard.py` 에 추가

결론 단계 표(코드와 SPEC 에 같은 값):

| 단계 | 판정 값 |
|---|---|
| 사양과 다름 | `SPEC_VIOLATION`, `CONTRADICTS_SPEC`, `CONFLICTS_WITH_PAST_DECISION` |
| 검토 필요 | `PARTIALLY_CONSISTENT`, `PARTIALLY_SUPPORTED`, `SPEC_UNDEFINED`, `SPEC_AMBIGUOUS`, `SPEC_NOT_FOUND`, `QA_CHECK_NEEDED`, `SPEC_UNCLEAR`, `PARTIALLY_COVERED`, `NOT_COVERED`, `SPEC_REVIEW_REQUIRED`, `UPDATE_EXISTING`, `QA_ACTION_REQUIRED`, `SPEC_CLAIM`, `REQUIREMENT_INFORMATION`, 그 밖에 표에 없는 값 |
| 근거 부족 | `INSUFFICIENT_EVIDENCE` |
| 참고 | `ROOT_CAUSE_INFORMATION`, `RESOLUTION_INFORMATION`, `REPRODUCTION_INFORMATION`, `OTHER_SIGNIFICANT_INFORMATION` |
| 문제 없음 | `CONSISTENT_WITH_SPEC`, `SUPPORTED_BY_SPEC`, `FULLY_COVERED`, `NO_QA_IMPACT`, `NOT_SIGNIFICANT` |

QA 할 일의 출처: `sections.recommendation.actions` → `sections.qa_analysis.checks` → `finding.action` 줄 나눔 → 없으면 빈 목록.

- [ ] Step 1: 실패 테스트 — `test_level_mapping_table`, `test_card_has_no_badges_and_at_most_three_todos`, `test_old_coverage_finding_renders_in_new_detail`, `test_dashboard_orders_by_level`.
- [ ] Step 2: FAIL → 구현 → PASS. 12000 서버에서 `/qa-agent`, `/qa-agent/findings/301` 을 390·1920px 로 보고 가로 스크롤·콘솔 오류 없음 확인.

### Task 5: 사양·문서·검증·게시

**Files:** `specs/qa-intelligence.md`, `SPEC.md`(5.4절·4절 일정·REQ-DAILY-001·005·006·011·024), `docs/SPEC.html`, `CHANGELOG.md`, `progress.md`, 화면 사용법(`/qa-agent/guide` 템플릿), `docs/README.md`(이 계획 문서 행)

- REQ-QAINTEL-013: 검증 TC 초안을 빼고 REQ-QAINTEL-032 로 연결.
- REQ-QAINTEL-016: "언제"를 [TC 점검] 버튼으로. 내용은 유지.
- 새 REQ-QAINTEL-031 사양 변경 분석(자동), 032 검증 TC 초안(요청), 033 결론 단계와 짧은 카드, 034 매뉴얼 점검(요청).
- REQ-DAILY-006: 자동 실행 조건 삭제, REQ-QAINTEL-034 로 연결. 12절 추적성·11절 테스트 사양 갱신.
- [ ] 전체 pytest, `node .project-check/render-spec-html.js .`, `node .project-check/project-readiness.js .`, 12000 서버 화면 확인(서버 재시작은 감시 작업으로).
- [ ] 실제 Claude 검증은 주간 한도(10-06 15:00 KST 초기화) 뒤 한 번. 그 전에는 "구현·자동 테스트 완료, 실제 AI 미검증"으로 보고.
- [ ] CHANGELOG·progress 갱신 → 커밋 → push.

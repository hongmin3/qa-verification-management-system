# 결과 JSON 형식

결과는 프롬프트가 알려 준 `runs/<실행ID>/out/<작업ID>.json` 한 파일이다. UTF-8 JSON 객체 하나.
코드 검증기: `app/modules/daily_qa/schema.py`(`SkillResult`), `app/modules/daily_qa/evidence_validation.py`.

```json
{
  "skill": "qa-new-issue-analysis",
  "task_id": "NEW-001",
  "gate_status": {"G1": "PASS", "G2": "PASS", "G3": "CHECK", "G7": "PASS"},
  "findings": [
    {
      "subject": "VP-30301",
      "subject_title": "Retake 후 Protocol 목록 갱신 안 됨",
      "verdict": "SPEC_VIOLATION",
      "summary": "Retake 뒤 Protocol 목록이 갱신되어야 한다는 사양과 달리 이전 목록이 남는다.",
      "detail": "검색어: 'Retake', 'Protocol List'. 사양 후보 3건 중 VP-767 이 해당.",
      "confidence": "Review Needed",
      "evidence": [
        {"source_type": "srs", "location": "VP-767 / 3번째 문장", "summary": "Retake 후 목록을 다시 읽는다", "validity": "Current"},
        {"source_type": "spec_doc", "ref": "spec:doc1-p12-3", "location": "사양서1 · p.12 · 4.3.2", "summary": "목록 갱신 규칙"}
      ],
      "action": "연구소에 Retake 이후 목록 갱신 조건 확인 요청",
      "draft_tcs": [],
      "sections": {"duplicate": {"verdict": "POSSIBLE_DUPLICATE", "candidates": [{"issue_id": "VP-29874", "similarity": "POSSIBLE", "reason": "같은 Retake 조건", "differences": "모달리티가 다름", "past_resolution": "SPEC"}]}}
    }
  ],
  "open_questions": [
    {"subject": "VP-30301", "question": "Retake 가 두 번이면 목록은 몇 번 갱신되는가?", "why": "Expected 를 정할 근거가 없다"}
  ],
  "next_recommended_skill": "",
  "human_review_required": true
}
```

## 공통 필드 규칙

- `skill`, `task_id`: 입력 작업과 정확히 같아야 한다. 다르면 작업 전체가 실패로 처리된다.
- `subject`: 작업 입력 `items[].target` 가운데 하나. 입력에 없는 대상의 결과는 버려진다.
- `verdict`: Skill 별 허용 값만 쓴다(아래 표).
- `confidence`: `Confirmed` / `Review Needed` / `Unsupported`. 근거가 없으면 `Review Needed` 보다 높이지 않는다.
- `evidence[].source_type`: `srs` / `spec_doc` / `tc` / `issue` / `comment` / `manual` / `rules` / `user_answer`.
- `evidence[].validity`: `Current` / `Deleted` / `Deprecated` / `Unknown`.
- 근거 위치는 입력에 있는 것만 쓴다. 코드가 대조해 없으면 뺀다.
  - `srs`: `location` 에 SRS 번호(`VP-1234`) 또는 Legacy 번호(`01-20-05`).
  - `spec_doc`: `ref` 에 입력 `candidates.spec_docs[].ref` 를 그대로, `location` 에 그 조각의 `location`.
  - `tc`: `location` 에 입력 TC 의 `location`(`파일 / 시트 / N행`) 또는 `tc_id`.
  - `issue`: `location` 에 이슈 번호. `comment`: `ref` 에 입력 댓글의 `id`.
  - `rules`: `location` 에 규칙 절(`§61`).
- `summary`: 결론부터 한두 문장. 카드에 그대로 보인다.
- `action`: QA 할 일. 줄바꿈으로 3줄까지(카드에 3줄까지 보인다). 이슈 종료·Close, TC 덮어쓰기, 결과·이력 삭제는 쓰지 않는다(쓰면 버려진다).
- 판정할 것이 없으면 `findings: []`. 빈 결과도 올바른 결과다.

## Skill 별 판정 값

| Skill | `verdict` |
|---|---|
| qa-new-issue-analysis | SPEC_VIOLATION / CONSISTENT_WITH_SPEC / SPEC_UNDEFINED / SPEC_AMBIGUOUS / INSUFFICIENT_EVIDENCE |
| qa-fixed-issue-analysis | CONSISTENT_WITH_SPEC / PARTIALLY_CONSISTENT / CONTRADICTS_SPEC / SPEC_UNDEFINED / INSUFFICIENT_EVIDENCE |
| qa-spec-decision-analysis | SUPPORTED_BY_SPEC / PARTIALLY_SUPPORTED / SPEC_AMBIGUOUS / SPEC_NOT_FOUND / CONTRADICTS_SPEC |
| qa-comment-analysis | 첫 의미 있는 댓글의 분류 (아래 comments 표) |
| qa-spec-change-summary | NO_QA_IMPACT / QA_CHECK_NEEDED / CONFLICTS_WITH_PAST_DECISION / SPEC_UNCLEAR |
| qa-spec-coverage-analysis (요청) | FULLY_COVERED / PARTIALLY_COVERED / NOT_COVERED / SPEC_REVIEW_REQUIRED |
| qa-verification-tc-draft (요청) | CONSISTENT_WITH_SPEC / PARTIALLY_CONSISTENT / CONTRADICTS_SPEC / SPEC_UNDEFINED / INSUFFICIENT_EVIDENCE |
| qa-manual-completeness | 보강 권장 / 타 문서 위임 적절 / 유지 / 사양 확인 필요 |

## `sections` (분석 Skill)

모양이 틀린 항목은 코드가 빼거나 고친다. 뺀 것은 사람이 보는 "근거 검증 기록"에 남는다.

| Skill | 키 | 모양 |
|---|---|---|
| new-issue | `duplicate` | `{verdict: STRONG_DUPLICATE/POSSIBLE_DUPLICATE/NO_DUPLICATE_FOUND, summary, candidates: [{issue_id, similarity: STRONG/POSSIBLE, reason, differences, past_resolution}]}` |
| new-issue, spec-decision, fixed | `historical` | `{summary, flags: [POSSIBLE_REGRESSION, HISTORICAL_SPEC_DUPLICATE, HISTORICAL_DUPLICATE_REREGISTERED, HISTORICAL_DECISION_CONFLICT], candidates: [{issue_id, flag, reason}]}` |
| new-issue, fixed, spec-decision | `specification` | `{summary, questions_for_lab: [..]}` |
| new-issue | `recommendation` | `{actions: [..], checks: [..]}` |
| fixed | `root_cause_review`, `resolution_review` | `{summary, assessment}` |
| fixed | `change_scope` | `{summary}` |
| fixed | `regression_risk` | `{axes: [{axis, applicable: true/false, reason}]}` — 축 이름은 입력 `regression_axes` 만 |
| tc-draft (요청) | `tc_coverage` | `[{tc_id, location, covers: true/false, reason}]` |
| spec-decision | `rd_claim`, `qa_analysis` | `{summary}`, `{summary, checks: [..]}` |
| comment | `comments` | `[{comment_id, summary, classification, impact, follow_up}]` |
| spec-change-summary, coverage | `change` | `{summary, changed_requirements: [..]}` — 요구사항 5개까지 |
| spec-change-summary | `related_issues` | `[{issue_id, relation: EXISTING_DEFECT/PAST_FIXED/PAST_SPEC/SAME_FUNCTION_REGRESSION, reason}]` — 5개까지 |
| coverage | `issue_coverage` | `{has_related, issues: [{issue_id, relation: EXISTING_DEFECT/PAST_FIXED/PAST_SPEC/SAME_FUNCTION_REGRESSION, reason}]}` |
| coverage | `checklist_coverage` | `{tcs: [{tc_id, location, decision: KEEP/UPDATE_EXISTING, reason, recommended_change}]}` |
| coverage | `gaps` | `[{perspective, description}]` |
| coverage | `alerts` | `[{type: ISSUE_WITHOUT_TC/TC_EXPECTED_OUTDATED/FIXED_ISSUE_WITHOUT_REGRESSION_TC/SPEC_ISSUE_BEHAVIOR_CHANGED, detail}]` |
| tc-draft, coverage (요청) | `tc_hold` | `{reason, questions: [..]}` — 초안을 만들지 않은 이유 |

댓글 분류: ROOT_CAUSE_INFORMATION / RESOLUTION_INFORMATION / REPRODUCTION_INFORMATION / SPEC_CLAIM /
REQUIREMENT_INFORMATION / QA_ACTION_REQUIRED / OTHER_SIGNIFICANT_INFORMATION / NOT_SIGNIFICANT.

## `draft_tcs`

사람이 버튼으로 요청한 qa-verification-tc-draft(연구소 결과 `FIXED` 인 이슈)와 qa-spec-coverage-analysis
(`PARTIALLY_COVERED`·`NOT_COVERED`)만 쓴다. 자동 실행 Skill 이 쓰면 코드가 초안을 뺀다. 사양 근거가 부족한
판정이면 코드가 초안을 뺀다.

```json
{"kind": "수정확인", "perspective": "DIRECT_FIX", "srs_no": "VP-2345", "change": "검색 날짜 조건",
 "change_detail": "시작일만 입력한 검색의 처리", "title": "검색 조건에 시작일만 입력",
 "precondition": "1. 로그인한다.\n2. 날짜가 다른 항목 3건을 등록한다.",
 "test_step": "1. 검색 조건에 시작일만 입력하고 검색한다.\n2. 검색 결과를 확인한다.",
 "expected_result": "2. 시작일 이후 항목 3건이 표시된다.", "test_data": "", "rationale": "원인(날짜 조건 누락)을 직접 확인"}
```

- `kind`: `수정확인` 또는 `Regression`.
- `perspective`: DIRECT_FIX / DIRECT_SPEC / REGRESSION / STATE_TRANSITION / PERSISTENCE / BOUNDARY / NEGATIVE /
  INTEGRATION / CONFIGURATION / ENVIRONMENT 가운데 하나.
- Expected 를 정할 사양 근거가 없으면 초안을 쓰지 않고 `tc_hold` 와 질문을 남긴다.

---
name: qa-fixed-issue-analysis
description: 연구소 결과가 수정 완료(FIXED)로 바뀌었거나 수정 완료 이슈의 원인·조치·중요 댓글이 바뀐 이슈의 원인과 조치를 검토하고, 최신 사양 일치·Regression 범위·QA 가 다시 확인할 것을 짧게 정리한다(QA 규칙 S04 Fix Verification). TC 는 보지 않는다. 입력 파일(runs/<실행ID>/in/FIX-*.json)을 받았을 때 쓴다.
---

# 수정 완료 이슈 분석 (S04)

**먼저 `qa-common-rules` 와 제품 규칙 Skill 을 읽는다.**

## 입력

`in/FIX-*.json` 의 `items[]`:

- `target`, `events[]`: 이 분석을 부른 변경(연구소 결과 → FIXED, 발생 원인·조치 내용 변경, 새 댓글).
- `issue`: 본문, `reproduction`(Precondition·Step·Expected·Actual), `occurrence_cause`(발생 원인),
  `action_details`(조치 내용), `comments[]`(연구소 댓글 최근 20개, 각 `id`).
- `candidates.srs[]`, `candidates.spec_docs[]`: 최신 사양 후보. `candidates.issues[]`: 비슷한 과거 이슈.
  TC 후보는 없다. 검증 TC 초안은 사람이 [검증 TC 초안 만들기]를 누를 때 `qa-verification-tc-draft` 가 만든다.
- `regression_axes[]`: 코드가 정한 Regression 축. 이 축만 판정한다.

## 절차 (순서대로)

1. **Root Cause 검토** (`sections.root_cause_review`): 발생 원인이 재현 절차·Actual 을 설명하는가.
2. **Resolution 검토** (`sections.resolution_review`): 조치 내용이 원인을 없애는가. 연구소 댓글은 단서일 뿐이다.
3. **최신 사양과 일치 여부** → `verdict`: `CONSISTENT_WITH_SPEC` / `PARTIALLY_CONSISTENT` /
   `CONTRADICTS_SPEC` / `SPEC_UNDEFINED` / `INSUFFICIENT_EVIDENCE`. 근거를 `evidence` 에.
4. **변경 범위** (`sections.change_scope`).
5. **부작용·Regression 범위** (`sections.regression_risk.axes`): `regression_axes` 의 축마다
   `applicable`(true/false)과 이유. 해당하지 않는 축도 false 로 남긴다.
6. **요약과 할 일**: `summary` 한두 문장, `action` 은 QA 가 다시 확인할 것을 3줄까지.
   TC 초안(`draft_tcs`)은 쓰지 않는다. 쓰면 코드가 빼고 메모를 남긴다.

## Finding

- 이슈마다 하나. `subject` = `target`, `verdict` = 사양 판정.
- 과거 비슷한 이슈의 재발 후보가 있으면 `sections.historical` 에 적는다.
- `gate_status`: G1, G2, G4, G7 (제품 규칙 Skill 의 Gate 번호).

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.

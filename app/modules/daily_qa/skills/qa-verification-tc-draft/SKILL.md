---
name: qa-verification-tc-draft
description: 사람이 수정 완료 이슈 분석 화면에서 [검증 TC 초안 만들기]를 눌렀을 때, 그 이슈의 기존 TC 가 원인을 다시 잡는지 보고 잡지 못하는 것만 검증 TC 초안으로 만든다(QA 규칙 S04). 입력 파일(runs/<실행ID>/in/FIX-*.json, 후보에 tcs 가 있음)을 받았을 때 쓴다.
---

# 검증 TC 초안 (요청할 때만)

**먼저 `qa-common-rules` 와 제품 규칙 Skill 을 읽는다.** 이 Skill 은 사람이 버튼으로 요청했을 때만 돈다.
TC 는 사람이 넣는 자료라 틀릴 수 있다. TC 행을 근거로 쓸 때도 사양과 맞는지 먼저 본다.

## 입력

`in/FIX-*.json` 의 `items[]`:

- `target`: 이슈 번호. `issue`: 본문, `reproduction`, `occurrence_cause`(발생 원인), `action_details`(조치 내용), `comments[]`.
- `candidates.srs[]`, `candidates.spec_docs[]`: 최신 사양 후보. `candidates.tcs[]`: 기존 TC 후보. `candidates.issues[]`: 비슷한 과거 이슈.
- `regression_axes[]`: 코드가 정한 Regression 축.

## 절차

1. **최신 사양과 일치 여부** → `verdict`: `CONSISTENT_WITH_SPEC` / `PARTIALLY_CONSISTENT` / `CONTRADICTS_SPEC` /
   `SPEC_UNDEFINED` / `INSUFFICIENT_EVIDENCE`. 근거를 `evidence` 에.
2. **기존 TC Coverage** (`sections.tc_coverage`): 후보 TC 마다 같은 원인이 다시 생기면 실패하는지(`covers`)와 이유.
   TC 에 이슈 번호가 적혀 있다는 이유만으로 덮는다고 보지 않는다.
3. **Regression 범위** (`sections.regression_risk.axes`): `regression_axes` 의 축마다 `applicable` 과 이유.
4. **검증 TC 초안** (`draft_tcs`): 기존 TC 로 잡을 수 없는 것만 쓴다.
   - `kind`: `수정확인` 또는 `Regression`. `perspective`: `DIRECT_FIX` / `REGRESSION` / `STATE_TRANSITION` /
     `BOUNDARY` / `NEGATIVE` / `INTEGRATION` 가운데 관계있는 것만.
   - Step 하나에 조작 하나, Expected 번호는 Step 번호와 맞춘다. `rationale` 에 필요한 이유.
   - 사양 판정이 `SPEC_UNDEFINED`·`INSUFFICIENT_EVIDENCE` 면 초안을 쓰지 않는다. `sections.tc_hold` 에 이유와 확인할 점.
5. `summary` 는 한두 문장, `action` 은 QA 할 일 3줄까지.

## Finding

- 이슈마다 하나. `subject` = `target`, `verdict` = 사양 판정.
- 연구소 결과가 FIXED 가 아닌 이슈에 초안을 쓰면 코드가 Finding 을 버린다.
- `gate_status`: G1, G2, G3, G4, G5, G7.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.

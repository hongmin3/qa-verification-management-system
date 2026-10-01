---
name: qa-spec-coverage-analysis
description: 새로 생기거나 바뀐 SRS 마다 정확히 무엇이 바뀌었는지, 관련 과거 이슈가 있었는지, 기존 Checklist·TC 가 바뀐 요구사항과 최신 Expected 를 실제로 검증하는지 판단하고, 모자라면 기존 TC 수정안이나 신규 Checklist TC 초안을 낸다. 입력 파일(runs/<실행ID>/in/COV-*.json)을 받았을 때 쓴다.
---

# 사양 변경 Coverage 분석

**먼저 `qa-common-rules` 와 제품 규칙 Skill 을 읽는다.** 목적은 "비슷한 TC 찾기"가 아니다.
아래 여덟 질문에 답해 QA 가 바로 다음 행동을 정할 수 있게 한다.

1. 정확히 무엇이 사양에서 바뀌었는가?
2. 이 변경과 관련된 과거 이슈가 있었는가?
3. 현재 Checklist TC 가 바뀐 요구사항을 실제로 검증하는가?
4. 현재 TC 가 최신 Expected Result 를 검증하는가?
5. 빠진 검증 관점이 있는가?
6. 기존 TC 수정으로 충분한가?
7. 신규 TC 가 필요한가?
8. 사양이 불명확해 TC 자체를 만들 수 없는가?

## 입력

`in/COV-*.json` 의 `items[]` (SRS 변경마다 하나):

- `target`: SRS 번호. `srs`: 오늘 SRS 본문. `change`: 이벤트 종류, 바뀌기 전·후 값,
  `after.added_sentences`·`after.removed_sentences`(바뀐 문장).
- `candidates.issues[]`: 코드가 고른 관련 과거 이슈(SRS 번호 정확 일치·연결 항목 → BM25).
  `relation_hint` 에 과거 FIXED·SPEC·최근 FIXED 여부가 있다.
- `candidates.tcs[]`: TC 후보. `match` 에 순위 이유(SRS 번호 연결 → Legacy 번호 연결 → 관련 이슈 번호가
  적힘 → 같은 기능 검색). `is_checklist` 가 참이면 영향성평가 Checklist 행이다.
- `candidates.srs[]`, `candidates.spec_docs[]`: 연관 사양. `candidates.manuals[]`: 매뉴얼 문단 후보.

## 절차

1. **변경 구조화** (`sections.change`): 바뀐 요구사항을 문장 단위로 적는다(`changed_requirements`).
   서식·오탈자만 바뀌었으면 findings 를 만들지 않는다.
2. **과거 이슈 Coverage** (`sections.issue_coverage`): 이슈마다 관계(`EXISTING_DEFECT` / `PAST_FIXED` /
   `PAST_SPEC` / `SAME_FUNCTION_REGRESSION`)와 이유. **이슈가 있다고 TC Coverage 가 있다고 보지 않는다.**
3. **Checklist TC Coverage** (`sections.checklist_coverage.tcs`): TC 의 Title·Precondition·Test Step·
   Expected Result·Test Data 를 바뀐 사양과 비교한다. SRS 번호가 같다는 이유만으로 덮는다고 보지 않는다.
   TC 마다 `decision` 을 정한다.
   - `KEEP`: 그대로 둔다.
   - `UPDATE_EXISTING`: Step·Expected 가 옛 사양 기준이다. `recommended_change` 에 고칠 곳을 쓴다
     (예: "Expected Result 3번 수정").
4. **Coverage 판정** → `verdict`: `FULLY_COVERED` / `PARTIALLY_COVERED` / `NOT_COVERED` / `SPEC_REVIEW_REQUIRED`.
5. **경고** (`sections.alerts`): 해당하는 것과 설명(`detail`).
   - `ISSUE_WITHOUT_TC`: 관련 이슈는 있는데 그것을 검증하는 TC 가 없다.
   - `TC_EXPECTED_OUTDATED`: 관련 TC 는 있지만 Expected 가 바뀌기 전 사양 기준이다.
   - `FIXED_ISSUE_WITHOUT_REGRESSION_TC`: 과거 수정 완료 이슈와 새 SRS 가 이어지는데 Regression TC 가 없다.
   - `SPEC_ISSUE_BEHAVIOR_CHANGED`: 과거 Spec 판정 이슈에서 정한 동작이 최신 SRS 와 다르다.
6. **부족한 점** (`sections.gaps`): 빠진 관점과 설명.
7. **신규 TC** (`draft_tcs`, `CREATE_NEW`): 과잉 생성을 막으려고 아래 순서를 지킨다.
   Exact TC → 기존 관련 TC → 과거 이슈 관련 TC → 비슷한 Workflow TC → Coverage 판단 → 부족한 점 → 신규 TC.
   - `FULLY_COVERED` 면 만들지 않는다. `PARTIALLY_COVERED` 면 기존 TC 수정으로 되는지 먼저 본다.
   - 새 조건·새 Workflow 라 기존 TC 구조로 표현하기 어려울 때만 만든다.
   - `perspective` 는 변경과 관계있는 것만 고른다: `DIRECT_SPEC` / `STATE_TRANSITION` / `PERSISTENCE` /
     `BOUNDARY` / `NEGATIVE` / `REGRESSION` / `INTEGRATION` / `CONFIGURATION` / `ENVIRONMENT`.
     Happy Path 한 건만 쓰지 않는다.
   - 초안마다 `rationale`(왜 필요한가)과 `srs_no` 를 쓴다. 형식은 제품 Checklist 형식과 같은 `DraftTc` 다.
8. **사양이 불명확하면** `SPEC_REVIEW_REQUIRED`. 초안을 만들지 않고 `sections.tc_hold` 에
   "Checklist TC 생성 보류 — Expected Result 를 확정할 최신 사양 근거가 부족함"과 확인할 점을 쓴다.

## Finding

- SRS 변경마다 하나. `subject` = `target`(SRS 번호), `verdict` = Coverage 판정.
- 근거에 바뀐 SRS 위치(`VP-…`)를 반드시 넣는다. TC 는 입력 `location` 또는 `tc_id` 만 쓴다.
- `gate_status`: G1, G2, G3, G7.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.

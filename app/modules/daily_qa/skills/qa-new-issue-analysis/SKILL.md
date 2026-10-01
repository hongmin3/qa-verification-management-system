---
name: qa-new-issue-analysis
description: 새로 등록됐거나 처리 전에 본문이 바뀐 이슈마다 중복 여부, 최신 사양 대비 판단, 과거 이슈와의 관계, QA 권고를 근거와 함께 정리한다. QA Agent 점검의 입력 파일(runs/<실행ID>/in/NEW-*.json)을 받았을 때 쓴다.
---

# 신규 이슈 분석 (Duplicate → Specification → Historical → Recommendation)

**먼저 `qa-common-rules` 와 프롬프트가 알려 준 제품 규칙 Skill 을 읽는다.** 읽지 못했으면
findings 를 비운 결과와 질문 하나만 쓰고 끝낸다.

## 입력

`in/NEW-*.json` 의 `items[]` (대상 이슈마다 하나):

- `target`: 이슈 번호. Finding 의 `subject` 에 그대로 쓴다.
- `events[]`: 이 분석을 부른 변경(`ISSUE_CREATED`, 본문·재현 절차 변경 등)과 바뀌기 전·후 값.
- `issue`: 제목, 상태, 심각도, 연구소 결과(`rd_result` 공통 값, `rd_result_raw` 원본), 설명,
  `reproduction`(`precondition`·`steps`·`expected`·`actual`·`log_data`), 발생·목표 버전, 연결 항목.
- `candidates.issues[]`: 코드가 고른 비슷한 과거 이슈. 같은 버전 후보가 앞에 있다. 각 후보의
  `rd_result`(과거 처리 결과), `match`(왜 걸렸는지).
- `candidates.srs[]`: 사양 후보(오늘 SRS). `candidates.spec_docs[]`: 사양서 조각(`ref`, `location`).
- `candidates.tcs[]`: 관련 TC 후보. `search`: 코드가 쓴 검색어.

## 절차 (이슈마다)

1. **중복 분석.** 후보 이슈와 제목·재현 절차·Expected·Actual·오류 문구·연결 SRS·버전을 비교한다.
   - 조건·증상·결과가 같으면 `STRONG_DUPLICATE`, 일부 다르면 `POSSIBLE_DUPLICATE`, 없으면 `NO_DUPLICATE_FOUND`.
   - 후보마다 `issue_id`, 중복으로 본 근거(`reason`), 차이점(`differences`), 과거 처리 결과(`past_resolution`
     = 후보의 `rd_result`)를 적는다. 입력 후보에 없는 번호는 쓰지 않는다.
2. **사양 분석.** 연결 SRS 만 보지 말고 `candidates.srs`·`candidates.spec_docs` 전체와 비교한다.
   판정은 finding 의 `verdict` 다: `SPEC_VIOLATION` / `CONSISTENT_WITH_SPEC` / `SPEC_UNDEFINED` /
   `SPEC_AMBIGUOUS` / `INSUFFICIENT_EVIDENCE`. 근거 위치를 `evidence` 에 넣는다.
   사양이 없으면 결함·정상으로 정하지 않고 `SPEC_UNDEFINED` 로 둔다.
   `sections.specification.questions_for_lab` 에 연구소·기획에 확인할 것을 적는다.
3. **과거 이슈 분석.** 후보의 과거 처리 결과를 보고 `sections.historical.flags` 에 후보로만 표시한다.
   - 과거 `FIXED` 이슈와 증상이 같으면 `POSSIBLE_REGRESSION`
   - 과거 `SPEC`·`NOT_BUG` 로 처리된 이슈가 다시 올라온 것 같으면 `HISTORICAL_SPEC_DUPLICATE`
   - 과거 `DUPLICATE` 로 처리된 이슈가 다시 올라온 것 같으면 `HISTORICAL_DUPLICATE_REREGISTERED`
   - 비슷한 과거 이슈들의 처리 결과가 서로 다르면 `HISTORICAL_DECISION_CONFLICT`

   표시마다 뒷받침하는 후보를 `sections.historical.candidates` 에 넣는다. 후보 없는 표시는 코드가 뺀다.
4. **QA 권고.** `sections.recommendation.actions`(사람이 할 다음 조치)와 `checks`(확인할 점).

## Finding

- 이슈마다 하나. `subject` = `target`, `subject_title` = 이슈 제목, `verdict` = 사양 판정.
- `sections`: `duplicate`, `specification`, `historical`, `recommendation`.
- **TC 초안(`draft_tcs`)을 만들지 않는다.** 만들면 Finding 이 버려진다.
- `gate_status`: 제품 규칙 Skill 이 정한 Gate 가운데 G1, G2, G7.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.

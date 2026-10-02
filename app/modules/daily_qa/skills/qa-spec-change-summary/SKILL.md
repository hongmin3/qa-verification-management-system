---
name: qa-spec-change-summary
description: 새로 생기거나 바뀐 SRS 마다 무엇이 바뀌었는지, QA 가 확인할 것이 있는지, 관련 과거 이슈가 있는지를 짧게 정리한다. TC·Checklist·매뉴얼은 보지 않는다. 입력 파일(runs/<실행ID>/in/COV-*.json)을 받았을 때 쓴다.
---

# 사양 변경 분석 (자동 실행)

**먼저 `qa-common-rules` 와 제품 규칙 Skill 을 읽는다.** QA 담당자가 카드 한 장만 보고 다음 행동을
정할 수 있게 세 가지만 답한다.

1. 사양에서 정확히 무엇이 바뀌었는가?
2. 이 변경 때문에 QA 가 확인할 것이 있는가?
3. 관련된 과거 이슈가 있는가?

TC·Checklist·매뉴얼과 비교하지 않는다. 그것은 사람이 화면의 [TC 점검] 버튼을 누를 때
`qa-spec-coverage-analysis` 가 한다.

## 입력

`in/COV-*.json` 의 `items[]` (SRS 변경마다 하나):

- `target`: SRS 번호. `srs`: 오늘 SRS 본문. `change`: 이벤트 종류, 바뀌기 전·후 값,
  `after.added_sentences`·`after.removed_sentences`(바뀐 문장).
- `candidates.issues[]`: 코드가 고른 관련 과거 이슈. `relation_hint` 에 과거 FIXED·SPEC 여부가 있다.
- `candidates.srs[]`, `candidates.spec_docs[]`: 연관 사양.

## 절차

1. **바뀐 것** (`sections.change`): `summary` 한두 문장, `changed_requirements` 는 바뀐 요구사항 문장 5개까지.
2. **관련 과거 이슈** (`sections.related_issues`): 입력 `candidates.issues` 가운데 정말 관계있는 것만 5개까지.
   항목은 `{issue_id, relation, reason}` 이고 `relation` 은 `EXISTING_DEFECT` / `PAST_FIXED` / `PAST_SPEC` /
   `SAME_FUNCTION_REGRESSION`, `reason` 은 한 줄이다.
3. **판정** → `verdict`.

   | 값 | 뜻 |
   |---|---|
   | `NO_QA_IMPACT` | 서식·오탈자·표현만 바뀌었다. QA 가 할 일이 없다 |
   | `QA_CHECK_NEEDED` | 동작·조건·값이 바뀌었다. QA 가 확인할 것이 있다 |
   | `CONFLICTS_WITH_PAST_DECISION` | 과거 Spec 판정 이슈에서 정한 동작이나 과거 수정 결과가 새 사양과 다르다 |
   | `SPEC_UNCLEAR` | 바뀐 문장이 여러 뜻으로 읽혀 Expected 를 정할 수 없다 |

4. **요약과 할 일**: `summary` 는 한두 문장으로 결론부터 쓴다. `action` 은 QA 할 일을 3줄까지 줄바꿈으로
   나눠 쓴다(예: "Reload 뒤 목록 유지 확인\n연구소에 저장 시점 확인"). `NO_QA_IMPACT` 면 `action` 을 비운다.

## 지킬 것

- 입력만으로 판단한다. 더 찾아야 하면 `context/srs_current.jsonl` 만 본다. 오래 검색하지 않는다.
- TC 번호·TC 위치를 쓰지 않는다. TC 초안(`draft_tcs`)을 만들지 않는다.
- 과거 이슈 관계는 후보다. 확정 사실처럼 쓰지 않는다.

## Finding

- SRS 변경마다 하나. `subject` = `target`(SRS 번호), `subject_title` = SRS 제목, `verdict` = 위 판정.
- 근거에 바뀐 SRS 위치(`VP-…`)를 반드시 넣는다.
- `gate_status`: G1, G2, G7.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.

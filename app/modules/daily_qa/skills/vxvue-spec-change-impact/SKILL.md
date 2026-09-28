---
name: vxvue-spec-change-impact
description: 바뀐 VXvue SRS(신규·변경)마다 영향을 받을 수 있는 기존 TC 를 판정한다(유지/경미 수정/수정 필수/신규 TC 필요/사양 확인 필요). 일일 점검 B 단계 입력 파일(runs/<실행ID>/in/B-*.json)을 받았을 때 쓴다.
---

# B — 사양 변경 → TC 영향

QA 규칙의 S02(Specification Trace) → S03(TC Coverage Review) 체인이다 (가이드 §45 "기존 TC 보강").

**먼저 `vxvue-qa-rules` Skill 을 읽는다.** 읽지 못했으면 판정하지 말고 findings 를 비운 결과와
질문 하나("vxvue-qa-rules 를 읽지 못함")만 쓰고 끝낸다. 이 파일은 공통 규칙을 다시 적지 않는다.

## 입력

`in/B-*.json`:

- `changes[]`: `change`(`added`/`modified`), `srs`(`id`, `old_id`, `title`, 신규면 `text`),
  변경이면 `fields`·`before`·`after`(바뀐 필드만), `candidates[]`(코드가 SRS 번호로 고른 TC 행 —
  `location`, `tc_id`, `title`, `precondition`, `step`, `expected`).
- `answered_questions[]`: 사람이 전에 답한 내용.

보조 자료: `context/srs_current.jsonl`(오늘 SRS 전체, 한 줄에 하나), `context/tc_index.jsonl`(TC 전체).

## 절차

1. **변경 의미 파악.** `before` → `after` 를 비교해 무엇이 바뀌었는지 한 문장으로 정리한다.
   서식·오탈자만 바뀌었으면 후보 TC 전부 `유지` 로 하나의 Finding 에 묶지 말고, 판정할 TC 가
   없으면 findings 를 만들지 않는다.
2. **연관 사양 추적.** 바뀐 동작의 입력·선행 상태를 다른 SRS 가 만들면 그 SRS 도
   `context/srs_current.jsonl` 에서 찾는다 (가이드 §59 상태 생성 사양 역추적). 제목 키워드·Legacy
   번호로 검색한다.
3. **후보 밖 TC 확인.** 코드 후보는 SRS 번호가 적힌 TC 뿐이다. 기능명·메뉴명으로
   `context/tc_index.jsonl` 을 검색해 같은 동작을 검사하는 TC 가 더 있으면 함께 판정한다.
   찾아본 검색어는 `detail` 에 적는다.
4. **TC 별 판정.** 가이드 §3.3(S03), §16(기존 TC 첨삭), §61(신규 TC 필요·중복 판단),
   §69(완료 TC 의미 보존 편집), §70(Coverage Review 확장)을 **판정 전에 읽는다.**
   - Step/Expected 가 바뀐 사양과 어긋나면 `수정 필수`.
   - 의미는 같고 표현만 낡았으면 `경미 수정`.
   - 기존 TC 로 검출할 수 없는 새 동작이면 `신규 TC 필요` (같은 Trigger·관찰점·Fail 조건의 TC 가
     이미 있으면 신규로 올리지 않는다, §61.3).
   - 근거가 모자라거나 사양끼리 상충하면 `사양 확인 필요` + `open_questions`.
   - 영향이 없으면 `유지`. `유지` 도 근거(SRS 위치 + TC 위치)를 적는다.
5. **완료된 TC 보호.** 이미 결과가 있는 TC 의 의미를 바꾸는 수정은 `수정 필수` 로 올리되
   `action` 에 "기존 TC 는 유지하고 신규 TC 로 분리 검토" 를 적는다 (§69.2).

## Finding 작성

- `subject`: SRS ID. TC 별로 Finding 하나. `tc_ref` 에 대상 TC 위치를 넣는다.
- 신규 SRS(`added`)는 대응 TC 가 없으면 `신규 TC 필요` Finding 하나 (`tc_ref` 없음).
- `gate_status`: G1, G2, G3, G7.

결과 JSON 을 쓰기 직전에 `vxvue-qa-rules` 의 `references/output-contract.md` 를 읽는다.

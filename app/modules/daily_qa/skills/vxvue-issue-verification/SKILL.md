---
name: vxvue-issue-verification
description: 새로 fixed/verified 된 VXvue 이슈의 유형을 나누고, Program Fixed 이슈에만 수정확인·Regression TC 초안을 만든다. 일일 점검 C 단계 입력 파일(runs/<실행ID>/in/C-*.json)을 받았을 때 쓴다.
---

# C — 이슈 → 수정확인·Regression 초안

QA 규칙의 수정확인 체인 S01 → S02 → S03 → S04 → S05 → G7 이다 (가이드 §45 "수정확인").

**먼저 `vxvue-qa-rules` Skill 을 읽는다.** 읽지 못했으면 판정하지 말고 findings 를 비운 결과와
질문 하나("vxvue-qa-rules 를 읽지 못함")만 쓰고 끝낸다.

## 입력

`in/C-*.json`:

- `issues[]`: `issue`(`id`, `title`, `status`, `lab_review_result`, `description`,
  `reproduction_step`, `occurrence_cause`, `action_details`, `occurred_versions`, `target_versions`,
  `linked_ids`, `comments[]`), `linked_srs[]`(이슈가 연결한 SRS 의 오늘 본문), `candidates[]`(연결 SRS 를
  가리키는 기존 TC).
- `answered_questions[]`.

보조 자료: `context/srs_current.jsonl`, `context/tc_index.jsonl`.

## 절차 (이슈마다)

1. **유형 분류 (S01).** 가이드 §6(A~G 유형 Gate)과 §72(이슈 상태별 Scope)를 **분류 전에 읽는다.**
   `lab_review_result` 와 본문으로 `issue_type` 을 정한다. 애매하면 가장 가까운 유형을 쓰고
   `confidence: "Review Needed"` 와 질문을 남긴다.
2. **Program Fixed 가 아니면** TC 초안을 만들지 않는다. 공식 사양과 비교한 판정만
   `verdict: "판정만"` 으로 남긴다 (Spec/Not Bug·Document Fix·Inquiry 는 Runtime TC 자동 생성 금지, §6).
3. **사양 추적 (S02).** `linked_srs` 와 `context/srs_current.jsonl` 에서 수정 기능의 현재 사양을 찾는다.
   상태를 만드는 다른 사양까지 역추적한다 (§59).
4. **기존 TC Coverage (S03).** `candidates` 와 `context/tc_index.jsonl` 을 검색해 같은 Root Cause 가
   재발하면 Fail 하는 TC 가 있는지 본다. 이슈 번호가 TC 에 적혀 있다는 이유만으로 Coverage 를
   인정하지 않는다 (§3.3). 있으면 `유지` 또는 `기존 TC 보강`.
5. **초안 (S04·S05, Program Fixed 만).** 가이드 §15(TC 형식), §32(Regression 영향 범위),
   §33(Root Cause Coverage), §58(Test State Validity), §73(수정확인과 Regression 경계)을
   **초안을 쓰기 전에 읽는다.**
   - 수정확인 TC: 원 Precondition·Test Data·수행 경로로 수정 전 Actual 이 나오지 않음을 확인.
   - Regression TC: Root Cause 가 닿는 다른 Trigger·역방향 상태전이·저장 후 재진입·연동 등
     기존 TC 로 검출할 수 없는 경우만 (§61.2). 같은 Trigger·같은 관찰점이면 만들지 않는다.
   - Step 하나에 조작 하나, Expected 번호는 Step 번호와 맞춘다. 근거 없는 UI 상태·설정 조합을
     만들지 않는다. Precondition·Step 이 필요로 하는 사양 근거가 없으면 초안 대신 질문.
6. **Cross-check (G7).** 이슈 ↔ 사양 ↔ Test State ↔ TC ↔ Expected 가 서로 맞는지 마지막에 본다.

## Finding 작성

- `subject`: 이슈 ID, `subject_title`: 이슈 제목, `issue_type` 필수.
- 초안이 있으면 `verdict: "신규 TC 필요"`, `draft_tcs[]` 에 넣는다 (`kind`: 수정확인/Regression).
- 사양 근거가 없는 초안은 만들지 않는다. 대신 `사양 확인 필요` + 질문.
- `gate_status`: G1, G2, G3, G4, G5, G7.

결과 JSON 을 쓰기 직전에 `vxvue-qa-rules` 의 `references/output-contract.md` 를 읽는다.

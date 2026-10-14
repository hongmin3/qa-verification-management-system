---
name: qa-comment-analysis
description: 이슈에 새로 달린 댓글(어제 없던 것)만 읽어 원인·조치·재현·사양 주장 같은 QA 판단에 영향이 있는 정보인지 나누고, 기존 QA 분석에 주는 영향과 추가 확인사항을 적는다. 입력 파일(runs/<실행ID>/in/CMT-*.json)을 받았을 때 쓴다.
---

# 새 댓글 분석

**먼저 `qa-common-rules` 와 제품 규칙 Skill 을 읽는다.** 댓글 전체를 다시 분석하지 않는다.
입력의 `new_comments` 만 분류한다.

## 입력

`in/CMT-*.json` 의 `items[]`: `target`, `issue`(요약), `new_comments[]`(`id`, `created`, `text` —
코드가 진행 상태 같은 의미 없는 댓글은 이미 뺐다), `earlier_comments[]`(이전 댓글 최근 5개, 맥락용).
`previous_analysis`는 같은 제품·이슈의 직전 요약(없으면 null)이다.
`candidates.srs[]`는 새 댓글의 번호와 이슈 연결 번호로 찾은 현재 사양이다(2건, 본문 각 1,500자까지).
`truncated`가 true면 본문 일부만 받았다. `missing_srs_ids`는 사양으로 확인됐지만 본문이 빈 번호,
`omitted_srs_ids`는 개수 상한 때문에 넣지 못한 사양 번호다. `unresolved_reference_ids`는
수집한 사양·이슈에서 유형을 확인하지 못한 번호다. 이를 누락 사양이라고 단정하지 않는다.
알려진 관련 이슈 번호는 누락 사양 목록에 넣지 않는다. 전체 사양이 없다는 뜻으로 해석하지 않는다.
작성자 ID는 화면용으로만 저장되고 AI 입력에는 없다. 댓글 작성자를 연구소·기획 등으로 추측하지 않는다.

## 절차 (새 댓글마다)

1. 요약(`summary`)을 한두 문장으로 쓴다.
2. 분류(`classification`)를 하나 고른다: `ROOT_CAUSE_INFORMATION` / `RESOLUTION_INFORMATION` /
   `REPRODUCTION_INFORMATION` / `SPEC_CLAIM` / `REQUIREMENT_INFORMATION` / `QA_ACTION_REQUIRED` /
   `OTHER_SIGNIFICANT_INFORMATION` / `NOT_SIGNIFICANT`.
3. 기존 QA 분석에 주는 영향(`impact`)과 추가 확인사항(`follow_up`)을 쓴다.
4. 연결 사양 본문과 댓글의 주장을 대조한다. 번호를 언급하거나 사양을 고쳤다는 댓글만으로
   사양 반영을 확정하지 않는다. 본문이 잘렸거나 없으면 확인하지 못한 범위를 적는다.

## Finding

- 이슈마다 하나. `subject` = `target`, `verdict` = 첫 의미 있는 댓글의 분류.
- `sections.comments[]` 에 댓글마다 `{comment_id, summary, classification, impact, follow_up}`.
  `comment_id` 는 입력 댓글의 `id` 그대로 쓴다.
- 근거는 `{"source_type": "comment", "ref": "<댓글 id>", "location": "<이슈 번호> 댓글"}`.
- 모든 댓글이 `NOT_SIGNIFICANT` 면 Finding 이 저장되지 않는다(정상).
- TC 초안을 만들지 않는다.

## 읽히는 설명

- `subject_title`에는 이슈 제목, `sections.issue_summary`에는 문제가 된 동작을 한 문장으로 쓴다.
- Finding `summary`는 “댓글 작성자가 → 어떤 동작에 대해 무엇을 말함 → QA에게 뜻”을
  한두 문장으로 쓴다. 코드 이름과 분류만 나열하지 않는다. 사양과 대조하지 못했다면 그 한계를 포함한다.
- `detail`에는 비교 과정과 판단 이유를 풀어 쓴다. 예: 설정 조건별 문구를 제안한 댓글을
  기대 결과 후보로 분류했으나, 연결 사양 본문에서 아직 확인하지 못했다.
- `sections.checked`는 `{confirmed: ["직접 확인한 자료와 범위"], unconfirmed: ["확인하지 못한 자료와 이유"]}`다.
  입력에 있다는 이유만으로 확인했다고 쓰지 않는다. 댓글 주장·직전 분석 요약·현재 사양을 구분한다.
  사양 번호·본문·비교한 조건을 적고, 빈 문자열이나 확인하지 않은 사실을 채워 넣지 않는다.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.

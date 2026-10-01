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

## 절차 (새 댓글마다)

1. 요약(`summary`)을 한두 문장으로 쓴다.
2. 분류(`classification`)를 하나 고른다: `ROOT_CAUSE_INFORMATION` / `RESOLUTION_INFORMATION` /
   `REPRODUCTION_INFORMATION` / `SPEC_CLAIM` / `REQUIREMENT_INFORMATION` / `QA_ACTION_REQUIRED` /
   `OTHER_SIGNIFICANT_INFORMATION` / `NOT_SIGNIFICANT`.
3. 기존 QA 분석에 주는 영향(`impact`)과 추가 확인사항(`follow_up`)을 쓴다.

## Finding

- 이슈마다 하나. `subject` = `target`, `verdict` = 첫 의미 있는 댓글의 분류.
- `sections.comments[]` 에 댓글마다 `{comment_id, summary, classification, impact, follow_up}`.
  `comment_id` 는 입력 댓글의 `id` 그대로 쓴다.
- 근거는 `{"source_type": "comment", "ref": "<댓글 id>", "location": "<이슈 번호> 댓글"}`.
- 모든 댓글이 `NOT_SIGNIFICANT` 면 Finding 이 저장되지 않는다(정상).
- TC 초안을 만들지 않는다.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.

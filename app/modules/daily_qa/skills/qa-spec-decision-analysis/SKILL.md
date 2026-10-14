---
name: qa-spec-decision-analysis
description: 연구소가 "사양대로(SPEC)" 또는 "결함 아님(NOT_BUG)"으로 판정한 이슈를 최신 사양과 과거 이슈에 비춰 다시 본다. 연구소 판정을 그대로 믿지 않는다. 입력 파일(runs/<실행ID>/in/SPC-*.json)을 받았을 때 쓴다.
---

# Spec 판정 이슈 분석

**먼저 `qa-common-rules` 와 제품 규칙 Skill 을 읽는다.**

## 입력

`verification_mode=true`이면 선택한 버전의 `candidates.tcs`도 비교한다. 연구소가 사양이라고 설명해도
원문에 그 동작과 적용 버전·예외가 명시돼 있는지 확인한다. 문서 수정 주장도 수정된 사양 본문과 대조한다.
말과 원문이 다르면 이슈 설명 수정·TC 보완·실제 장비 동작 확인 중 필요한 조치를 근거와 함께 제안한다.
확인할 수 없는 사실은 단정하지 않는다. `sections.tc_coverage`에 TC 위치·`covers`·이유를 남긴다.

`in/SPC-*.json` 의 `items[]`: `target`, `events[]`, `issue`(본문·재현 절차·발생 원인·조치 내용·
연구소 댓글 `comments[]`), `candidates.srs[]`, `candidates.spec_docs[]`, `candidates.issues[]`(비슷한 과거 이슈와 처리 결과).

## 절차

1. **연구소 주장 정리** (`sections.rd_claim.summary`): 무엇을 근거로 사양이라고 했는가.
2. **사양 근거 판정** → `verdict`: `SUPPORTED_BY_SPEC` / `PARTIALLY_SUPPORTED` / `SPEC_AMBIGUOUS` /
   `SPEC_NOT_FOUND` / `CONTRADICTS_SPEC`. 근거 위치를 `evidence` 에 넣는다. 연구소 댓글만으로
   `SUPPORTED_BY_SPEC` 을 내지 않는다.
3. **과거 판정 비교** (`sections.historical`): 비슷한 증상이 과거에 `FIXED` 였는데 이번에 `SPEC`
   으로 처리됐거나 그 반대면 `HISTORICAL_DECISION_CONFLICT` 를 후보로 표시한다. 뒷받침하는 과거
   이슈를 `candidates` 에 넣는다.
4. **QA 분석** (`sections.qa_analysis`): QA 가 연구소에 다시 확인할 점(`checks`).

## Finding

- 이슈마다 하나. `subject` = `target`. TC 초안을 만들지 않는다.
- `gate_status`: G1, G2, G7.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 확인한다. 프롬프트에 이미 들어 있으면 다시 읽지 않는다.

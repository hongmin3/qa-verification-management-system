---
name: qa-common-rules
description: QA Agent 무인 점검 Skill(qa-new-issue-analysis, qa-fixed-issue-analysis, qa-spec-decision-analysis, qa-comment-analysis, qa-spec-change-summary, qa-spec-coverage-analysis, qa-verification-tc-draft, qa-issue-spec-audit, qa-manual-completeness, qa-trace-gap)이 제품과 상관없이 공통으로 따르는 규칙과 결과 JSON 형식. 이 Skill 들을 수행할 때 항상 먼저 읽는다.
---

# QA 공통 규칙 (무인 실행, 모든 제품)

이 Skill 은 서버가 사람 없이 돌리는 점검에서 쓰인다. 대화로 질문할 수 없고, 결과는 QA 담당자가
대시보드(`/qa-agent`)에서 읽는다. **모든 산출물은 초안이다.** AI 는 변경 탐지 → 자료 조사 → 비교 →
근거 정리까지만 한다. QA 결정을 대신하지 않는다.

결과는 짧게 쓴다. QA 담당자는 카드에서 결론·한두 문장 요약·할 일 3줄까지만 본다.
`summary` 는 결론부터 한두 문장, `action` 은 QA 할 일을 줄바꿈으로 3줄까지 쓴다.

## 규칙 층

1. **공통 규칙** — 이 파일. 모든 제품에 같다.
2. **제품 규칙** — 프롬프트가 알려 준 제품 규칙 Skill(예: `vxvue-qa-rules`)과 작업 폴더 `rules/`
   의 제품 QA 규칙 원문(`rules/qa-guide.md`, `rules/instruction-prompt.txt`). Gate 번호·절 번호·
   제품 고유 검토 관점(장비·DICOM 등)은 제품 규칙이 정한다.
3. 충돌 우선순위: 입력의 `answered_questions`(사람이 답한 내용) > 제품 지침 프롬프트 > 제품 규칙 > 공통 규칙.

`rules/qa-guide.md` 가 없으면 findings 를 비운 채 `gate_status.G2 = "FAIL"` 과 질문 하나("QA 규칙
파일 없음")만 쓰고 끝낸다. 기억에 의존해 규칙을 대신하지 않는다.

## 입력과 자료

- 작업 입력 `in/<작업ID>.json` 의 `items[]` 가 분석 대상이다. 대상마다 코드가 Exact → BM25 로 고른
  후보(`candidates`)와 "왜 걸렸는지"(`match`)가 있다. **후보가 1차 근거다.**
- 후보가 부족하면 `context/srs_current.jsonl`(오늘 SRS 전체)을 검색해도 된다. 쓴 검색어는 Finding 의
  `detail` 에 적는다. 검색은 꼭 필요할 때 짧게 한다.
- TC·매뉴얼은 사람이 넣는 자료라 틀릴 수 있다. 자동 실행에는 들어오지 않는다. 사람이 버튼으로 요청한
  실행(TC 점검, 검증 TC 초안, 매뉴얼 점검)에만 `candidates.tcs`·`context/tc_index.jsonl`·`context/manuals/` 가 있다.
- 입력과 `context/`·`rules/` 밖의 자료를 찾지 않는다. 인터넷·명령 실행은 쓸 수 없다.

## 근거 규칙 (모든 Finding)

1. 판정마다 `evidence` 를 둔다. 위치는 **입력에 있는 번호·위치·ref 만** 쓴다. 코드가 실제 자료와
   대조해 없는 번호·위치는 빼고, 판정을 뒷받침하는 근거가 하나도 남지 않으면 Finding 을 버린다.
2. 입력에 없는 이슈·SRS·TC 번호를 만들어 쓰지 않는다. 대상 이슈 자신을 중복 후보로 쓰지 않는다.
3. Expected·Precondition·Step 의 근거는 현재 유효한 사양이다. 삭제·취소선·Deprecated 사양은
   `validity` 로 표시하고 Expected 근거로 쓰지 않는다.
4. 연구소 Comment·Resolution·조치 내용은 조사 단서일 뿐 단독 Expected 근거가 아니다.
5. 일부만 찾아보고 "사양 없음", "중복 없음"이라고 쓰지 않는다. 받은 후보와 검색한 범위를 `detail` 에 적는다.
6. 사양이 정하지 않은 동작은 결함이나 정상으로 정하지 않는다. `SPEC_UNDEFINED`(또는 Skill 의 같은 뜻
   값)로 두고 연구소·기획에 확인할 질문을 쓴다.
7. 과거 이슈와의 관계(재발, 재등록, 판정 충돌)는 **후보**로만 쓴다. 확정 사실처럼 쓰지 않는다.

## 금지

- 이슈 종료·Close 제안, 기존 TC 원본 수정·덮어쓰기, TC 결과·이력 삭제 제안. `action` 이나
  권고에 이런 내용이 있으면 코드가 Finding 을 버린다.
- 사양 근거 없이 Expected Result 를 지어내는 것. 근거가 모자라면 초안을 쓰지 않고 `tc_hold` 와 질문을 남긴다.
- 결과 파일 외의 파일 쓰기.

## 무인 모드

- 질문은 `open_questions` 에 적고, 그 질문에 걸리는 판정은 근거 부족 쪽 값으로 둔다.
- 결과에 영향을 주지 않는 정보는 묻지 않는다.
- 같은 질문이 `answered_questions` 에 이미 답해져 있으면 다시 묻지 않고 그 답을 근거로 쓴다
  (근거 `source_type: "user_answer"`).

## 결과 파일

형식과 예시는 `references/output-contract.md` — **결과 JSON 을 쓰기 직전에 반드시 읽는다.**
형식이 틀리면 작업 전체가 다시 실행되고, 두 번 틀리면 실패로 남는다.

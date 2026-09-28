---
name: vxvue-qa-rules
description: VXvue 일일 QA 점검 Skill(vxvue-spec-change-impact, vxvue-issue-verification, vxvue-manual-completeness, vxvue-trace-gap)이 공통으로 따르는 무인 실행 규칙, Gate, 근거 기준, 결과 JSON 형식. 이 Skill 들을 수행할 때 항상 먼저 읽는다.
---

# VXvue QA 공통 규칙 (무인 실행)

이 Skill 은 서버가 사람 없이 돌리는 점검에서 쓰인다. 대화로 질문할 수 없고, 결과는 사람이
다음 날 검토 화면에서 승인·거절한다. 따라서 **모든 산출물은 초안**이다.

## 규칙 원문

QA 규칙 원문은 작업 폴더의 `rules/qa-guide.md` (QA 작성 규칙 가이드)와
`rules/instruction-prompt.txt` (지침 프롬프트)에 있다. 이 Skill 은 원문을 복사하지 않고 어느
절을 읽을지만 가리킨다. **`rules/qa-guide.md` 가 없으면 결과 JSON 에 findings 를 비운 채
`gate_status.G2 = "FAIL"` 과 질문 하나("QA 규칙 파일 없음")만 쓰고 끝낸다.** 기억에 의존해
규칙을 대신하지 않는다.

충돌 우선순위: 입력 파일의 `answered_questions`(사람이 답한 내용) > 지침 프롬프트 > 가이드 > 이 Skill.

## 무인 모드에서 바뀌는 것

- 가이드의 Question-First 원칙은 "질문한 뒤 완성형 작성" 이다. 무인 모드에서는 질문을
  `open_questions` 에 적고, 그 질문에 걸리는 판정은 `사양 확인 필요` 로 둔다. 질문이 필요한
  항목에 대해 추측으로 Expected 를 쓰지 않는다.
- 결과에 영향을 주지 않는 정보는 묻지 않는다 (가이드 "Rev.1.17 질문 필요성 보정" 절, §68).
  버전·환경·장비는 결과를 바꿀 때만 묻는다.
- 같은 질문이 `answered_questions` 에 이미 답해져 있으면 다시 묻지 않고 그 답을 근거로 쓴다
  (근거 `source_type: "user_answer"`).

## Gate (가이드 §76 기준, G1~G7)

가이드 §44 는 Gate 를 5개(G1~G5)로 적은 옛 번호다. §62·§76 이 7개로 다시 정의했으므로
**§76 번호만 쓴다.** 각 Gate 의 뜻과 판정법은 `references/gates.md` — 결과의 `gate_status` 를
채우기 전에 읽는다.

## 근거 규칙 (모든 Finding)

1. Finding 마다 `evidence` 를 하나 이상 둔다. 근거의 `location` 에는 찾아갈 수 있는 위치를
   쓴다: SRS ID(`VP-1234`), Legacy 번호(`01-20-05`), TC 의 `파일 / 시트 / N행`, 가이드 절(`§61`).
   위치 없는 근거는 코드가 버린다.
2. Expected·Precondition·Step 의 근거는 현재 유효한 사양이다 (가이드 §7, §11, §58.2).
   삭제·취소선·Deprecated 사양은 `validity: "Deleted"` 등으로 표시하고 Expected 근거로 쓰지 않는다.
3. 일부 파일만 찾아보고 "사양 없음", "전수조사 완료" 라고 쓰지 않는다 (가이드 §10, §60.2).
   `context/srs_current.jsonl` 전체를 검색했을 때만 없다고 쓴다.
4. 연구소 Comment·Resolution 은 조사 단서일 뿐 단독 Expected 근거가 아니다 (가이드 §7).

## 금지 (가이드 §55)

- 이슈 종료·Close 제안, 기존 TC 원본 수정·덮어쓰기, TC 결과·이력 삭제 제안.
  `action` 필드에 이런 내용을 쓰면 코드가 Finding 을 버린다.
- 입력과 `context/`·`rules/` 밖의 자료 조회. 인터넷·명령 실행은 쓸 수 없다.
- 결과 파일 외의 파일 쓰기.

## 문구

TC·Issue 문구를 쓰거나 고칠 때는 가이드 §13(TC 설계 원칙), §14(Step–Expected 번호),
§57.1(Title 분류 Prefix 금지), §57.2·§71(QA 현업 문체, 추상적 표현 금지), §57.3(내부 구현 추정
금지)을 **초안을 쓰기 전에** 읽는다. Expected 는 관찰 가능한 UI/API 응답/로그/데이터 결과로 쓴다.

## 결과 파일

형식과 예시는 `references/output-contract.md` — **결과 JSON 을 쓰기 직전에 반드시 읽는다.**
형식이 틀리면 작업 전체가 다시 실행되고, 두 번 틀리면 실패로 남는다.

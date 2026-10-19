---
name: vxvue-qa-rules
description: VXvue 제품 규칙 Skill. 현재 VXvue QA 규칙의 Gate 의미와, QA Agent 분석 Skill 이 판정 전에 읽을 가이드 절을 알려 준다. VXvue 점검 작업에서 qa-common-rules 다음에 읽는다.
---

# VXvue 제품 규칙 (무인 실행)

공통 규칙은 `qa-common-rules` 에 있다. 이 Skill 은 **VXvue 에만 해당하는 것**만 적는다.
QA 규칙 원문은 작업 폴더 `rules/qa-guide.md`의 통합 운영 지침과 상세 QA 규칙이다.
서버형 VXvue Skill과 같은 원문을 사용한다. 개정 번호가 바뀌어도 현재 본문을 읽고 적용한다.
아래 절 번호는 탐색 힌트다. 번호가 달라지면 제목·Skill·기능으로 찾는다.
현재 원문의 Gate 의미를 결과 형식에 표현할 수 없으면 구체적인 충돌을 질문으로 남긴다.

## Gate (가이드 §76 기준, G1~G7)

가이드 §44 는 Gate 를 5개(G1~G5)로 적은 옛 번호다. §62·§76 이 7개로 다시 정의했으므로
현재 원문의 최종 Gate 정의를 확인한다. 각 Gate 의 뜻과 판정법은 `references/gates.md` — 결과의 `gate_status` 를
채우기 전에 읽는다. 프롬프트에 이미 들어 있으면 다시 읽지 않는다.

## 분석 Skill 별로 먼저 읽을 가이드 절

| Skill | 판정 전에 읽을 절 |
|---|---|
| qa-new-issue-analysis | §6(Issue 유형), §7(정보 우선순위), §9~§11(사양 근거·취소선·분할 사양), §37(근거 없는 확정 금지). Generator·장비 상태 이슈면 §3.9(S09 상태 동기화) |
| qa-fixed-issue-analysis | §6, §15(TC 형식), §32(Regression 영향 범위 8축), §33(Root Cause Coverage), §58(Test State Validity), §61(신규 TC 필요·중복), §73(수정확인과 Regression 경계). Generator·장비 상태 이슈면 §3.4·§3.5 의 Generator 문단과 §3.9(S09) |
| qa-verification-tc-draft | qa-fixed-issue-analysis 와 같은 절. Generator 이슈에 기존 TC 후보가 있으면 §17 "Generator Issue Expected 충돌 검사" |
| qa-spec-decision-analysis | §6(B 유형 Spec/Not Bug), §7, §9~§11, §37 |
| qa-comment-analysis | §7(연구소 Comment 는 탐색 단서) |
| qa-spec-coverage-analysis | §3.3(S03), §16(기존 TC 첨삭), §17(Generator Issue Expected 충돌 검사), §59(상태 생성 사양 역추적), §61, §69(완료 TC 의미 보존 편집), §70(Coverage Review 확장) |
| qa-manual-completeness | S07 B 모드(Completeness Review) 절 |

## VXvue 고유 관점

- Regression 축에 `GENERATOR`(Step·Size·Study·Dose Mode·Focal Spot·Save Dose)가 있다. X-ray 촬영 조건이
  닿는 이슈만 해당으로 본다.
- Generator·Console·Viewer 사이의 상태 이슈(Ready·Preparation·Error 표시, kVp·mA·ms·AEC 값 불일치,
  빠른 조작, 여러 Error, Viewer 재실행·Generator 재연결, 시간이 지나 바뀌는 상태)는 가이드 §3.9(S09)를 읽고 판정한다.
  지킬 것만 적는다. 장비·Console·Log·Viewer 상단·하단·촬영 Workflow 의 관찰 사실을 따로 적는다. 화면 표시가
  지워진 것을 장비 Error 해제나 수정 완료로 보지 않는다. 재조회·재수신, 경과 시간은 입력에 근거가 있을 때만 쓴다.
  Race Condition·Thread 같은 내부 원인은 분석 후보로만 두고 Actual·Expected 에 넣지 않는다.
- 기존 TC 의 Expected 와 이슈의 Expected 가 부딪치면 §17 "Generator Issue Expected 충돌 검사" 의 네 가지 분류
  가운데 하나와 이유를 `summary` 나 `action` 에 적는다. 이 분류는 `verdict` 값이 아니다. 완료된 TC 의 결과는
  바꾸자고 하지 않는다.
- DICOM Tag(`(0008,0018)` 모양), Command 번호, API/WebSocket 응답은 사양 근거 위치로 쓸 수 있다.
- Dose Table 이 없으면 공식 촬영 가능 조합·출력 정확도를 Expected 로 확정하지 않는다(§27). 실제 X-ray
  Exposure 가 불가하면 실제 촬영 결과를 Expected 로 만들지 않는다(§28).

## 문구

TC·Issue 문구를 쓰거나 고칠 때는 가이드 §13(TC 설계 원칙), §14(Step–Expected 번호),
§57.1(Title 분류 Prefix 금지), §57.2·§71(QA 현업 문체, 추상적 표현 금지), §57.3(내부 구현 추정
금지)을 **초안을 쓰기 전에** 읽는다. Expected 는 관찰 가능한 UI/API 응답/로그/데이터 결과로 쓴다.
Generator 상태 불일치 이슈의 Title·Step·Actual·Expected 는 §29 "Generator 상태 불일치 Issue 작성" 과
§52 의 Generator 자체검토 항목을 따른다. "동기화된다", "정상 반영된다" 같은 말만으로 Expected 를 쓰지 않는다.

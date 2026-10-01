---
name: vxvue-qa-rules
description: VXvue 제품 규칙 Skill. VXvue QA 규칙(가이드 Rev1.17)의 Gate 번호(§76 기준 G1~G7)와, QA Agent 분석 Skill 이 판정 전에 읽을 가이드 절을 알려 준다. VXvue 점검 작업에서 qa-common-rules 다음에 읽는다.
---

# VXvue 제품 규칙 (무인 실행)

공통 규칙은 `qa-common-rules` 에 있다. 이 Skill 은 **VXvue 에만 해당하는 것**만 적는다.
QA 규칙 원문은 작업 폴더 `rules/qa-guide.md`(`[QA 작성 규칙] VXvue TC 설계 및 자체검토 가이드`)와
`rules/instruction-prompt.txt`(지침 프롬프트)다. 이 Skill 은 원문을 복사하지 않고 절 번호만 가리킨다.

기준 판: Rev1.17. 제품 설정 `config/products/vxvue.yaml` 의 `qa_intelligence.rules.supported_rev` 와 같다.
판이 바뀌면 코드가 AI 단계를 멈춘다(REQ-DAILY-010). 이 Skill 을 새 판에 맞춰 고친 뒤 설정 값을 올린다.

## Gate (가이드 §76 기준, G1~G7)

가이드 §44 는 Gate 를 5개(G1~G5)로 적은 옛 번호다. §62·§76 이 7개로 다시 정의했으므로
**§76 번호만 쓴다.** 각 Gate 의 뜻과 판정법은 `references/gates.md` — 결과의 `gate_status` 를
채우기 전에 읽는다.

## 분석 Skill 별로 먼저 읽을 가이드 절

| Skill | 판정 전에 읽을 절 |
|---|---|
| qa-new-issue-analysis | §6(Issue 유형), §7(정보 우선순위), §9~§11(사양 근거·취소선·분할 사양), §37(근거 없는 확정 금지) |
| qa-fixed-issue-analysis | §6, §15(TC 형식), §32(Regression 영향 범위 8축), §33(Root Cause Coverage), §58(Test State Validity), §61(신규 TC 필요·중복), §73(수정확인과 Regression 경계) |
| qa-spec-decision-analysis | §6(B 유형 Spec/Not Bug), §7, §9~§11, §37 |
| qa-comment-analysis | §7(연구소 Comment 는 탐색 단서) |
| qa-spec-coverage-analysis | §3.3(S03), §16(기존 TC 첨삭), §59(상태 생성 사양 역추적), §61, §69(완료 TC 의미 보존 편집), §70(Coverage Review 확장) |
| qa-manual-completeness | S07 B 모드(Completeness Review) 절 |

## VXvue 고유 관점

- Regression 축에 `GENERATOR`(Step·Size·Study·Dose Mode·Focal Spot·Save Dose)가 있다. X-ray 촬영 조건이
  닿는 이슈만 해당으로 본다.
- DICOM Tag(`(0008,0018)` 모양), Command 번호, API/WebSocket 응답은 사양 근거 위치로 쓸 수 있다.
- Dose Table 이 없으면 공식 촬영 가능 조합·출력 정확도를 Expected 로 확정하지 않는다(§27). 실제 X-ray
  Exposure 가 불가하면 실제 촬영 결과를 Expected 로 만들지 않는다(§28).

## 문구

TC·Issue 문구를 쓰거나 고칠 때는 가이드 §13(TC 설계 원칙), §14(Step–Expected 번호),
§57.1(Title 분류 Prefix 금지), §57.2·§71(QA 현업 문체, 추상적 표현 금지), §57.3(내부 구현 추정
금지)을 **초안을 쓰기 전에** 읽는다. Expected 는 관찰 가능한 UI/API 응답/로그/데이터 결과로 쓴다.

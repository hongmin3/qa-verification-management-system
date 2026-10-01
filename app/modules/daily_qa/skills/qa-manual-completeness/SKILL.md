---
name: qa-manual-completeness
description: 지난 7일 바뀐 SRS 가운데 제품 매뉴얼(운영·서비스·DICOM Conformance·연동 가이드·API 등)에 반영이 빠졌을 수 있는 후보를 낸다. QA Agent 점검 F 단계 입력 파일(runs/<실행ID>/in/F-*.json)을 받았을 때 쓴다.
---

# F — 매뉴얼 누락 후보

QA 규칙 S07 의 B 모드(Completeness Review)다. 결과는 **후보**이며 확정 판정이 아니다.

**먼저 `qa-common-rules` 와 프롬프트가 알려 준 제품 규칙 Skill 을 읽는다.** 읽지 못했으면 findings 를 비운 결과와 질문 하나만 쓰고 끝낸다.

## 입력

`in/F-*.json`:

- `changed_srs[]`: `id`, `old_id`, `title`, `text`(오늘 본문), `fields`(바뀐 필드, 신규면 `added`).
- `manual_files[]`: `context/manuals/` 아래 매뉴얼 텍스트 파일 이름.
- `answered_questions[]`.

## 절차

가이드 §3.7 B 모드 확인 순서, §35(문서 개정 검토), §35.1(공통 설정 Coverage), §35.2(Manual-worthy
Gate), §35.3(Completeness 전수조사 완료 기준)을 **판정 전에 읽는다.**

SRS 마다:

1. 그 변경이 매뉴얼 독자(사용자·서비스 엔지니어·연동 개발자)에게 필요한 정보인지 먼저 본다
   (Manual-worthy Gate). 내부 구현·로그 형식처럼 독자에게 필요 없으면 Finding 을 만들지 않는다.
2. 해당 매뉴얼에서 기능명·메뉴명·설정 이름으로 검색한다. 상위 공통 절, 다른 절의 참조,
   다른 매뉴얼로의 위임까지 확인한다.
3. 판정:
   - 반영이 없어 보이면 `보강 권장` — 어느 매뉴얼 어느 절(찾은 가장 가까운 절)에 들어가야 하는지 적는다.
   - 다른 공식 문서가 다루면 `타 문서 위임 적절`.
   - 이미 반영돼 있으면 Finding 을 만들지 않는다 (`유지` 는 사람이 물어본 경우에만).
   - 사양끼리 상충하거나 반영 여부를 판단할 수 없으면 `사양 확인 필요` + 질문.
4. 매뉴얼 전체를 검색하지 않았으면 "누락" 이라고 단정하지 않는다. 검색한 파일과 검색어를
   `detail` 에 적는다.

## Finding 작성

- `subject`: SRS ID. 근거에 SRS 위치와 매뉴얼 위치(`파일명 / 절 번호 또는 제목`)를 함께 둔다.
- 매뉴얼 근거의 `source_type` 은 `manual`. 매뉴얼 위치에 절 번호가 없으면 SRS ID 가 들어간
  근거를 반드시 함께 둔다 (위치 규칙).
- `gate_status`: G1, G2, G7.

결과 JSON 을 쓰기 직전에 `qa-common-rules` 의 `references/output-contract.md` 를 읽는다.

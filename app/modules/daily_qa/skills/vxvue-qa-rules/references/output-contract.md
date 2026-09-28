# 결과 JSON 형식

결과는 프롬프트가 알려 준 `runs/<실행ID>/out/<작업ID>.json` 한 파일이다. UTF-8 JSON 객체 하나.
코드 검증기: `app/modules/daily_qa/schema.py` (`SkillResult`).

```json
{
  "skill": "vxvue-spec-change-impact",
  "task_id": "B-001",
  "gate_status": {"G1": "PASS", "G2": "PASS", "G3": "CHECK", "G7": "PASS"},
  "findings": [
    {
      "subject": "VP-1234",
      "subject_title": "목록 화면 표시 항목",
      "verdict": "수정 필수",
      "summary": "목록 화면의 표시 항목이 3개에서 4개로 바뀌었는데 TC 의 Expected 는 3개 기준이다.",
      "detail": "변경 전후 문장과 TC Expected 를 대조한 내용 (선택)",
      "confidence": "Review Needed",
      "evidence": [
        {"source_type": "srs", "location": "VP-1234 / 기능 요약 2번째 항목", "summary": "변경 후 문장 요지", "validity": "Current"},
        {"source_type": "tc", "location": "(TC) Example_TestCase.xlsx / Database / 57행", "summary": "Expected 3번", "validity": "Current"}
      ],
      "tc_ref": {"workbook": "(TC) Example_TestCase.xlsx", "sheet": "Database", "row": 57, "tc_id": "TC_DB_012"},
      "issue_type": "",
      "action": "Expected 3번을 변경 후 사양의 표시 항목 기준으로 수정 검토",
      "draft_tcs": []
    }
  ],
  "open_questions": [
    {"subject": "VP-1234", "question": "4번째 항목은 Admin 계정에서만 보이는가?", "why": "권한에 따라 Expected 가 달라진다"}
  ],
  "next_recommended_skill": "",
  "human_review_required": true
}
```

## 필드 규칙

- `skill`, `task_id`: 입력 작업과 정확히 같아야 한다. 다르면 작업 전체가 실패로 처리된다.
- `verdict`: Skill 별 허용 값만 쓴다.

| Skill | 허용 값 |
|---|---|
| vxvue-spec-change-impact | 유지 / 경미 수정 / 수정 필수 / 신규 TC 필요 / 사양 확인 필요 |
| vxvue-issue-verification | 신규 TC 필요 / 기존 TC 보강 / 유지 / 사양 확인 필요 / 판정만 |
| vxvue-manual-completeness | 보강 권장 / 타 문서 위임 적절 / 유지 / 사양 확인 필요 |

- `confidence`: `Confirmed` / `Review Needed` / `Unsupported`.
- `evidence[].source_type`: `srs` / `tc` / `issue` / `manual` / `rules` / `user_answer`.
- `evidence[].validity`: `Current` / `Deleted` / `Deprecated` / `Unknown`.
- `evidence[].location` 에는 SRS ID, Legacy 번호, `시트 / N행`, `§번호` 중 하나 이상이 있어야 한다.
- `issue_type` (issue-verification 만): `Program Fixed` / `Spec/Not Bug` / `Spec Change/Document Fix` /
  `Inquiry` / `Duplicate` / `Blocked` / `Cannot Reproduce`.
- `draft_tcs` (issue-verification 의 `Program Fixed` 만): 아래 형식. 다른 유형에 넣으면 버려진다.

```json
{"kind": "수정확인", "srs_no": "VP-2345", "change": "검색 날짜 조건", "change_detail": "시작일만 입력한 검색의 처리",
 "title": "검색 조건에 시작일만 입력", "precondition": "1. 로그인한다.\n2. 목록에 날짜가 다른 항목 3건을 등록한다.",
 "test_step": "1. 검색 조건에 시작일만 입력하고 검색한다.\n2. 검색 결과를 확인한다.",
 "expected_result": "2. 시작일 이후 항목 3건이 표시된다.", "test_data": ""}
```

- `kind`: `수정확인` 또는 `Regression`.
- `action`: 사람이 할 다음 조치를 한 문장으로. 종료·Close·덮어쓰기·삭제는 쓰지 않는다.
- 판정할 것이 없으면 `findings: []` 로 두고 끝낸다. 빈 결과도 올바른 결과다.

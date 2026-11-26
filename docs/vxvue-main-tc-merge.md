# ALM 변경분을 Main TC에 반영하기 전 셀 검사

1. 사용자가 지정한 기준 XLSX와 대상 TC의 시트·셀·식별값을 고정한다. 최신 원본은 실제 예약 동기화가 읽는 경로와 서버 등록본을 대조해 선택한다.
2. 승인 범위를 후보 편집 전에 JSON으로 작성한다. 각 항목에 같은 행의 TC 식별 셀과 정확한 이전·새 값을 적는다. 승인값이 공란이면 `null`을 쓴다.
3. 최신 원본의 사본을 편집한 뒤 아래 명령으로 확인한다. 예시의 파일명과 셀 주소는 합성 값이다.

```text
python -X utf8 scripts/verify_vxvue_main_tc_merge.py baseline.xlsx current.xlsx candidate.xlsx approvals.json
```

```json
{
  "baseline_sha256": "<지정 기준 XLSX의 SHA-256>",
  "changes": [
    {
      "sheet": "Registration",
      "cell": "J2",
      "id_cell": "B2",
      "before": "이전 문구",
      "after": "승인한 문구",
      "reason": "사용자가 승인한 제목 변경"
    }
  ]
}
```

종료 코드 0과 `passed: true`를 확인한다. `pending`은 최신 원본에 적용할 승인 셀이다. `already_applied`만 있고 `pending`이 비어 있으면 다시 저장할 필요가 없다. 코드는 1이면 충돌·누락·승인 밖 변경·시트 구조 차이를, 2이면 잘못된 입력이나 기준 해시 차이를 보고한다.

이 명령은 셀 값·수식과 시트 목록·순서·표시 상태·병합 범위를 비교한다. 서식, 도형, 매크로, 외부 연결과 Excel 표시 상태는 사본에서 별도로 확인한다. 서버에 반영하기 직전에 실제 최신 원본 해시가 출력의 `current_sha256`과 같은지 다시 확인한다. 승인된 운영 저장 뒤에는 통합 QA 지침 81.12에 따라 서버 재다운로드·해시·예약 반영·색인·대표 수정 셀을 확인한다.

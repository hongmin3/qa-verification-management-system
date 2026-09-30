---
name: vxvue-manual-revision-review
description: VXvue 개정 매뉴얼(변경 추적 .docx 또는 .pdf)의 변경마다 최신 사양서와 맞는지 판정하고 결과 Excel과 Word Comment 파일을 만든다. 사용자가 "이 경로의 매뉴얼 분석해 줘", "매뉴얼 분석", "매뉴얼 개정 검증", "개정 매뉴얼 검토", "Service Manual 검토해 줘", "매뉴얼 Comment 달아 줘"처럼 말하면 쓴다.
---

# 매뉴얼 개정 검증 (Claude 대화)

사양: `SPEC.md` 의 REQ-MANUAL-018. 판정 기준은 화면 검증(REQ-MANUAL-011·012)과 같다.
판정은 네가 하지만, 변경 추출·사양서 근거 후보·Release 대조·Comment 쓰기는 도구가 한다.
도구는 `scripts/manual_review_local.py` 이고 이 저장소 루트에서 `.venv/Scripts/python.exe` 로 실행한다.
명령 앞에는 늘 `PYTHONIOENCODING=utf-8` 을 붙인다. 이 도구는 Windows 콘솔용으로 cp949 로 출력하므로,
붙이지 않으면 `결과 폴더:` 줄과 오류 문장의 한글이 깨져 읽을 수 없다.

## 1. 경로 확인

1. 사용자 말에서 개정 매뉴얼 경로를 찾는다. 참고 문서(Release Note, 설계검토보고서), 제품(기본 `VXvue`),
   앞 회차 `comments.json` 도 말했으면 함께 적어 둔다.
2. 파일이 있는지 확인한다. 매뉴얼이 `.pdf` 면 이전 판 `.pdf` 가 꼭 필요하다. 없으면 먼저 묻는다.
3. 경로가 모호하면(폴더만 줬고 매뉴얼이 여러 개 등) 추측하지 말고 어느 파일인지 묻는다.
4. 원본 매뉴얼·참고 문서는 읽기만 한다. 고치거나 옮기지 않는다.

## 2. 준비 (`extract`)

```text
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/manual_review_local.py extract --manual "<개정 매뉴얼>" \
    [--previous "<이전 판 PDF>"] [--release-note "<RN>"]... [--design-review "<설계검토>"]... \
    [--product VXvue] [--prior-comments "<앞 회차 comments.json>"]
```

- 출력 첫 줄 `결과 폴더: …` 가 이번 실행 폴더다. 끝까지 이 폴더를 쓴다.
- 종료 코드 2 는 입력 파일 문제다. 출력된 파일 이름을 사용자에게 알리고 멈춘다.
- `경고:` 줄이 있으면 사용자에게 그대로 전한다. 특히 "지식 사본에 사양서가 없어…"가 나오면 모든 판정이
  근거 없는 판정이 된다고 알리고 계속할지 묻는다.

## 3. 판정 (`changes.json` → `decisions.json`)

먼저 규칙을 읽는다.

- `app/modules/daily_qa/skills/vxvue-qa-rules/SKILL.md` 가 가리키는 QA 규칙 원문(지식 사본의 QA 작성 규칙
  가이드) 가운데 문서 개정 검토 절(가이드 §35 계열)이 있으면 판정 전에 읽는다. 없으면 이 문서의 기준만 쓴다.
- 판정 값과 한국어 이름은 `changes.json` 의 `judgment_values` 목록을 그대로 쓴다. 새 값을 만들지 않는다.

`changes.json` 의 `changes` 가운데 `functional: true` 인 변경만 판정한다. 단순 변경(`functional: false`)은
판정하지 않는다. 변경마다 다음 기준을 지킨다.

1. 근거는 그 변경의 `srs_candidates`(최신 사양서 조각)만 쓴다. 기억이나 다른 문서로 사양을 지어내지 않는다.
2. 후보 조각에 근거가 없으면 `SPEC_CONFIRMATION_REQUIRED` 로 두고 `evidence` 를 비운다.
3. 변경 글자가 근거 조각과 맞으면 `PASS`. 조건·예외·단위가 빠졌으면 `SUPPLEMENT_REQUIRED`, 사양과 다르면
   `MODIFICATION_REQUIRED`, 삭제된 사양을 아직 설명하면 `DELETE_REQUIRED`, 문서 버전이 헷갈리면
   `VERSION_CONFIRMATION_REQUIRED`, 판단할 정보가 모자라면 `UNABLE_TO_DETERMINE`.
4. `release_context` 가 있으면 참고로 쓴다. `result_status: FAIL` 이면 사람 확인이 필요하다고 적는다.
5. 이미지 변경(`image_change: true`)과 PDF 변경은 글자만으로 확정하지 않는다. 사람 검토가 필요하다고 적는다.
   이미지 변경을 `PASS` 로 적어도 `finish` 가 "판정 불가"로 바꾼다.
6. 권장 문구(`recommended_manual_text`)와 QA Comment(`qa_comment`)는 근거 조각의 표현만으로 쓴다.
   근거에 없는 수치·메뉴명·동작을 추측으로 넣지 않는다. QA Comment 는 연구소가 읽는 한국어 한두 문장이다.

`decisions.json` 의 `decisions` 줄마다 채운다.

| 칸 | 값 |
|---|---|
| `decision` | 판정 코드 (예: `SUPPLEMENT_REQUIRED`) |
| `confidence` | 0~1 숫자 |
| `evidence` | `[{"chunk_id": "<그 변경의 candidate_ids 가운데 하나>", "type": "DIRECT_SPEC"}]`. 근거 없으면 `[]` |
| `problem`, `recommended_manual_text`, `qa_comment` | 문제가 있을 때만. `PASS` 면 비운다 |

`prior_comments` 가 있으면(앞 회차 지적사항) `suggestion`(반영 의심/미반영 의심/판단 불가)을 참고로 보여 주고,
`status` 는 QA 가 말한 대로만 바꾼다(`RESOLVED`, `NOT_RESOLVED`, `REOPENED`, `IGNORED_BY_QA`). 추측으로 바꾸지 않는다.

## 4. 요약을 보이고 수정 요청 반영

`finish` 전에 대화에 보인다.

- 판정 값별 건수(한국어 이름)
- 문제 있는 변경 목록: 변경 번호, 변경 글자 앞부분, 판정, 한 줄 이유, 근거 조각
- 사람 확인이 필요한 변경(이미지·PDF·근거 없음), 누락 의심 Release 항목, 이전 지적사항

QA 가 "C007 은 문제없음으로" 같은 수정을 말하면 `decisions.json` 을 고치고 바뀐 줄을 다시 보인다.
QA 가 확정한다고 말할 때까지 `finish` 를 부르지 않는다.

## 5. 마무리 (`finish`)

```text
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/manual_review_local.py finish --run-dir "<결과 폴더>"
```

- 종료 코드 1 이면 출력된 변경 번호(빈 판정, 목록에 없는 값, 후보 밖 근거)를 고치고 다시 실행한다.
- 이미지 변경을 판정 불가로 바꿨다는 줄이 나오면 사용자에게 알린다.

## 6. 결과 안내

결과 폴더의 파일 경로를 알려 준다.

| 파일 | 내용 |
|---|---|
| `manual_review.xlsx` | 변경마다 판정·근거·권장 문구 (판정은 한국어 이름) |
| `<매뉴얼 이름>_QA_Comment.docx` | Comment 를 넣은 사본. `.docx` 매뉴얼이고 문제 항목이 있을 때만 |
| `comments.json` | 이번 회차 지적사항. 다음 회차에 `--prior-comments` 로 넘긴다 |

결과는 이 PC 의 `output/manual_review_local/` 에만 있고 git 에 올리지 않는다. 핵심 앱 DB·화면 이력에는 남지 않는다.
AI 판정은 QA 판단을 돕는 초안이며 최종 승인이나 사양 확정을 대신하지 않는다고 함께 적는다.

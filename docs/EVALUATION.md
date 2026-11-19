# QA 답변 평가

## 벨라룬·VXvue 공용 스킬 회귀시험

Codex CLI에 로그인하고 `vxvue-ai` MCP와 `bellalun-server-ai`, `vxvue-ai` 스킬을 설치한 PC에서 실행한다. 각 질문은 새 대화에서 실행된다. 실행기는 설치된 스킬 파일을 업무 지침으로 전달하고 해당 제품의 읽기 도구만 연다.

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\qa_skill_eval.py
```

`--case bellalun-id-only` 또는 `--case vxvue-image-page`로 한 질문을 고른다. `--model`을 주면 해당 모델로 실행하고, 생략하면 Codex 설정을 따른다. `--report`에는 저장할 새 JSON 경로를 지정한다.

| 결과 | 뜻 |
|---|---|
| `PASS` | 최종 답변의 모든 필드와 필수 근거 조회를 확인했다. 두 질문 모두 통과하면 종료 코드는 0이다. |
| `FAIL` | 답변 필드나 도구 호출 검사가 실패했다. 다른 실행 오류가 없으면 종료 코드는 1이다. |
| `SOURCE_CHANGED` | 기준 문서가 바뀌었거나 목록에서 사라졌다. 새 원문을 읽고 문서 해시와 정답을 함께 검토한다. 종료 코드는 2다. |
| `RUN_ERROR` | CLI 시작·완료 또는 상태 조회에 실패했다. 종료 코드는 2다. |

결과는 사례별로 스킬 파일 해시, 문서 판, 검사별 결과, 최종 답변, 실행 시간과 CLI가 보고한 토큰 수를 남긴다. 회귀시험은 정해진 질문과 구조화된 답을 대상으로 한다. 실장비 시험 결과와 자연어 보고서의 문장 품질은 별도 검증 대상이다.

모델과 서버를 부르지 않고 채점기만 검증할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_qa_skill_eval.py
```

## Regression 추천 평가 종료 안내

2026-10-01에 별도 Regression 영향 분석과 함께 정답 TC 입력 화면·precision/recall/F1 계산·`scripts/evaluate_analysis.py`를 없앴다.

기존 SQLite의 `analyses`·`analysis_evaluations`와 저장된 결과 파일은 보존한다. 새 분석의 검토와 QA 결정 기록은 [QA Agent](modules/qa-agent.md)에서 확인한다. 옛 기능의 요구사항 번호(REQ-CORE-001~006)는 [SPEC](../SPEC.md)의 추적성 표에 `deprecated`로 남긴다.

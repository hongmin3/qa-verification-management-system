# Regression 영향 분석 (`/impact-analyzer`)

> 코드: [`app/modules/impact_analyzer/`](../../app/modules/impact_analyzer)
> · 앱 안 사용법: `/impact-analyzer/guide`
> · 상위 문서: [README](../../README.md) · [문서 지도](../README.md)

SW 변경사항을 등록된 제품 사양서·Test Case와 대조해, 이번 변경으로 **다시 검증해야 하는
TC**와 그 판단 근거를 추천한다.

## 무엇을 해결하는가

SW 변경이 생겼을 때 "이번엔 어디까지 다시 봐야 하는가"는 대부분 담당자의 경험에 의존한다.
사람이 판단하면 이런 문제가 생긴다.

- 담당자에 따라 검증 범위가 달라진다 (재현되지 않는 기준)
- 사양서가 여러 리비전으로 쌓이면 "무엇이 실제로 바뀌었는지"부터 다시 찾아야 한다
- TC가 수백 건이면 관련 TC를 눈으로 훑는 데만 시간이 든다
- 빠뜨린 TC가 있어도 사후에 그 사실을 확인할 방법이 없다

## 처리 흐름

```text
변경 문서 (여러 개 첨부 가능 / 문서 없이 요청 텍스트만도 가능)
  ↓ 기준 사양서와 diff — "진짜 바뀐 줄"만 남긴다
Change 추출 (Rule 기반, 결정적)
  ↓ BM25 검색 — 사양서 근거 상위 K건
Specification 근거 선정
  ↓ Rule 기반 후보 압축 — TC 후보 N건까지
TC Candidate 선정
  ↓ Gemini Structured Output — 분석 1건당 정확히 1회 호출
의미적 판정 (검증 필요 / 불필요 + 근거 + Confidence)
  ↓ TC ID · Chunk ID 교차검증 — 모델이 지어낸 ID를 걸러낸다
HTML 보고서 + XLSX + 신규 TC 초안(md)
```

각 단계에서 LLM에 실제로 도달하는 입력을 어떻게 줄이는지는
[비용 절감 설계](../COST_OPTIMIZATION.md)에 단계별로 정리했다.

## 설계상 중요한 결정

**기준 사양서 diff를 먼저 한다.** 변경 문서 전체에서 키워드를 뽑으면, 바뀌지 않은 문장까지
변경으로 오인해 불필요한 AI 판정을 만든다. 등록된 기준 사양서와 실제로 diff해서 바뀐 줄만
분석 대상으로 삼는다 (`regression_analyzer.py`).

**모델이 만든 ID를 그대로 믿지 않는다.** 응답의 TC ID는 AI에게 보낸 TC 후보와, 근거 Chunk ID는
보낸 사양 조각과 대조한다. 보내지 않은 TC의 판정은 버리고, 보내지 않은 조각 번호는 지운다 (`validation.py`).

**읽지 못한 Knowledge 문서는 따로 보인다.** 파일이 없거나 읽다가 실패한 문서는 "사용한 문서"에 넣지
않는다. 분석 결과의 `knowledge_failures`, 보고서 1절, 분석 상세 화면에 "읽지 못한 문서"로 보인다.

**추천 여부는 AI가 정하고, Confidence는 사람이 볼 곳을 표시한다.** 추천(`recommended`)은 AI 응답
값을 그대로 쓴다. Confidence는 검토 상태만 정한다.

| Confidence | 검토 상태 | 사람 확인 필요 표시 |
|---|---|---|
| `analysis.recommended_confidence`(기본 0.80) 이상 | AI 추천 채택 (`AI_RECOMMENDATION_ACCEPTED`) | 끔 |
| `analysis.review_confidence`(기본 0.60) 이상 | 검토 권장 (`REVIEW_RECOMMENDED`) | 끔 |
| 그 미만 | 사람 확인 필요 (`MANUAL_REVIEW_REQUIRED`) | 켬 |

근거 조각이 하나도 남지 않은 판정은 Confidence를 0.59 이하로 낮춘다. 근거가 취소선(삭제된 사양)이면
검토 상태를 사람 확인 필요로 바꾼다. 신뢰도가 낮아도 AI가 추천한 판정은 추천 목록에 남고, 보고서에서
"확인 요청"으로 표시된다.

**커버되지 않는 변경은 신규 TC 초안으로 남긴다.** 기존 TC 어느 것으로도 검증되지 않는
변경이 발견되면 "해당 없음"으로 끝내지 않고 신규 TC 초안(md)을 생성한다.

**진행 상태는 실제 백엔드 단계다.** SSE로 현재 단계를 그대로 흘려보낸다. 시간이 지나면
올라가는 가짜 퍼센트를 쓰지 않는다.

## 결과물

| 산출물 | 내용 |
|---|---|
| HTML 보고서 | 의미 단위 Change Summary, 단순화된 TC 표, 사람이 읽는 사양 근거 |
| XLSX | 검증을 통과한 판정 전체 (추천 아닌 것 포함, `Recommended` 열로 구분). 검증 계획에 그대로 붙여 쓸 수 있는 형태 |
| 신규 TC 초안 (md) | 기존 TC로 커버되지 않는 변경에 대한 초안 |
| 분석 상세 화면 | 요청 문서, Knowledge 근거, System Instruction, Gemini에 실제로 전달된 입력 JSON과 원본 응답, 모델·캐시·생성 설정, BM25 후보 순위와 점수, QA 확정 정답과 정확도 |

마지막 항목이 이 기능의 감사 근거다. "AI가 왜 이렇게 판단했는가"를 사후에 그대로 열어볼 수
있어야 검증 결과를 업무에 쓸 수 있다.

## 관련 설정 (`config.yaml`)

| 키 | 기본값 | 의미 |
|---|---|---|
| `retrieval.specification_top_k` | 8 | LLM에 넣을 사양서 근거 개수 |
| `retrieval.candidate_limit` | 150 | TC 후보 상한 |
| `retrieval.change_text_top_lines` | 60 | 요청 관련 변경 문서 줄 수 상한 |
| `analysis.recommended_confidence` | 0.80 | 검토 상태 "AI 추천 채택" 기준 (추천 여부는 바꾸지 않음) |
| `analysis.review_confidence` | 0.60 | 이 값 미만이면 "사람 확인 필요" |
| `analysis.cache_enabled` | true | 동일 입력 응답 캐시 |
| `analysis.daily_token_limit` | 0 (비활성) | 하루(한국 시간 0시부터) 토큰 한도. 새 분석과 재실행 모두 검사한다. 실패한 분석이 쓴 토큰도 센다 |
| `analysis.max_concurrent_jobs` | 2 | 동시 분석 실행 수 |

## 정확도 평가

완료된 분석의 상세 화면에서 QA 확정 TC를 저장하면 분석별 및 누적 precision·recall·F1과
누락/과추천 TC가 즉시 표시된다. 운영 방법과 파일 기반 CLI 절차는
[정확도 평가](../EVALUATION.md)에 있다.

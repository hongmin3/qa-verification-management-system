# 핵심 앱 — AI 호출 규칙

> 이 서비스는 "판단이 꼭 필요한 지점에서만, 최소한의 근거로 LLM을 부른다"를 원칙으로 한다.

## 호출 횟수와 입력 최소화
<!-- akela: id=single-call-rule scope=core-development tier=must -->

- Regression 분석은 **1건당 Gemini 호출 정확히 1회**다. 재파싱·재질의 왕복을 만들지 않는다.
- 매뉴얼 개정 검증은 quick 판정이 PASS면 detail 호출을 생략한다(PASS short-circuit).
- 파싱·diff·검색·후보 압축·1차 필터링은 전부 결정적인 Python 코드가 한다. 이 구간을 LLM으로 대체하지 않는다.
- 전체 사양서·전체 TC를 그대로 보내지 않는다. `retrieval.specification_top_k`, `retrieval.candidate_limit`, `retrieval.change_text_top_lines`로 잘라서 보낸다.

## 프롬프트는 YAML 파일이다
<!-- akela: id=prompt-yaml scope=core-development tier=must -->

- 프롬프트는 `app/prompts/<name>.yaml`에 있고 본문·`version`·생성 설정(`temperature`, `max_output_tokens`, `thinking_budget`)을 한 파일에 담는다. 코드에 프롬프트 문자열을 두지 않는다.
- 프롬프트 내용을 고치면 **`version`을 함께 올린다.** 버전이 캐시 키에 들어가므로, 올리지 않으면 옛 응답이 계속 재사용된다.
- `thinking_budget`의 기본값은 `0`이다. 이 서비스의 호출은 근거 기반 구조화 추출·판정이라 내부 추론이 필요 없고, Gemini 2.5의 thinking 토큰이 `max_output_tokens` 예산을 함께 소비해 JSON이 잘리는 문제를 막는다.

## 응답 캐시 키
<!-- akela: id=response-cache-key scope=core-development tier=must -->

- 캐시 키는 `sha256(model + prompt_name + prompt_version + prompt 본문)`이다 (`app/core/gemini_client.py`).
- 동일 입력이면 API 호출 자체가 발생하지 않는다. `analysis.cache_enabled`로 끌 수 있다.
- 캐시는 `Storage.cache_get`/`cache_set`으로 SQLite에 저장된다. 캐시 적중 여부는 `last_cache_hit`으로 노출돼 감사 화면과 비용 대시보드에 집계된다.

## 모델이 만든 ID를 믿지 않는다
<!-- akela: id=id-cross-validation scope=core-development tier=must -->

`app/modules/impact_analyzer/validation.py::validate_decisions`가 강제하는 규칙이다. 새 AI 기능을 붙일 때도 같은 방식으로 검증한다.

- 응답의 `tc_id`가 이번에 AI에게 보낸 TC 후보에 없으면 그 판정을 **결과에서 제외**한다(REQ-IMPACT-010).
- 근거 `relevant_specifications`는 실제 Chunk ID만 남기고 걸러낸다.
- 걸러낸 뒤 근거가 하나도 남지 않으면 `confidence`를 **0.59로 낮추고** `manual_review_required`를 세운다. 근거 없는 판정이 사람 확인 없이 채택되지 않게 하기 위해서다. 추천 여부(`recommended`)는 AI 값을 그대로 쓴다(REQ-IMPACT-010).

## Confidence 분류
<!-- akela: id=confidence-classification scope=core-development tier=should -->

`classify_confidence`가 `analysis.recommended_confidence`(기본 0.80), `analysis.review_confidence`(기본 0.60) 기준으로 나눈다.

- `>= recommended` → `AI_RECOMMENDATION_ACCEPTED`
- `>= review` → `REVIEW_RECOMMENDED`
- 그 미만 → `MANUAL_REVIEW_REQUIRED` (+ `manual_review_required=True`)

임계값을 코드에 하드코딩하지 않고 항상 설정에서 읽는다.

## 사람이 확인해야 하는 것은 AI가 통과시키지 않는다
<!-- akela: id=human-review-gates scope=core-development tier=must -->

- 이미지 변경(DOCX drawing/pict, PDF 페이지 이미지 hash 변화)은 `IMAGE_CHANGE_REVIEW_REQUIRED`로 강제 표시한다. 텍스트만으로 의미를 판단할 수 없으므로 AI가 PASS 처리하게 두지 않는다.
- PDF diff 판정은 confidence 상한 60%이고 `PDF_DIFF_REVIEW_REQUIRED`를 붙인다. PDF에는 Word Comment를 생성하지 않는다.
- Cross-Manual 영향은 `REVIEW_REQUIRED` 후보로만 표시하고 자동 확정하지 않는다.
- 이전 Round 지적사항의 상태는 QA가 확정하기 전까지 자동으로 바꾸지 않는다.

## 일일 토큰 한도
<!-- akela: id=daily-token-guard scope=core-development tier=should -->

- `analysis.daily_token_limit`(0=비활성)을 넘으면 **새 분석 실행을 시작 전에 429로 차단**한다. 실행 중인 분석에는 영향을 주지 않는다.
- 사용량은 `Storage.tokens_used_since`로 집계하며 `/config/status`와 `/cost-dashboard`에서 확인한다.

## 반복 실행 시 주의
<!-- akela: id=rerun-caveats scope=core-development tier=should -->

동일 AI 입력은 SQLite Cache를 재사용한다. 업로드·DB·인덱스·로그·보고서는 Git에 포함하지 않는다. Unit Test는 Gemini Mock을 사용한다.

## 알려진 이슈
<!-- akela: id=known-issues scope=core-development tier=should -->

Gemini Key가 없으면 실제 분석은 실패하지만 Web 서비스는 유지된다. Python 3.14 개발 환경에서는 PyYAML 6.0.3 및 Pydantic 2.12.5 이상이 필요하다.

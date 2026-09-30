# 비용 절감 설계 — LLM을 언제 부르지 않을 것인가

> 상위 문서: [README](../README.md) · [문서 지도](README.md)

## 왜 이것이 핵심 설계 원칙인가

사양서·TC·매뉴얼 원문을 통째로 LLM에 넘기면 정확도는 높아 보여도 호출당 토큰이 급증하고,
분석 1건마다 비용이 선형으로 늘어나 사내 서비스로 지속 운영하기 어렵다. 이 프로젝트의 원칙은
하나다.

> **판단이 꼭 필요한 지점에서만, 최소한의 근거로 LLM을 부른다.**

파싱·검색·후보 압축·1차 필터링은 전부 결정적인 Python Rule Engine이 처리하고, Gemini는 의미적
판단이 꼭 필요한 마지막 단계에서 구조화된 입력으로 단 한 번(또는 조건부로 그 이하) 호출된다.

부수 효과가 하나 더 있다. Rule Engine이 처리하는 구간은 **매번 같은 입력에 같은 출력**을
내므로, 결과가 흔들리는 범위 자체가 좁아진다.

## 파이프라인 — 단계마다 입력을 줄인다

괄호 안은 관련 코드와 설정이다.

**1. Rule Engine 사전 필터**
매뉴얼 검증에서 페이지 번호·저작권 표기·목차 리더 점선 같은 `NON_FUNCTIONAL_CHANGE`는 애초에
AI 분석 대상에서 제외한다.
`app/modules/manual_review/change_filter.py::is_functional_change`

**2. 기준 사양서 Diff**
변경 문서 전체에서 키워드를 뽑는 대신 등록된 기준 사양서와 실제 diff해 "진짜 변경된 줄"만
분석 대상으로 삼는다. 미변경 문장을 변경으로 오인해 불필요한 AI 판정을 만드는 문제를
근본적으로 막는다.
`app/modules/impact_analyzer/regression_analyzer.py`

**3. BM25/RAG Top-K 후보 압축**
사양서 근거는 BM25로 상위 `retrieval.specification_top_k`(기본 8)건만, TC 후보는
`retrieval.candidate_limit`(기본 150)건까지만 골라 LLM 입력에 포함한다. 전체 사양서·전체 TC를
매번 통째로 보내지 않는다.

**4. 변경 문서 관련 줄 축소**
사용자 요청 사항이 있으면 변경 문서 전체 대신 요청과 관련성 높은 줄만 BM25로 추려 보낸다
(`retrieval.change_text_top_lines`, 기본 60줄). 문서가 이보다 짧으면 그대로 전체를 사용한다.
`app/modules/impact_analyzer/change_analyzer.py::trim_by_relevance`

**5. Structured Output 단일 호출**
Gemini는 JSON Schema로 강제된 Structured Output을 반환하며, Regression 분석 1건당 정확히 1회만
호출한다. 재파싱·재질의 왕복이 없다.

**6. 매뉴얼 quick/detail 2단계 + PASS short-circuit**
매뉴얼 개정 변경은 먼저 짧은 quick 판정만 수행하고, 결과가 PASS면 비용이 큰 detail 호출을
생략한다. 문제가 의심될 때만 상세 근거·판정을 추가로 요청한다.
`app/prompts/manual_revision_{quick,detail}.yaml`

**7. SHA-256 응답 캐시**
모든 Gemini 호출은 `sha256(model + prompt명 + prompt버전 + prompt내용)`을 키로 캐시된다. 동일
입력으로 재분석·재검증하면 API 호출 없이 캐시 응답을 그대로 재사용한다.
`app/core/gemini_client.py`, `analysis.cache_enabled`

**8. `thinking_budget=0`**
이 서비스의 모든 AI 호출은 근거 기반 구조화 추출·판정이라 별도의 내부 추론이 필요 없다.
`thinking_config.thinking_budget=0`으로 내부 reasoning 토큰 소비를 비활성화해 같은
`max_output_tokens` 예산을 응답 생성에 온전히 쓴다.
`app/prompts/*.yaml`

**9. 일일 토큰 한도 + 감사 기록**
`analysis.daily_token_limit`을 넘으면 새 분석 실행 자체를 차단한다 (`/config/status`에서 사용량
확인). 완료된 모든 호출은 요청 문서, Knowledge 근거, System Instruction, AI 에 실제로
전달된 입력 JSON과 원본 응답, 모델·캐시·생성 설정, BM25 후보 순위·점수를 분석 상세 화면에서
그대로 열람할 수 있어 비용과 판단 근거를 사후 검증할 수 있다.

## 사용량 확인

| 위치 | 내용 |
|---|---|
| `/cost-dashboard` | 호출 수·토큰 사용량·캐시 적중 집계 |
| `/config/status` | 일일 토큰 사용량과 한도, API Key 설정 여부(값은 반환하지 않음) |
| 분석 상세 화면 | 이 분석 1건이 실제로 무엇을 보내고 무엇을 받았는지 |

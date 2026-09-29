## 실행 흐름
<!-- akela: id=execution-flow scope=all tier=must -->

Regression 영향 분석(`/impact-analyzer`)은 Knowledge 등록 → Change PDF Upload → Rule 분석 → 사양 Top-K 검색 → TC 후보 축소 → Gemini 의미 판단 → ID/Schema/Confidence 검증 → HTML/CSV 생성 순서로 실행한다.

일일 QA 점검(`/daily-qa`)은 이 흐름을 쓰지 않는다. Polarion 을 직접 읽고 Claude Skill 로 판정한다(SPEC REQ-DAILY-001).

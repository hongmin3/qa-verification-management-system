"""QA Intelligence 점검 엔진 (SPEC REQ-DAILY-*, specs/qa-intelligence.md, NFR-SEC-001).

서버의 예약 실행과 대시보드의 [지금 실행]이 `scripts/run_daily_qa.py` 로 `pipeline.run_daily` 를 부른다.
수집·스냅샷·변경 감지·분석·검증은 이 패키지가 맡고, 결과 화면은 `app/modules/qa_agent` 의
`/qa-agent` 대시보드가 맡는다. 제품 차이는 제품 설정과 `product_adapter.py` 가 흡수한다.
"""

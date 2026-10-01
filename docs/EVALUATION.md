# Regression 추천 평가 종료 안내

2026-10-01에 별도 Regression 영향 분석과 함께 정답 TC 입력 화면·precision/recall/F1 계산·`scripts/evaluate_analysis.py`를 없앴다.

기존 SQLite의 `analyses`·`analysis_evaluations`와 저장된 결과 파일은 보존한다. 새 분석의 검토와 QA 결정 기록은 [QA Agent](modules/qa-agent.md)에서 확인한다. 옛 기능의 요구사항 번호(REQ-CORE-001~006)는 [SPEC](../SPEC.md)의 추적성 표에 `deprecated`로 남긴다.

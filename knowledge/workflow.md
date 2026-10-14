## 실행 흐름
<!-- akela: id=execution-flow scope=all tier=must -->

핵심 앱의 분석 흐름은 세 가지다. 순서와 세부 단계는 SPEC 1절 "한눈에 보기"와 각 REQ 가 기준이고, 여기서는 어느 흐름을 고치는지 가르는 데만 쓴다.

- QA Intelligence Agent 일일 점검(`/qa-agent` 대시보드, `/daily-qa` 는 옛 주소): 평일 04:00 예약·놓친 오늘 예약 만회·[지금 실행] → Polarion SRS·이슈 직접 수집 → 스냅샷 비교 → 변경 이벤트 → Claude Skill(바뀐 것만) → 결과 형식·근거 검증 → 요약 메일·대시보드 (REQ-DAILY-001, REQ-QAINTEL-001·035).
- QA Agent 단일 이슈 분석(`/qa-agent/issue-analysis`): 이슈 하나를 등록 지식과 대조해 검증 범위를 낸다 (5.2절).
- 매뉴얼 개정 검증(`/manual-review`): 변경 추적 `.docx`·`.pdf` 의 변경마다 사양과 맞는지 판정해 Excel·Word Comment 를 만든다 (5.3절).

Regression 영향 분석(`/impact-analyzer`)은 2026-10-01 커밋 `0fdf2f2` 로 제거됐다. 옛 주소는 308 로 넘어간다. 이 흐름을 되살리거나 확장하지 않는다.

AI 호출은 기본이 Claude CLI(`ai.provider: claude_cli`)다. 모델·추론 정도는 `ai.claude.models.*`·`ai.claude.effort` 한 곳에서 읽고, 일일 점검 작업도 같은 값을 쓴다 (REQ-AICALL-005, REQ-DAILY-016).

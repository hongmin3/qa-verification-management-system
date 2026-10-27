# VXvue 활용 이력 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. 다른 agent 생성은 하지 않는다.

**Goal:** 본인 선택 이력과 팀 익명 호출 지표를 제작자 전용 화면에서 확인한다.

**Architecture:** 기존 Storage의 별도 mixin·추가 테이블, 도구 호출 경계의 무본문 계측, 독립 제작자 인증과 화면을 사용한다. 기존 웹 대화는 변경하지 않는다.

**Tech Stack:** FastAPI, SQLite, Jinja2, 표준 라이브러리 PBKDF2/HMAC, 기존 Playwright.

**Spec:** `specs/vxvue-usage.md`

**Preflight:** Storage·vxchat storage·Desktop _handle/dispatch·stdio serve·agent context·웹 owner cookie·cost dashboard·base template을 확인했다. 공용 인증·선택 이력 기능은 없으므로 모듈을 추가한다. 기존 대화 쿠키는 사람 인증으로 사용하지 않는다. 비밀값과 실제 기기 ID는 저장소에 넣지 않는다.

- [x] 사양을 먼저 확정하고 이벤트·업무·월 집계 저장/중복/보관 시험을 RED→GREEN으로 구현한다.
- [x] 선택 원문·서버 기기 권한·인증·CSRF·CSV 시험을 RED→GREEN으로 구현한다.
- [x] Desktop/HTTP MCP와 웹 stdio 경계에 계측을 붙이고 팀 응답 동일·소유자 목록/지침 분리를 시험한다.
- [x] 숨겨진 제작자 화면·웹 소유권 선택 연결·검색/평가/내보내기를 구현하고 합성 브라우저 경로를 검증한다.
- [x] 최종 독립 검토 수정 후 관련 시험과 전체 회귀·정리·준비 검사·HTML 재생성을 마친다.
- [ ] 문서와 명시한 코드만 커밋한다. 운영 프로비저닝·배포·push는 통합 담당자가 수행한다.

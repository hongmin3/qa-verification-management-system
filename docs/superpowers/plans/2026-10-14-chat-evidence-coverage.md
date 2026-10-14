# 챗봇 근거 누락 개선 Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task.

**Goal:** 상위 검색 결과와 텍스트 추출 한계 때문에 남긴 미확인 범위를 줄이고 필요한 근거만 읽는다.
**Architecture:** 기존 Corpus 검색과 MCP 원본 페이지 도구를 확장한다. 별도 모델·OCR 서비스·의존성을 추가하지 않는다.
**Tech Stack:** Python, SQLite FTS, PyMuPDF, FastAPI, JavaScript.
**Spec:** specs/vxvue-chatbot.md REQ-VXVUECHATBOT-001·005·007·010.

## Global Constraints
- 기본 검색 6개, 원문 4구간, batch 30,000토큰, Opus medium을 유지한다.
- 지식 원본·사용자 대화·인증·예약·별도 서비스는 보존한다. 실제 AI 사용은 허용된 운영 검증에서만 수행한다.
- 테스트 → 준비 검사 → 변경 검토 → 커밋·master push → 운영 작업 없을 때 배포 → 실제 검증.

## 기존 근거와 재사용
- corpus.py search_documents: OR 검색, limit 100, offset 없음. get_document의 페이지 검사 재사용.
- mcp_server.py: get_document_page가 이미 이미지 제공. 새 이미지 처리 서비스 불필요.
- vxvue_chatbot.yaml: 잘림 후 추가 조회 지침을 목록 질문과 원본 페이지 확인에 구체화.
- tests/test_vxvue_chat_knowledge.py·cost.py: 근거 보존과 응답 예산 회귀 재사용.

## Review Focus
- 같은 순위의 후보가 페이지 사이에서 누락·중복되지 않아야 한다.
- all 검색과 VP 전체 ID 매칭이 오탐을 만들지 않아야 한다.
- 미리보기를 근거 전문으로 인용하지 않아야 한다.
- 빈 PDF·이미지 PDF·잘못된 페이지·손상 파일을 구분해야 한다.
- 복사 버튼과 첨부·원문 다운로드는 답변 Markdown 다운로드 제거 후에도 동작해야 한다.

## Tasks
- [x] SPEC 반영 후 100개 초과 후보·순위·any/all·미리보기·잘못된 입력 시험을 RED로 확인한다.
- [x] 검색 offset/match_mode/preview를 구현하고 MCP에 공개한다. 기존 인자는 호환 유지한다.
- [x] 이미지 반환에 페이지 추출 상태를 추가하고 프롬프트에 검색 완료·명칭 대조·필요 이미지 확인 절차를 적용한다.
- [x] 관련 자동 검사와 실제 브라우저 복사·API 제거를 확인한다.
- [x] 운영 같은 질문의 답변·인용·사용량을 확인하고 검증 문서·진행 상태를 갱신한다.

## 운영 피드백 반영

첫 개선안의 남은 누락을 재현하고 원본 최대 6쪽 묶음 조회와 context 0~3 인접 구간 조회를 보완했다. 관련 57건·실제 목록 질문·표시 조건 구분 질문을 확인했다. 비용 감소는 입증되지 않아 검증 기록에 구분했다.

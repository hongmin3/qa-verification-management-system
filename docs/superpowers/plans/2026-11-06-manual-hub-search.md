# Manual Hub Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development to implement this plan task-by-task.

**Goal:** 기존 매뉴얼 검색의 조건 공유·결과 제한 표시·파일 본문 검색을 개선한다.
**Architecture:** 기존 React·FastAPI·PostgreSQL 서비스 안에서 검색과 파일별 파생 본문만 확장한다.
**Tech Stack:** React, FastAPI, SQLAlchemy, Alembic, 별도 본문 추출 프로세스와 PyMuPDF.
**Spec:** SPEC.md REQ-HUB-008, TEST-HUB-008.

## Global Constraints

- 사용자 최종 범위는 기존 기능 개선이다. 개인·공용 작업공간, 새 계정, 페이지 편집, 업무·협업 플랫폼은 추가하지 않는다.
- 기존 원본·Revision·Current·로그인·권한·GET 검색 응답 배열을 보존한다.
- 원본 파일 50MiB, 추출 8초, 본문 1,000,000자, PDF 500쪽, Office 압축 합계32MiB·512항목, XML8MiB.
- 본문 갱신은 관리자 요청 또는 정상 업로드 확정 뒤에만 실행한다. 외부 AI·네트워크·매크로 실행 없음.
- 테스트는 임시 파일·임시 DB를 쓰며 운영 PostgreSQL의 기존 fixture는 실행하지 않는다.

## Shared Interfaces

- GET /api/search: 기존 배열에 snippet, body_status, body_detail을 선택 필드로 추가한다.
- POST /api/search/reindex: cursor, limit(기본2·최대5), current_only(기본true), force(기본false)를 받고 처리한 파일·현재 상태·다음 cursor·남은 건수를 반환한다.
- StoredFile의 본문·상태·판·해시·처리 시각은 추가 열이다. migration0002는 기존 행을 보존한다.

## Review Focus

- 서식 run으로 나뉜 문장·한글 TXT 인코딩·XLSX에서 표시하지 않는 sharedStrings.
- 파일 크기·Office 압축·PDF·처리 시간·출력 크기 제한.
- 관리자·임시 비밀번호·외부 Origin·중지 뒤 재개 범위.
- URL 공유·뒤로가기·늦은 검색 응답·200건 제한 오해.
- 원본·메타데이터·API 배열·기존 판·마이그레이션 보존.

## Tasks

- [x] URL 필터·정렬·결과 제한 표시의 RED → GREEN.
- [x] 본문 추출·추가 DB 열·기존 검색·관리자 갱신의 RED → GREEN.
- [x] 본문 문맥·상태·명시적 갱신·중지/재개 화면과 타입·빌드 검증.
- [x] 독립 검토 발견의 회귀 시험과 수정.
- [x] 실제 브라우저·PostgreSQL 전체 시험·준비 검사·운영 파일/원본 보존 확인.
- [x] 관련 문서·검증 결과 정리. 저장소 완료 규칙에 따라 커밋·푸시한다.

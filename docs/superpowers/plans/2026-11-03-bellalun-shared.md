# Bellalun 공용 서버 구현 계획

> **For agentic workers:** Use executing-plans to implement this plan task-by-task.

**Goal:** Bellalun 공통 지침과 자료를 기존 인증된 QA 서버에서 모든 파트원이 조회한다.
**Architecture:** VXvue stdio/HTTP 연결·PC별 인증·Corpus·DesktopTools를 재사용한다. XIPL과 같은 불변 자료 판 방식으로 Bellalun을 분리하고 공통 Skill 원본의 Markdown만 수집한다.
**Tech Stack:** Python, FastAPI, 기존 Corpus, PowerShell 설치 안내, 정적 HTML.
**Spec:** `specs/bellalun-shared.md`

## Global Constraints

- 기존 인증·등록·회수와 네트워크 정책 유지. 원문·실제 지침·키는 Git 제외.
- 기존 로컬 Skill과 정션 및 제품 자동화 저장소 보존.
- 최근 8개 판, workflow limit 12000자, PDF 원본 페이지만 렌더링.
- 서버에서 현재 파일을 읽고 배포 전 재확인·백업, 변경 파일만 전송.

## Shared Interfaces

### Contract: Bellalun 조회

도구 접두사는 bellalun_. get_status 외에는 snapshot_id:string 필수.
문서·검색 입력은 기존 Corpus 도구 스키마. workflow는 offset:int>=0,
limit:int 1..12000을 받고 text,total_chars,truncated,next_offset을 반환.
수집 목록은 기존 data/product_knowledge/bellalun-viewer/manifest.json의 assets 형식.

## Review Focus

- 누락·변조·다른 제품 판은 오류, 이전 정상 판은 조회 가능.
- QA 지침은 마스킹 후 잘라 이어 읽고 개인 로컬 경로를 사용하지 않도록 서버 지침 우선.
- 기존 설치의 동적 도구 갱신과 신규 확장 내장 목록을 함께 확인.
- 공통 Skill 내보내기는 읽기 도중 변경·외부 링크·빈 필수 파일을 거절.
- 별도 로컬 Skill과 기존 동기화 예약을 보존.

## 사전 확인과 영향

`desktop.py`, `knowledge.py`, `shared_knowledge/service.py`, Corpus, 기존 업로드·설치 빌더,
제품 설정과 합성 RPC 테스트를 확인했다. 현재 서버 desktop.py와 master 내용은 같다.
XIPL service는 줄바꿈 바이트만 다르며 의미 차이는 없다. 서버는 Git이 없는 배포본이다.
다른 worktree의 ALM·Skill 관리 작업과 충돌하지 않도록 새 worktree를 사용한다.
도구 소비자는 설치된 Desktop bridge, HTTP MCP, 신규 다운로드 내장 목록이다.
기존 도구 이름·입력은 바꾸지 않고 Bellalun 도구와 별도 Skill만 추가한다.

## 최종 확인

구현·동기화·운영 배포와 PC 예약의 실제 1회 성공을 확인했다. 결과와 제한은 `progress.md`의 2026-11-03 Bellalun 절에 기록했다.
기존 전체 회귀의 최초 실패와 최종 관련 시험 결과를 구분한다. 공통 스킬 관리 세션의 완료 커밋을 병합해 실제 상세 안내로 바로 연결했다.

### Task 1: 서버 지침·자료 조회

- [x] 합성 RPC 회귀를 작성하고 기존 코드에서 Bellalun 도구 부재로 실패 확인.
- [x] Bellalun snapshot·workflow와 기존 연결 라우팅 추가.
- [x] 판 고정·변조·잘못된 입력·인증과 기존 VXvue/XIPL 시험 실행.

### Task 2: 동기화·안내·실서버

- [x] 공통 Markdown 내보내기 회귀 후 스크립트·분류 설정 구현.
- [x] 별도 서버 Skill ZIP·가이드와 기존 가이드 연결 작성.
- [x] 최신 공통 지침과 서버 자료를 대조하고 기존 수집기로 업로드.
- [x] cleanup·전체 pytest·readiness·diff·검토, 문서 갱신.
- [x] 운영 작업 유휴·대상 파일 해시 재확인 후 백업·선별 배포·systemd 재시작.
- [x] 외부 클라이언트 인증 RPC·지침 전문·원문·이미지·ZIP 재조회.
- [x] 최종 커밋·push.

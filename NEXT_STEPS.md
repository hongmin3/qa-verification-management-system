# Next Steps — 매뉴얼 개정 검증 기능 진행 상황 (우선순위 순)

과거 완료 작업의 상세 서사(무엇을 왜 어떻게 고쳤는지)는 git log와 각 커밋 메시지에 있다.
이 파일은 "아직 할 일"을 우선순위 순으로 추적하는 문서이므로, 완료된 항목은 날짜·한 줄
요약·커밋 해시만 남긴다(2026-09-02 압축 — 토큰 절약 목적, 상세가 필요하면
`git log --grep`/`git show <hash>`).

## 완료 이력 색인 (최신순, 상세는 커밋 참고)

- 2026-09-08: **QA Agentic Workflow 구축** — Issue 기반 검증 범위 분석(`/qa-agent`).
  제품 지식 자산 레지스트리 공통화(파일명 규약만으로 분류·리비전 판별, Bellalun Viewer가
  코드 변경 없이 편입), QA 규칙 Rev 로더(절 단위 Skill 태깅 → 85~94% 절감), Polarion Issue
  파서(실측 38건), Exact→BM25 단계 검색, Gate G1~G5(코드 판정 · 막히면 API 0회), Evidence
  Store, ID 교차검증, Human Review 6탭, QA 승인 기록, 외부 전송 마스킹, 모델 등급 라우팅.
  실측: 코퍼스 2.11MB 중 API 전송 1.37%. 테스트 535건.

- 2026-09-02: Release Scope BM25 소표본 오탐 완화(토큰 겹침 fallback) — `63fbd11`
- 2026-09-02: Word Comment 앵커링을 문단 단위→변경 요소 단위로 정밀화 — `f5c4f9f`
- 2026-09-02: `app/parsers/*` 계층 역전 해소(`app/core/document_schemas.py`) — `dee64c2`
- 2026-09-02: `deploy.ps1` 자동 pip install + `-Restart` 옵션 — `15f1d05`
- 2026-09-02: nginx HTTPS 강제 적용(self-signed) + 구주소 호환 블록 제거 — `e887bc7`,
  **이후 같은 날 HTTPS만 롤백**(구주소 블록 제거는 유지). 배포 직후 서버 loopback(`curl
  127.0.0.1`)으로만 검증해 443이 방화벽에 막혀 있는 걸 놓쳤고, `sudo ufw allow 443/tcp`로
  1차 수정까지 했으나 self-signed 인증서의 브라우저 "안전하지 않음" 경고를 사용자가
  실사용성 문제로 지적해 HTTPS 강제 자체를 되돌리기로 결정. nginx를 평문 HTTP 단일
  서버블록으로 복원, Manual Hub `SESSION_COOKIE_SECURE=false`로 원복, `ufw`의 443 규칙도
  삭제. 외부 클라이언트(개발 PC)에서 http 정상·https 접속불가(의도된 상태) 확인 완료.
  정식 CA 인증서 없이 self-signed로 다시 시도하지 않는다 — 필요해지면 사용자와 인증서
  발급 방안(정식 CA vs 사내 CA)부터 다시 정한다.
- 2026-09-02: Akela 첫 CURATE 검토 + `core-deployment.md` 갱신 — `a4fca4c`
- 2026-09-02: 구 GitHub 저장소(`qa-manual-hub`) archive 처리
- 2026-09-02: 운영 신뢰성/평가 CLI/CI/백업/PDF 개정 표시 고도화 — `7d8f854`, `611e4a9`
- 2026-09-02: 등록 문서 파싱 결과 캐시 — `c60380c`
- 2026-09-02: systemd 전환, 모니터 확장(nginx/manual_hub), cron 시간대 정정, 매뉴얼 서버
  데이터 연동(Cross-Manual 대조 소스로 활용, 실서버 활성화까지 완료)
- 2026-09-01: TC Excel 컬럼/시트 수동 매핑 UI — `4459dd3`
- 2026-09-01: 분석 이력 검색·필터·페이지네이션 — `7b38ac5`
- 2026-09-01: 매뉴얼 개정 검증 캐시 Hit 기록(비용 대시보드 공백 해소) — `af0287a`
- 2026-09-01: 실제 파일 기반 E2E pytest 편입 — `458dbd3`
- 2026-09-01: 비용/캐시 대시보드 UI(`/cost-dashboard`) — `c674185`
- 2026-09-01: Cross-Manual 영향분석, 이미지 변경 Human Review Gate, 프로젝트 명칭 통일
  (표시명 "QA 검증 관리 시스템", slug `qa-verification-management-system`)
- 2026-09-01: QA 플랫폼 허브 전환, 공용 Knowledge 모듈 분리, PDF 리비전 diff, Release
  Note/설계검토보고서 상세 근거, 매뉴얼 개정 검증 최초 구현(스켈레톤→파이프라인), Release
  Note 파서 + Reverse 검증(누락 의심)

## 아직 미착수 (2026-09-02 재정리, 우선순위 순)

각 항목의 "확인" 줄은 실서버 또는 로컬에서 직접 검증한 근거다.

### A. 운영 리스크

-1. **Gemini API 선불 크레딧이 소진됐다.** 실호출이 `429 RESOURCE_EXHAUSTED`로 실패한다
   (2026-09-08 로컬 확인). 파이프라인은 Gate 통과·검색·payload 조립·마스킹까지 정상
   수행하고 API 호출 지점에서만 막힌다. AI Studio에서 결제 상태를 확인해야 실사용이 가능하다.
-2. **`gemini-2.5-pro`가 신규 계정에 제공되지 않는다.** 실제 응답: "no longer available to
   new users, please update to gemini-3.1-pro-preview". 상위 등급 모델이 막히면 기본
   모델로 물러나고 그 사실이 결과에 남도록 처리했고, `config.yaml`의 `models.complex`
   기본값은 검증된 `gemini-2.5-flash`로 두었다. 상위 등급을 쓸지는 **사용자 결정 대기**.
-3. **지식 폴더를 서버가 볼 수 없다.** 폴더는 QA 담당자 PC에 있다. 담당자 PC에서 CLI로
   수집한 결과(`data/product_knowledge/`)를 서버로 옮기는 방식(복사 / 네트워크 마운트 /
   화면 업로드)을 정해야 한다 — **사용자 결정 대기** (`docs/POST_DEPLOY_TESTS.md` §2).

0. **HTTPS 미적용 (다시).** self-signed 인증서로 2026-09-02에 적용했다가 브라우저
   "안전하지 않음" 경고 때문에 같은 날 롤백했다(위 완료 이력 참고). 매뉴얼 서버는
   로그인·세션 기반인데 여전히 평문 HTTP다. 재적용하려면 정식 CA 인증서를 발급받거나
   사내 CA를 신뢰 저장소에 배포하는 방안이 먼저 필요하다 — 사용자 결정 대기.
1. **매뉴얼 서버 백업이 원본과 같은 디스크에 있다.** 실서버 조사 확인(2026-09-02): 물리
   디스크가 `sda` 하나뿐이고 `/`·`/home`이 같은 btrfs subvolume이다. `/srv/qa-manual-hub/
   backup`(276M)과 `storage`(58M) 모두 `/dev/sda3` 위에 있어 디스크 장애 시 함께 소실된다.
   `/mnt/vhdmaster`가 별도 마운트로 있지만 운영 보호 규칙상 접근·마운트 변경 금지 대상이라
   쓸 수 없다. **여분 디스크가 없어 이 서버만으로는 해결 불가 — 별도 볼륨/NAS 추가라는
   인프라 결정이 먼저 필요하다** (보류, 사용자 결정 대기).

### B. 제품 기능 고도화

5. **핵심 앱에 사용자 인증이 없다.** QA 승인 기록에 "누가" 승인했는지 남지 않는다. 파트원
   5명이 함께 쓰면 추적성이 필요해진다. Manual Hub의 세션 인증을 재사용하는 방법과 별도
   인증을 붙이는 방법이 있고, **평문 HTTP 상태(A-0)와 함께 결정해야 한다** — 인증을 붙이면서
   HTTPS를 미루면 자격증명이 평문으로 흐른다.
6. **QA Agent 2차 Skill 미구현.** S04(Fix Verification) · S06(Issue Writing & Closure) ·
   S08(API/WebSocket/DICOM) · S09(Generator) · S10(Release Validation). 현황은
   `/qa-agent/rules` 또는 `python scripts/rule_capability_report.py --status NOT_PLANNED`.
7. **사양 Chunk에 SRS 메타데이터가 없다.** SRS ID·Status·Parent/Child·Linked SRS를 뽑지
   않고 본문 exact 검색으로 찾는다. Polarion REST API로 SRS 워크아이템을 수집하면 채울 수
   있다(`alm-issue-export`가 이미 그 접근을 갖고 있다).
8. **Semantic Search 미구현.** exact+BM25의 Recall을 먼저 측정해야 하고, 외부 Embedding은
   이 프로젝트의 전제(원문 미전송)와 충돌한다. 로컬 임베딩이 준비되면 `Retriever` Protocol
   구현체만 추가하면 된다.

9. **`retrieval.candidate_limit=150`이 검증되지 않았다.** 실서버 표본 1건(전체 TC 6,407 →
   후보 150 → 최종 추천 3)만으로 정한 값이다. 추천 정확도 측정 루프는 이미 동작하지만
   (완료된 분석 상세 화면에서 QA 확정 TC ID/메모 적립 → precision/recall/F1 표시), QA
   확정 표본이 실제로 쌓여야 조정 근거가 생긴다.
12. **매뉴얼 서버 확장 항목.** 본문 full-text 검색(현재 PostgreSQL ILIKE → `tsvector`),
    변경 알림(Email/Teams), Revision 자동 추출, 권한 고도화. 구조는 준비돼 있고 미구현이다
    (`services/qa-manual-hub/README.md` "향후 확장").

### C. 구조 · 기술 부채

14. **핵심 앱 스키마 마이그레이션 방식.** 현재 `CREATE TABLE IF NOT EXISTS` + 컬럼 보강이다.
    `docs/SHARED_PLATFORM_ARCHITECTURE.md`가 정한 전환 시점(운영 인스턴스나 개발자 증가)에
    도달하면 Alembic으로 옮긴다. 매뉴얼 서버는 이미 Alembic을 쓴다.

### D. Akela 지식 운영

19. ~~**매뉴얼 서버 지식 31개 섹션이 아직 한 번도 applied되지 않았다.**~~ → **1차 스팟체크
    완료 (2026-09-02)**. `manual-hub-deploy` activity로 오늘 실제 서버에서 확인한 사실(systemd
    ExecStart, `.env`/`scripts/` 경로, backup.sh, deploy.sh 순서, HTTPS 설정 절차)과
    4개 지식 섹션을 대조해 전부 정확함을 확인(`server-directories`/`deploy-script`/
    `config-coupling`/`troubleshooting-checklist`, applied 기록). 그 과정에서 진짜 공백
    하나를 발견 — `manual-hub-overview`의 아키텍처 다이어그램이 **단독 배포**만 그리고
    있고, 지금 운영 서버가 실제로 쓰는 **플랫폼 통합 배포**(nginx 하나가 핵심 앱과 함께
    라우팅, `/manual-hub/` 하위 경로, HTTPS)는 문서에 전혀 없었다. 소유자 승인 후
    `manual-hub-overview`에 보완 문단 추가, `akela check` ok. 나머지 27개 섹션은 아직
    미검증 — 다음 매뉴얼 허브 작업 때 이어서 확인.
20. **CURATE 정기 검토** 1차는 2026-09-02에 완료(`deployment`/`core-development` 스코프,
    커밋 `a4fca4c`)했지만 다음 정기 실행은 아직 예약되지 않았다. 주기적 실행(주 1회 또는
    스프린트당 1회)을 원하면 `/loop` 또는 스케줄 작업으로 등록. `scope=all`+`tier=should`
    전체 스코프 검토는 아직 한 적 없다.

## 알려진 설계상 단순화 (버그 아님, 의도적 v1 범위)

- `match_release_changes`의 BM25 매칭은 토큰 겹침 fallback(2026-09-02)으로 소표본 오판을
  줄였지만, 여전히 로컬 규칙 기반 참고 신호일 뿐 확정 판정이 아니다 — 항상 "QA 확인 필요"로
  취급할 것.
- 매뉴얼 서버 Cross-Manual 연동은 제품 **이름**으로 매칭한다. 현재 겹치는 제품은
  `Bellalun Viewer` 하나뿐이고, 핵심 앱의 `VXvue`는 매뉴얼 서버에 같은 이름의 제품이 없어
  조용히 건너뛴다(오류 아님). VXvue도 대조에 쓰려면 매뉴얼 서버에 `VXvue` 제품을 만들고
  매뉴얼을 등록해야 한다.

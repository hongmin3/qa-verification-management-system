# Next Steps — QA 검증 관리 시스템 남은 작업

과거 완료 작업의 상세 서사(무엇을 왜 어떻게 고쳤는지)는 git log와 각 커밋 메시지에 있다.
이 파일은 "아직 할 일"을 우선순위 순으로 추적하는 문서이므로, 완료된 항목은 날짜·한 줄
요약·커밋 해시만 남긴다(2026-09-02 압축 — 토큰 절약 목적, 상세가 필요하면
`git log --grep`/`git show <hash>`).

## 현재 기준 (2026-10-13)

- 핵심 기능은 QA Intelligence Agent 일일·주간 점검과 매뉴얼 개정 검토이다. 단일 이슈 검증 범위 분석은 2026-10-13 사용을 종료했다. Regression 영향 분석 화면·API·추천 평가는 2026-10-01 제거됐다(`0fdf2f2`). 과거 DB·평가·분석 기록은 보존한다.
- 기본 AI 제공자는 `ai.provider: claude_cli`다. Gemini는 선택 가능한 제공자로 남아 있으며 아래의 9월 Gemini 실측·가격 항목은 그 제공자를 쓸 때 참고한다.
- 일일 점검 예약은 운영 서버에서 평일 07:30(한국 시간)에 돈다. 담당자 PC의 같은 예약은 `config.local.yaml`에서 껐다. PC 테스트 서버와 운영 핵심 앱 포트는 `24357`이다.
- 서버 DNS 복구는 이번 세션에서 확인했다. 메일은 처음 SMTP 인증정보가 없어 `530 Authentication Required`로 거절됐으나, 사용자 설정 후 인증정보 등록 상태와 `send_report`의 `sent`·1건을 확인했다. 받은편지함 수신과 새 실제 분석 완료는 별도 확인이 필요하다.
- VXvue AI 챗봇의 운영 코드·Skill 지침 업로드·네 파일 예약 실행본은 반영했다. 서버 Opus medium 성공 답변·후속 첨부·이력·브라우저 분리·캐시 토큰과 실패 비용 저장도 실제로 확인했다. 세부 결과와 현재 남은 작업은 `progress.md`를 읽는다.
- 최신 사양은 `SPEC.md`와 `specs/qa-intelligence.md`, 구현·검증·배포의 현재 진행 상태는 `progress.md`를 읽는다. 아래의 2026-09-02 운영 조사 수치는 재확인 없이 현재 수치로 쓰지 않는다.

## 완료 이력 색인 (최신순, 상세는 커밋 참고)

- 2026-09-08: **할당량 알림 · 모델 실측 판단 · 한글 파일명 처리** — Gemini 할당량 소진/모델
  사용 불가를 메일로 알림(쿨다운·마스킹·발송 실패 내성). 모델을 실측으로 결정:
  `gemini-3.5-flash` 채택, Pro 계열 배제(thinking 을 끌 수 없고 근거 없이 40건을 KEEP 으로
  단정). 마운트에서 한글 파일명이 NFD/mojibake 로 와도 자동 흡수 + fstab 생성기.

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
- 2026-09-01: 매뉴얼 개정 검토 캐시 Hit 기록(AI 사용량 공백 해소) — `af0287a`
- 2026-09-01: 실제 파일 기반 E2E pytest 편입 — `458dbd3`
- 2026-09-01: AI 사용량 UI(`/cost-dashboard`) — `c674185`
- 2026-09-01: Cross-Manual 영향분석, 이미지 변경 Human Review Gate, 프로젝트 명칭 통일
  (표시명 "QA 검증 관리 시스템", slug `qa-verification-management-system`)
- 2026-09-01: QA 플랫폼 허브 전환, 검증 자료 관리 모듈 분리, PDF 리비전 diff, Release
  Note/설계검토보고서 상세 근거, 매뉴얼 개정 검토 최초 구현(스켈레톤→파이프라인), Release
  Note 파서 + Reverse 검증(누락 의심)

## 남은 작업 (2026-10-13 현재 설명으로 갱신)

각 항목의 "확인" 줄은 실서버 또는 로컬에서 직접 검증한 근거다.

### A. 바로 해야 하는 것 (사용자 조치)

1. **운영 서버 점검 메일 수신 확인.** 처음에는 `530 Authentication Required`로 실패했다. 사용자 설정 후 인증정보 등록 상태와 `send_report`의 `sent`·1건을 확인했다.
   실제 점검 메일의 받은편지함 수신과 실행 결과 링크를 확인한다. 추가 메일 발송 시험은 전송을 승인받은 범위에서 수행한다.
2. **서버가 지식 폴더를 직접 읽게 할 때의 마운트.** 현재는 담당자 PC가 최신 문서를 서버 지식 사본으로 업로드한다(REQ-SYNC-002). 이 방식의 수집·업로드 결과는 `progress.md`에 있다. 서버가 공유 폴더를 직접 읽는 방식은 별도 운영 결정이다.
   2026-09-02 당시 대안은 읽기 전용 CIFS 마운트였다.
   `python scripts/make_knowledge_mount.py --share "//<서버>/<공유>" --app-user <계정>`이
   fstab 라인과 systemd Environment를 생성한다. 마운트 후 `ls`로 한글 파일명을 먼저 확인한다.
3. **Gemini 선택 시 사내 승인 확인.** 2026-09-08에는 결제와 Flash 호출을 확인했다. 현재 기본 제공자는 Claude CLI다. Gemini로 바꿀 때 사내 문서
   조각이 외부로 나가므로 **회사 보안 정책상 외부 생성형 AI API 사용 가능 여부**가 확인돼야
   한다. 코드가 아니라 운영 승인 문제다.
4. **Gemini 선택 시 `gemini-3.x` 단가 확인.** `config.yaml` `models.pricing`의 `gemini-3.5-flash`·
   `gemini-3.1-flash-lite` 값은 2.5-flash 기준 **잠정값**이다. 공식 표를 확인해 채운다.
   비용 추정이 그만큼 어긋난다.

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

### B. 다음 개선 (측정이 먼저인 것)

4a. **단일 이슈 분석의 후보 평가 항목 종료.** 전용 기능과 후보 한도 설정은 사용을 종료했다. 2026-09-08 실측은 당시 단일 이슈 분석에 대한 기록이며 현재 점검의 후보 한도를 정하는 근거로 쓰지 않는다.

4b. **옛 Regression 후보 평가 항목 종료.** 기능과 추천 평가 화면은 제거됐다. 현재 비용 절감은 [비용 설계](docs/COST_OPTIMIZATION.md)의 작업 묶음·검색 재사용·결과 저장본 기준으로 검토한다.

### C. 제품 기능 고도화

5. **핵심 앱에 사용자 인증이 없다.** QA 승인 기록에 "누가" 승인했는지 남지 않는다. 파트원
   5명이 함께 쓰면 추적성이 필요해진다. Manual Hub의 세션 인증을 재사용하는 방법과 별도
   인증을 붙이는 방법이 있고, **평문 HTTP 상태(A-0)와 함께 결정해야 한다** — 인증을 붙이면서
   HTTPS를 미루면 자격증명이 평문으로 흐른다.
6. **제품 규칙의 자동화 범위 확인.** 폐기된 단일 이슈 엔진의 Skill 확장 계획은 종료했다. 현재 수정 완료 이슈 분석·TC 수동 점검·초안은 QA Intelligence 엔진이 수행한다. 자동 확정 금지와 제품별 미구현 규칙의 현황은
   `/qa-agent/rules` 또는 `python scripts/rule_capability_report.py --status NOT_PLANNED`.
7. **등록 문서 조각의 메타데이터 필요성 확인.** QA 점검은 Polarion 원본 필드와 관계를 제품 설정으로 읽는다(REQ-QAINTEL-002·003). 등록 PDF의 사양 조각에 필드를 더할 필요가 있는지는 현재 매뉴얼 검증·지식 검색 사용처를 기준으로 조사한다. 폐기된 단일 이슈 엔진을 위한 확장은 진행하지 않는다.
8. **Semantic Search 미구현.** exact+BM25의 Recall을 먼저 측정해야 하고, 외부 Embedding은
   이 프로젝트의 전제(원문 미전송)와 충돌한다. 로컬 임베딩이 준비되면 `Retriever` Protocol
   구현체만 추가하면 된다.

9. **폐기 기능의 평가 기록(2026-09-02).** 옛 Regression의 `retrieval.candidate_limit=150`은 실서버 표본 1건(전체 TC 6,407 → 후보 150 → 최종 추천 3)만으로 정한 값이었다.
   당시 완료된 분석 상세 화면에서 QA 확정 TC ID·메모를 적립해 precision/recall/F1을 보였으나, 2026-10-01 기능 제거로 이 개선 항목은 현재 결정 대기에서 제외한다. 과거 평가 데이터는 보존한다.
12. **매뉴얼 서버 확장 항목.** 본문 full-text 검색(현재 PostgreSQL ILIKE → `tsvector`),
    변경 알림(Email/Teams), Revision 자동 추출, 권한 고도화. 구조는 준비돼 있고 미구현이다
    (`services/qa-manual-hub/README.md` "향후 확장").

### D. 구조 · 기술 부채

14. **핵심 앱 스키마 마이그레이션 방식.** 현재 `CREATE TABLE IF NOT EXISTS` + 컬럼 보강이다.
    `docs/SHARED_PLATFORM_ARCHITECTURE.md`가 정한 전환 시점(운영 인스턴스나 개발자 증가)에
    도달하면 Alembic으로 옮긴다. 매뉴얼 서버는 이미 Alembic을 쓴다.

### E. Akela 지식 운영

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
- 매뉴얼 서버 Cross-Manual 연동은 제품 **이름**으로 매칭한다. 2026-09-02 확인 당시 겹치는 제품은
  `Bellalun Viewer` 하나뿐이었고, 핵심 앱의 `VXvue`는 매뉴얼 서버에 같은 이름의 제품이 없어
  조용히 건너뛴다(오류 아님). VXvue도 대조에 쓰려면 매뉴얼 서버에 `VXvue` 제품을 만들고
  매뉴얼을 등록해야 한다.

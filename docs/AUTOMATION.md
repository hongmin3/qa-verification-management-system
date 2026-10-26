# 자동화 아키텍처 — 진행 상태 · 지식 동기화 · 보고서

이 문서는 2026-08-31 고도화 작업(실시간 진행 상태 UI, VXvue 사양서 자동 동기화, 보고서 재구성)의
설계와 운영 방법을 정리한다. 코드 변경 이력은 `HANDOFF.md`, 절대 안전 규칙은 `SECURITY.md`와
프로젝트 루트 `CLAUDE.md`를 따른다.

## 1. 전체 시스템 구조

```text
[Windows PC]                                   [Ubuntu 10.13.0.222:24357]
ALM 사양서 최신화 크롤링(Polarion REST API)         FastAPI 앱 (uvicorn, 일반 사용자 프로세스)
  └─ output/<날짜>/pdf/*.pdf  ──(HTTP 업로드)──▶   /knowledge/specification
       ▲                                            │
scripts/sync_vxvue_spec.py (Windows 작업 스케줄러)   ├─ BackgroundScheduler (앱 내부, 신규 systemd 없음)
지식 폴더 ──(scripts/sync_product_knowledge.py       ├─ SQLite: documents / analyses / sync_log
            --upload-to, 작업 스케줄러)──▶          │   /knowledge/product-knowledge/*
                                                     ├─ 공용 AI 호출 (Claude CLI 기본, Gemini 선택, 응답 캐시)
                                                     └─ QA Agent 점검(제품별 일일·주간 정책, 04:00, 공휴일 제외, 분리 프로세스)
                                                          → Polarion(GET) · Claude CLI · /qa-agent 대시보드
[사용자 브라우저]
  점검 지금 실행 ──POST /qa-agent/runs──▶ 분리 프로세스 ──▶ QA 점검 엔진
  매뉴얼 검증 ──POST /manual-review/revisions──▶ 작업 스레드 ──▶ 매뉴얼 검증
       ◀──SSE (/manual-review/jobs/{id}/stream)── 실제 단계·경과시간·실패 원인
```

## 2. AI 분석과 진행 상태

단일 이슈 분석의 전용 화면·API·엔진은 사용을 종료했다. 과거 DB 기록은 보존하며 대기 작업을 다시 실행하지 않는다. 현재 사양·이슈 점검은 QA Agent 점검 엔진이 맡는다.
- 매뉴얼 검증은 `/manual-review/jobs/{job_id}/stream`의 SSE로 단계·경과시간·실패 원인을 보낸다. 변경마다 quick 판정 뒤 필요한 경우에만 detail 판정을 부른다.
- 예약·수동 점검은 `app/modules/daily_qa/pipeline.py`를 사용한다. 실행 단계와 결과는 `/qa-agent` 대시보드와 실행 상세에서 본다.

## 3. VXvue 사양서 자동 동기화 구조

VXvue 최신 사양서는 별도 프로젝트 ALM-QA-Automation 의 srs-spec 앱
(`C:\Users\2024980\Documents\자동화\projects\ALM-QA-Automation\apps\srs-spec`, 통합 전 이름
`vxvue-srs-spec-automation`)이 **Polarion ALM REST API**로 자동 수집했다
(작업 스케줄러 `ALM_QA_Automation_Daily`). 지금은 이 저장소의 `QA_SRS_Collect`가 평일 10:20 에 수집한다(REQ-SYNC-003). 이 프로젝트를 재구현하지 않고 그 결과물
(`output/<날짜>/pdf/`)만 읽는다.

```text
app/modules/knowledge/vxvue_spec_sync.py   ← 실제 로직 (run, is_available_on_this_host, report_sync_log)
  ├─ scripts/sync_vxvue_spec.py   (Windows 작업 스케줄러용 CLI, 위 모듈을 그대로 호출)
  └─ app/modules/knowledge/router.py            (POST /knowledge/sync/specification, 같은 프로세스에서 직접 호출)
```

동작:
1. `config/products/vxvue.yaml`의 `specification.crawler_output_dir`에서 `output/YYYY-MM-DD/` 중
   최신 날짜 폴더를 찾는다.
2. `filename_patterns`(`(사양서) VXvue 사양서*.pdf`, `(사양서) Licence Manager SRS 사양서*.pdf`)에
   맞는 PDF만 대상으로 삼는다.
3. `data/specifications/vxvue/original/<날짜>/`에 원본을 보존 복사한다.
4. `data/specifications/vxvue/normalized/<날짜>/*.md`에 텍스트 정규화본을 만든다
   (`app/parsers/document_parser.extract_document_text` 재사용).
5. 로컬 상태 파일(`data/spec_sync_state.json`)의 (파일명, 크기, 수정시각)과 비교해 **바뀐
   파일만** 기존 `/knowledge/specification` API로 업로드한다 (신규 구현 없이 기존 등록 파이프라인 재사용).
6. 성공/실패와 무관하게 서버의 `sync_log` 테이블에 결과를 기록한다 (`POST /knowledge/sync-log`).
   실패해도 이미 등록된 사양서는 삭제하지 않는다 — 다음 분석은 마지막 정상 데이터로 계속 동작한다.

## 4. TC 저장 위치

TC는 계속 Knowledge 화면에서 수동 업로드하며 `data/testcases/`에 저장된다(기존과 동일,
변경 없음).

## 5. ALM 사양서 최신화 프로젝트 연계 방법

- 이 프로젝트의 코드/설정을 전혀 수정하지 않는다. 그 프로젝트 `AGENTS.md`가 `config/config.yaml`
  (실제 knowledge_folder 경로 포함)과 `.env`, `IMPLEMENTATION_LOG.md` 열람을 금지하므로 그 값도
  읽지 않는다.
- 대신 그 프로젝트가 이미 공개적으로 쌓아두는 `output/<YYYY-MM-DD>/pdf/`만 읽는다 — 이 폴더는
  실행할 때마다 새 날짜 폴더가 추가되며 이전 폴더도 그대로 남아 있어(예: `2026-08-20`,
  `2026-08-24`, `2026-08-31`), 별도 조치 없이도 전체 이력이 보존된다.
- 그 크롤러는 Windows 전용(Task Scheduler + `taskkill` 기반 프로세스 종료)이라 Ubuntu 서버가
  직접 실행할 수 없다. 따라서 `scripts/sync_vxvue_spec.py`는 **크롤러와 같은 Windows PC**에서
  실행하고, `--target-url`로 지정한 서버(기본값: 운영 서버)에 HTTP로 업로드한다.

## 6. 사양서 저장 위치

```text
data/specifications/vxvue/original/<YYYY-MM-DD>/     원본 PDF 그대로 보존
data/specifications/vxvue/normalized/<YYYY-MM-DD>/   텍스트 정규화본(.md)
data/specifications/<uuid>.pdf                       Knowledge 등록용 실제 분석 대상 사본 (기존 구조, 변경 없음)
```

원본은 날짜별로 계속 쌓이며 삭제하지 않는다. Knowledge에 등록된 사양서 자체도 이번 세션에
구현한 "다중 문서 관리"에 따라 새 리비전이 이전 문서를 대체하지 않고 모두 유지된다(잘못
등록한 경우에만 Knowledge 화면에서 수동 삭제 가능).

## 7. Scheduler 설정

- 신규 systemd 유닛을 만들지 않았다 — `app/core/scheduler.py`의 `BackgroundScheduler`가 기존
  uvicorn 프로세스 안에서 함께 뜬다(`app/main.py` lifespan). 다만 서버(Ubuntu)에서는 크롤러
  output 폴더에 접근할 수 없으므로 이 스케줄러는 실질적으로 트리거되어도 조용히 건너뛴다
  (`is_available_on_this_host()`가 False) — **실제 자동 실행은 아래 Windows 작업 스케줄러**가
  전담한다.
- 트리거 시각은 `config/products/vxvue.yaml`의 `sync.day_of_week`(`mon-fri`)/`sync.schedule_time`
  (`03:30`, `Asia/Seoul` 기준 — 서버 시스템 타임존이 `America/New_York`이라 명시했다).
- 2026-09-28 에 ALM 수집이 주 1회(월 07:00)에서 평일 통합 수집(09:00)으로 바뀌어, 그 40분 뒤
  평일마다 돌도록 옮겼다. 옛 시각(월 07:30)을 그대로 두면 월요일에 지난주 사양서를 올리게 된다.
  사양서가 바뀌지 않은 날은 다시 올리지 않고 미변경 건수만 기록하므로 매일 돌아도 부담이 없다.
- **2026-10-26 부터 사양서 동기화는 `QA_SRS_Collect` 작업의 두 번째 동작이다.** 담당자 PC 는 평일
  10:00 쯤 켜지므로, 수집(10:20)이 끝나면 같은 작업이 바로 `scripts\sync_vxvue_spec.py` 를 실행한다.
  옛 03:30 작업 `AIRegressionAnalyzer_VXvueSpecSync` 는 지우지 않고 끈다(`Disabled`). 설치는 관리자
  PowerShell 에서 `scripts\install_srs_schedule.ps1` 로 한다. 아래 표는 끄기 전 03:30 작업의 기록이다.

```powershell
Get-ScheduledTask -TaskName "QA_SRS_Collect" | Select-Object TaskName, State, @{n='Actions';e={$_.Actions.Arguments}}
```

  | 설정 | 값 |
  |---|---|
  | 트리거 | Weekly, 월~금 03:30 (KST) — QA Agent 점검(평일 04:00)보다 30분 먼저, 전날 09:00 수집본을 올린다. 2026-09-28 에 월 07:30 에서 09:40 으로 바꿨다. 03:30 변경은 관리자 PowerShell 에서만 된다(일반 권한은 `Access is denied`). 실제 반영 여부는 `progress.md` 에 적는다. 바꾸기 전 설정 XML 은 이 PC 의 `backups/`(Git 제외)에 있다 |
  | 실행 파일 | `...\자동화\projects\qa-verification-management-system\.venv\Scripts\python.exe` (2026-09-28 `projects/` 재배치 때 Action 경로를 갱신했다) |
  | 인자 | `scripts\sync_vxvue_spec.py` (기본 `--target-url`이 운영 서버라 생략) |
  | LogonType | `S4U` — 비밀번호를 저장하지 않고 로그인하지 않은 상태에서도 실행 (기존 ALM 크롤러 작업과 동일 패턴) |
  | StartWhenAvailable | `True` — PC가 꺼져 있거나 화면이 잠겨 있어도, 켜지는 즉시(또는 잠금 여부와 무관하게 S4U로) 가능한 가장 빠른 시점에 실행 |
  | MultipleInstances | `IgnoreNew` — 이미 실행 중이면 중복 실행 안 함(스크립트 자체 파일 락과 이중 방어) |

  재등록이 필요하면 동일한 원리로 새 스크립트를 실행하거나 `Set-ScheduledTask`로 트리거만
  바꾼다. 삭제는 `Unregister-ScheduledTask -TaskName AIRegressionAnalyzer_VXvueSpecSync`.

### 7.1 지식 폴더 업로드 (`QA_ProductKnowledge_Sync`)

서버는 담당자 PC 의 지식 폴더(`projects/vxvue/VXvue 지식파일`)를 볼 수 없다. 그래서 PC 가 폴더를
수집한 뒤 **서버가 갖고 있지 않은 파일만**(sha256 비교) 올리고, 서버가 등록까지 마친다
(`app/core/knowledge_push.py` → `app/core/knowledge_upload.py`). TC Excel, 매뉴얼, QA 규칙,
지침 프롬프트가 이 경로로 서버에 간다. QA Agent 점검(`docs/modules/daily-qa.md`)도 이 사본을 쓴다.

매일 08:00 KST에 갱신하고 실패하면 15분마다 재시도한다. 설치·실행 확인 결과는 `progress.md`에 기록한다.

| 설정 | 값 |
|---|---|
| 시간 | 매일 08:00 KST, 15분 간격 확인, PC 시작 때 놓친 일정 만회 |
| 실행 | 운영 프로젝트 `data/knowledge_retry/worker-<해시>/retry_product_knowledge.py --project-root <운영 프로젝트> --product VXvue --upload-to <서버 주소>` |
| 성공 후 | 제품·대상 서버별 완료 날짜를 저장하고 그날 추가 업로드를 멈춘다 |
| 실패·부분 실패 | 다음 재시도 시각을 저장한다. 서버는 이전 자료를 계속 사용한다 |
| LogonType | 기존 예약의 S4U 계정을 유지한다 |
| StartWhenAvailable / MultipleInstances / 제한 시간 | `True` / `IgnoreNew` / 2시간 |

관리자 PowerShell에서 `scripts/install_product_knowledge_schedule.ps1 -ProjectRoot <운영 프로젝트>`를 실행한다. `-CheckOnly`는 설치 전에 입력 경로와 서버 주소를 확인한다. 기존 작업의 XML을 백업하고 같은 작업을 바꾼다.

설치 스크립트는 수집 실행기·재시도 처리·업로드 처리·Skill 지침 내보내기 네 파일의 해시로 실행본을 만든다. 임시 worktree 경로가 예약에 남지 않으며, 코드 수정 후에는 설치 스크립트를 다시 실행해 실행본을 갱신한다. 예약이 재시도 시각보다 먼저 실행되면 `--wait-retry`가 남은 시간만 기다려 한 주기를 더 미루지 않게 한다. 서버 주소·상태·로그는 실행본 밖 운영 프로젝트 설정과 `data/knowledge_retry/`, `output/logs/product_knowledge_retry.log`에서 관리한다.

VXvue는 업로드 전에 활성 로컬 Skill의 최신 설치 캐시에서 지침·참조 MD를 읽어 같은 지식폴더의 `[Skill 지침] VXvue.md`에 내보낸다. 사용자 QA 규칙과 프롬프트 원본은 고치지 않는다. 검색 코드와 MCP 설정은 해시만 기록하며 변경은 별도 검증 후 배포한다(REQ-VXVUECHATBOT-008). 새 종류를 받을 수 있도록 서버와 운영 PC 코드를 반영한 뒤 예약 실행본을 갱신한다. 자세한 기준은 [Skill 동기화 계획](VXVUE_SKILL_SYNC_PLAN.md)에 있다.

기존 평일 10:20 수집·사양 동기화(`QA_SRS_Collect`)는 유지한다. 그 결과가 서버에 등록되면 다음 챗봇 질문에 반영된다. 동일한 원본이 두 경로로 등록돼도 챗봇 지식 판은 같은 바이트의 자료를 한 번만 검색한다.

### 7.2 하루 실행 순서 (평일)

```flow
04:00 서버 QA Agent 점검(제품마다, Polarion 직접 조회 + 전날까지 올라온 지식 사본, 공휴일 건너뜀) -> 요약 메일·/qa-agent 대시보드
08:00 PC 지식 폴더 업로드 -> 실패 시 15분마다 재시도 -> 서버 지식 사본
10:20 PC 통합 SRS 수집·PDF 배포(QA_SRS_Collect) -> 같은 작업에서 바로 사양서 동기화 -> 서버 신규 사양서 등록 -> 다음 날 04:00 점검이 사용
11:00 PC ALM 이슈 수집(ALM_QA_Automation_Daily)
```

`QA_SRS_Collect`는 평일 10:20 과 로그온 15분 뒤에 돈다. 부팅 직후 만회 실행은 도메인 로그온에 실패할 수 있어(2026-10-26 10:01, 0x8007051F) 로그온 트리거를 함께 둔다. 같은 날 완료본은 다시 수집하지 않는다. 시작에 실패하면(PC 재시작 중 등) 10분 뒤 최대 3번 다시 시도한다. 설치 스크립트(`scripts/install_srs_schedule.ps1`)가 이 설정을 넣으며 관리자 PowerShell에서만 바뀐다. 동기화가 고른 수집본 다음 날부터 오늘까지 빠진 평일이 있으면 `PARTIAL`로 보고해 Knowledge 화면 사양서 구역이 `주의`가 된다(REQ-SYNC-001).

서버의 QA Agent 점검은 Polarion 을 스스로 읽으므로 PC 가 꺼져 있어도 돈다. 공휴일(`config/holidays/kr.yaml`)에는 돌지 않는다. 일일 모드는 Claude 한도(세션·주간)나 사용률 기준에 걸려 멈추면 초기화 5분 뒤 자동으로 이어 하고(REQ-QAINTEL-049) 주간 모드는 다음 주 첫 업무일 또는 수동 실행에서 이어 간다. 자동 점검 중지나 서버 예약 비활성화는 모든 자동 실행을 막는다. PC 쪽 작업이 멈추면 서버의
TC·매뉴얼·규칙 사본이 낡을 뿐이고, 그 시각은 `/knowledge` 화면에 보인다.

## 8. 수동 동기화 방법

통합 수집기는 `scripts/collect_srs_spec.py`로 실행한다. 문서 구성은 `config/srs-crawler.yaml`, 인증은 기존 비밀 설정, 최종 배포 폴더는 제품 설정의 `knowledge_source.dir`에서 읽는다. 다른 저장소의 실행 파일·설정에 의존하지 않는다. `requirements-srs.txt`와 Playwright Chromium은 수집 PC에만 설치한다.

`--check`와 `--dry-run`은 설정을 확인하며 수집·배포하지 않는다. `--force`는 오늘 자료와 이미지도 다시 받는다. `--from-snapshot YYYY-MM-DD`는 저장된 그 날짜의 SRS를 PDF로 다시 만든다. `--no-publish`는 검사할 PDF만 만들고 지식 폴더·공개 완료본을 바꾸지 않는다.

원본 SRS는 `data/srs_crawler/snapshots/`, 진행 중 산출물·실패 기록은 `output/srs_crawler/.runs/`, 완료 PDF는 `output/srs_crawler/YYYY-MM-DD/pdf/`에 남긴다. 성공 manifest·최종 PDF 해시를 확인한 날짜만 동기화한다. 배포 위치에서 읽기·페이지·해시 검사에 실패하면 이전 파일을 복구한다. 기존 사양서 보관본은 지식 폴더 옆 `ORG/`에 남기며 자동 삭제하지 않는다.

PC 작업 전환 시 기존 작업 XML을 백업한다. 실제 수집·배포·서버 읽기를 확인한 뒤 옛 ALM 전체 예약을 이슈 Export 전용으로 바꾼다. 이슈 Export 자료는 별도 보관 자료로 남는다. 현재 QA 점검은 Polarion을 직접 읽으며 폐기된 단일 이슈 화면에서 Export 자료를 소비하지 않는다. SRS는 통합 수집기, 분석·메일은 QA Agent 점검 엔진이 맡는다. 최근 실행 결과와 로그인 조건 등 설치 상태는 `progress.md`를 확인한다.

- 웹 UI의 "사양서 지금 동기화" 버튼은 제거했다 — 실제 자동 실행이 Windows 작업 스케줄러로
  이관되어 서버 화면에서 누르면 항상 "이 서버에서는 접근할 수 없다"는 안내만 나오는 상태가
  혼란을 줬기 때문이다(2026-08-31 사용자 피드백). `/knowledge` 페이지에는 마지막 동기화
  시각/상태/상세만 표시되고, 즉시 실행이 필요하면 아래 CLI를 크롤러가 있는 Windows PC에서
  직접 실행한다. 수동 트리거용 백엔드 엔드포인트(`POST /knowledge/sync/specification`)
  자체는 남겨뒀다(스크립트나 다른 자동화가 재사용할 수 있도록).
- CLI: `\.venv\Scripts\python.exe scripts\sync_vxvue_spec.py --target-url http://10.13.0.222:24357`
  (`--dry-run`으로 실제 등록 없이 변경분만 미리 확인 가능). 평일 10:20 수집 뒤에는 `QA_SRS_Collect`
  작업이 이 스크립트를 자동 실행한다(기본 대상이 운영 서버이므로 `--target-url`
  생략).
- 같은 문서의 이전 리비전(파일명에서 `(YYMMDD)` 날짜 부분만 다른 동일 문서, 예:
  `VXvue 사양서2(260824).pdf` → `VXvue 사양서2(260831).pdf`)이 Knowledge에 남아 있으면, 신규
  파일 업로드 성공 직후 자동으로 삭제된다(`app/modules/knowledge/vxvue_spec_sync.py::_replace_stale_revisions`).
  삭제는 신규 등록이 성공한 뒤에만 실행되므로 업로드가 실패하면 기존 리비전은 그대로 남는다.

## 9. 로그 위치

- 분석 파이프라인 전체(입력 분석/변경 추출/사양 조회/TC 검색/AI 호출/검증/보고서 생성/오류):
  `output/logs/app.log` (`app/core/logger.py`, 기존과 동일).
- 사양서 동기화 전용: `output/logs/sync_vxvue_spec.log`.
- 각 분석은 `analysis_id`(예: `regression-5f3c52aa6984`)로 시작부터 완료/실패까지 로그에서
  추적 가능하다 (`analysis_started`/`analysis_stage`/`analysis_finished`/`analysis_failed`).

## 10. 장애 발생 시 확인 방법

1. `/analyses/{id}/stream` 또는 `/analyses/{id}`에서 `status`/`stage`/`error` 확인.
2. `output/logs/app.log`에서 같은 `analysis_id`로 검색.
3. 사양서 동기화 실패는 `/knowledge` 페이지의 동기화 상태 카드(`상세` 텍스트) 또는
   `output/logs/sync_vxvue_spec.log` 확인 — 실패해도 마지막으로 정상 등록된 사양서는 그대로
   남아 있으므로 분석 자체는 계속 가능하다.
4. Gemini API 자체 장애(예: `503 UNAVAILABLE`)는 SDK가 그대로 예외를 올리며, 현재 `@retry`는
   `TimeoutError`/`ConnectionError`만 재시도한다 — 일시적 과부하는 사용자가 재실행하면 된다
   (이번 문서화 작업 중 실제로 한 번 재현되어 확인함).

기존 예약 전환은 관리자 PowerShell에서 `./scripts/install_srs_schedule.ps1`로 실행한다. `-CheckOnly`는 설정과 변경할 실행 경로만 보여 준다. 전환은 기존 예약 XML을 백업한 뒤 ALM 예약을 이슈 수집만 하도록 바꾸고, 사양서 동기화 경로와 새 수집 예약을 연결한다. 새 수집 예약은 S4U로 저장한다. 설치 및 실행 확인 결과는 `progress.md`를 본다.

## 11. 환경변수 / 설정값 목록 (이번 변경분)

| 위치 | 키 | 설명 |
|---|---|---|
| `config.yaml` | `analysis.daily_token_limit` | 일일 Gemini 토큰 한도 (기존 기능) |
| `config/products/vxvue.yaml` | `specification.crawler_output_dir` | 통합 SRS 수집 output 폴더 경로 |
| `config/products/vxvue.yaml` | `specification.filename_patterns` | 사양서로 인식할 파일명 패턴 |
| `config/products/vxvue.yaml` | `sync.schedule_time` | 예약 동기화 시각 (HH:MM, Asia/Seoul) |
| CLI 인자 | `--target-url` | `scripts/sync_vxvue_spec.py`가 업로드할 서버 주소 |

## 12. 향후 다른 제품으로 확장하는 방법

1. `config/products/<product>.yaml`을 새로 만든다(`vxvue.yaml` 구조 그대로 복사).
2. 그 제품의 사양서 출처가 다르면 `specification.source`를 바꾸고(예: 다른 크롤러, 다른
   폴더), `app/modules/knowledge/`에 그 출처 전용 모듈을 하나 추가한다(`vxvue_spec.py`와 같은 패턴 —
   `run()`/`is_available_on_this_host()`/`report_sync_log()` 인터페이스만 맞추면 됨).
3. `app/core/scheduler.py`의 `start_scheduler()`에 그 제품의 sync job을 추가한다.
4. `app/modules/knowledge/router.py`의 `/knowledge/sync/{kind}` 계열 엔드포인트와 `/knowledge` 페이지의
   동기화 상태 카드는 제품명을 매개변수화하면 재사용 가능하다(현재는 VXvue 하드코딩 — 확장
   시 가장 먼저 손볼 지점).

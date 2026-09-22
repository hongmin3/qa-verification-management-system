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
                                                     ├─ SQLite: documents / analyses / sync_log
                                                     └─ Gemini API (분석 1건당 호출 1회, 캐시)
[사용자 브라우저]
  분석 화면 ──POST /analyses──▶ BackgroundTasks ──▶ RegressionAnalyzer._execute (8단계)
       ◀──SSE (/analyses/{id}/stream)── 실제 단계·경과시간·실패 원인
```

## 2. AI 분석 Flow (8단계, 가짜 % 없음)

`app/modules/impact_analyzer/regression_analyzer.py::RegressionAnalyzer._execute`가 아래 8단계를 순서대로
지나가며, 각 단계 시작 시점에 `Storage.update_stage(job_id, index, name)`로 SQLite `analyses`
테이블에 실제 진행 상태를 기록한다. 프론트엔드는 `stage_index/stage_total`로 퍼센트를 **계산**할
뿐, AI 응답 여부와 무관하게 임의로 증가시키지 않는다.

1. 입력 문서 분석 — 변경 문서(여러 개 첨부 가능) 텍스트 추출·병합. 사용자 요청 사항(notes)이
   있으면 BM25로 요청과 관련 높은 줄만 추려 이후 단계에 넘기는 RAG 토큰 절약 적용
   (`trim_by_relevance`, `retrieval.change_text_top_lines`로 상한 조절, 문서가 짧으면 생략)
2. 변경사항 추출 — 기준 사양서 diff + 사용자 요청 텍스트 반영
3. 최신 사양서 조회 — BM25로 관련 사양 Chunk 검색
4. TC 후보 검색 — BM25로 관련 TC 후보 축소
5. AI 영향도 분석 — Gemini Structured Output 1회 호출 (`change_items`/`decisions`/`draft_test_cases` 동시 반환)
6. Regression TC 선정 — TC ID/Chunk ID 교차검증, 사람이 읽는 `specification_reference` 조립
7. 신규 TC 초안 검증 — 근거 Chunk ID 재검증
8. HTML 결과 생성 — `report.html` 렌더링, XLSX/TC 초안 저장

진행 상태는 `GET /analyses/{job_id}/stream`(SSE, `text/event-stream`)으로 push되며, 기존
`GET /analyses/{job_id}`(단발성 JSON)도 하위 호환을 위해 그대로 유지된다. 실패 시 `stage`
필드에 실패 당시 단계명이, `error`에 실제 예외 메시지가 남는다.

## 3. VXvue 사양서 자동 동기화 구조

VXvue 최신 사양서는 별도 프로젝트
(`C:\Users\2024980\Documents\자동화\vxvue-srs-spec-automation`)가 **Polarion ALM REST API**로
이미 매주 자동 수집하고 있었다. 이 프로젝트를 재구현하지 않고 그 결과물(`output/<날짜>/pdf/`)만
읽는다.

```text
app/modules/impact_analyzer/vxvue_spec_sync.py   ← 실제 로직 (run, is_available_on_this_host, report_sync_log)
  ├─ scripts/sync_vxvue_spec.py   (Windows 작업 스케줄러용 CLI, 위 모듈을 그대로 호출)
  └─ app/modules/impact_analyzer/router.py            (POST /knowledge/sync/specification, 같은 프로세스에서 직접 호출)
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
- 트리거 시각은 `config/products/vxvue.yaml`의 `sync.day_of_week`(`mon`)/`sync.schedule_time`
  (`07:30`, `Asia/Seoul` 기준 — 서버 시스템 타임존이 `America/New_York`이라 명시했다). 매일이
  아니라 **매주 월요일**로 바꾼 이유는 ALM 크롤러 자체가 매주 월요일에만 새 사양서를 수집하기
  때문이며(그 외 요일엔 어차피 변경분이 없음), 07:30은 같은 PC의 크롤러 작업(07:00)이 끝날
  시간을 30분 확보하기 위함이다.
- **Windows 작업 스케줄러에 실제 등록 완료**(`Register-ScheduledTask`로 생성, 예시 아님):

```powershell
Get-ScheduledTask -TaskName "AIRegressionAnalyzer_VXvueSpecSync" | Format-List TaskName, State
# TaskName : AIRegressionAnalyzer_VXvueSpecSync
# State    : Ready
```

  | 설정 | 값 |
  |---|---|
  | 트리거 | Weekly, Monday 07:30 (KST) — 기존 `VXvue_SRS_Spec_Automation`(월 07:00)보다 정확히 30분 뒤 |
  | 실행 파일 | `...\qa-verification-management-system\.venv\Scripts\python.exe` (2026-09-01 폴더 rename 후 `Set-ScheduledTask`로 Action 경로 갱신 필요 — `HANDOFF.md` 참고) |
  | 인자 | `scripts\sync_vxvue_spec.py` (기본 `--target-url`이 운영 서버라 생략) |
  | LogonType | `S4U` — 비밀번호를 저장하지 않고 로그인하지 않은 상태에서도 실행 (기존 ALM 크롤러 작업과 동일 패턴) |
  | StartWhenAvailable | `True` — PC가 꺼져 있거나 화면이 잠겨 있어도, 켜지는 즉시(또는 잠금 여부와 무관하게 S4U로) 가능한 가장 빠른 시점에 실행 |
  | MultipleInstances | `IgnoreNew` — 이미 실행 중이면 중복 실행 안 함(스크립트 자체 파일 락과 이중 방어) |

  재등록이 필요하면 동일한 원리로 새 스크립트를 실행하거나 `Set-ScheduledTask`로 트리거만
  바꾼다. 삭제는 `Unregister-ScheduledTask -TaskName AIRegressionAnalyzer_VXvueSpecSync`.

## 8. 수동 동기화 방법

- 웹 UI의 "사양서 지금 동기화" 버튼은 제거했다 — 실제 자동 실행이 Windows 작업 스케줄러로
  이관되어 서버 화면에서 누르면 항상 "이 서버에서는 접근할 수 없다"는 안내만 나오는 상태가
  혼란을 줬기 때문이다(2026-08-31 사용자 피드백). `/knowledge` 페이지에는 마지막 동기화
  시각/상태/상세만 표시되고, 즉시 실행이 필요하면 아래 CLI를 크롤러가 있는 Windows PC에서
  직접 실행한다. 수동 트리거용 백엔드 엔드포인트(`POST /knowledge/sync/specification`)
  자체는 남겨뒀다(스크립트나 다른 자동화가 재사용할 수 있도록).
- CLI: `\.venv\Scripts\python.exe scripts\sync_vxvue_spec.py --target-url http://10.13.0.222:24357`
  (`--dry-run`으로 실제 등록 없이 변경분만 미리 확인 가능). 매주 월요일 07:30에는 위 작업
  스케줄러가 인자 없이 이 스크립트를 자동 실행한다(기본 대상이 운영 서버이므로 `--target-url`
  생략).
- 같은 문서의 이전 리비전(파일명에서 `(YYMMDD)` 날짜 부분만 다른 동일 문서, 예:
  `VXvue 사양서2(260824).pdf` → `VXvue 사양서2(260831).pdf`)이 Knowledge에 남아 있으면, 신규
  파일 업로드 성공 직후 자동으로 삭제된다(`app/modules/impact_analyzer/vxvue_spec_sync.py::_replace_stale_revisions`).
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

## 11. 환경변수 / 설정값 목록 (이번 변경분)

| 위치 | 키 | 설명 |
|---|---|---|
| `config.yaml` | `analysis.daily_token_limit` | 일일 Gemini 토큰 한도 (기존 기능) |
| `config/products/vxvue.yaml` | `specification.crawler_output_dir` | ALM 크롤러 output 폴더 경로 |
| `config/products/vxvue.yaml` | `specification.filename_patterns` | 사양서로 인식할 파일명 패턴 |
| `config/products/vxvue.yaml` | `sync.schedule_time` | 예약 동기화 시각 (HH:MM, Asia/Seoul) |
| CLI 인자 | `--target-url` | `scripts/sync_vxvue_spec.py`가 업로드할 서버 주소 |

## 12. 향후 다른 제품으로 확장하는 방법

1. `config/products/<product>.yaml`을 새로 만든다(`vxvue.yaml` 구조 그대로 복사).
2. 그 제품의 사양서 출처가 다르면 `specification.source`를 바꾸고(예: 다른 크롤러, 다른
   폴더), `app/modules/impact_analyzer/`에 그 출처 전용 모듈을 하나 추가한다(`vxvue_spec.py`와 같은 패턴 —
   `run()`/`is_available_on_this_host()`/`report_sync_log()` 인터페이스만 맞추면 됨).
3. `app/core/scheduler.py`의 `start_scheduler()`에 그 제품의 sync job을 추가한다.
4. `app/modules/impact_analyzer/router.py`의 `/knowledge/sync/{kind}` 계열 엔드포인트와 `/knowledge` 페이지의
   동기화 상태 카드는 제품명을 매개변수화하면 재사용 가능하다(현재는 VXvue 하드코딩 — 확장
   시 가장 먼저 손볼 지점).

# 진행 상태

## 2026-10-01 오후 Knowledge 현황판 재설계 · 깨진 SRS 날짜 되살리기 · Polarion 연결

- 완료: 공용 Knowledge 재설계(REQ-KNOW-001~005·017~020). 인계 문서 `2026-09-30-knowledge-dashboard-redesign.md` 4절 1~8번을 끝냈다. 상태 판정은 `app/core/knowledge_status.py`, 안전 교체 등록은 `app/core/knowledge_registry.py` 다. 화면은 현황판, 제품 상세, 여섯 부분 사용법이다.
- 검증: 새 테스트 23개(`tests/test_knowledge_profiles.py`, 가짜 제품 포함)와 전체 pytest 1177 passed · 1 skipped. 제품 이름 분기 검사와 "더 최신 판 거절"은 일부러 코드를 깨면 실패하는 것을 확인했다. 12000 포트 서버에서 `/knowledge`, `/knowledge/products/vxvue`, `/knowledge/products/bellalun-viewer`, `/knowledge/guide` 가 390·1920px 에서 가로 스크롤이 없고 콘솔 오류도 없다. 실제 파일 교체는 이 PC 의 DB 를 바꾸므로 자동 테스트로만 확인했다.
- 이 PC 의 현황판 결과: Bellalun Viewer 는 `오류`, VXvue 사양서는 `수집 실패` 로 나온다. 판정 오류가 아니다. 이 PC DB 의 등록이 이동 전 폴더(`…\자동화\qa-verification-management-system\…`)를 가리켜 파일이 없다. 오늘 09:40 의 로컬 대상 사양서 동기화도 연결 거부로 실패했다(이어서 돈 서버 대상 동기화는 6건 성공). `고급 정보` 의 `죽은 등록 정리` 로 정리할 수 있다(사용자 결정).
- 완료: 깨진 srs-spec 날짜 2개를 되살려 가져왔다(REQ-QAINTEL-029). 09-07 은 437건 중 10개 파일, 09-21 은 3개 파일이다. 꼬리 글자가 앞 JSON 의 끝과 같을 때만 되살린다.
- 완료: Polarion 연결. 토큰은 이미 사용자 환경변수 `POLARION_TOKEN` 에 있었고 주소가 빠져 있었다. ALM-QA-Automation `apps/srs-spec/config/config.yaml` 의 주소를 `secrets.txt` 의 `POLARION_HOST` 로 옮겼다. `--check` 의 Polarion 설정이 `[OK]`, `--dry-run` 이 SRS·이슈를 모두 읽었다.
- 대기: 대기 분석 72건. 11:06 실행이 Claude 세션(5시간) 한도에 걸려 13:00 이후 다시 돌린다.
- 다음: 이슈 기록이 없는 기간의 "현재 상태 기준 이슈 정합성 점검"(사용자 결정: 전체 범위, 버튼 실행, 상한 없음과 토큰 절약 설계). SPEC 부터 쓴다.

## 2026-10-01 과거 SRS 가져오기 · 기간 실행 · Claude 로그인 (사용자 요청으로 Knowledge 재설계보다 먼저)

- 완료: ALM-QA-Automation `srs-spec` 스냅샷 12개 날짜 가운데 10개를 이 PC 에 가져왔다(`data/daily_qa/snapshots/vxvue/srs/`, 날짜마다 437건). 2026-09-07·09-21 은 srs-spec 이 같은 날 두 번 동시에 돌아(정기 작업 + 부팅 만회 작업) JSON 13개 끝에 이전 쓰기의 꼬리가 붙어 깨졌다. 그래서 건너뛴다. srs-spec 은 09-21 커밋 `65f79cb` 에서 실행 잠금을 넣었고 그 뒤로는 깨진 날이 없다. 깨진 파일도 앞부분의 온전한 JSON 으로 되살릴 수 있다(사용자 결정 대기).
- 확인: 새 엔진 본문 변환(링크 `번호 - 제목` 풀기)이 srs-spec 본문과 정상 10개 날짜에서 날짜마다 437건 중 435건 같다. 다른 2건은 srs-spec 이 이미지 받기에 실패한 곳이다. 고치기 전에는 서버 첫 실행이 SRS 181건을 잘못 "본문 변경"으로 잡을 상태였다.
- 확인: 12000 포트 서버에서 08-01~08-10 은 400 "가장 오래된 스냅샷: 2026-08-20", 08-31~09-22 는 202 와 "이슈 스냅샷이 없어 SRS 변경만 분석" 경고. 실행 `20261001-093656-vxvue` 는 SRS 변경 72건을 찾아 대기 이벤트로 남겼다(그때는 토큰 없음으로 AI 건너뜀).
- 완료: 토큰이 없으면 Claude CLI 로그인을 쓴다. `--check` 에 `Claude 인증 — CLI 로그인(claude.ai)`. 대기 72건은 다음 실행(버튼 또는 예약)에서 로그인으로 분석된다. 약 15개 작업이며 실행당 상한 10개라 두 번에 나뉜다.
- 남은 일: 이슈 과거 기록은 원본이 없어 가져올 수 없다(issue-export 는 요청한 이슈의 현재 상태만). 서버에서 쓰려면 가져온 SRS 스냅샷을 서버로 옮겨야 한다(서버 작업, 사용자 확인 필요). Knowledge 재설계는 아래 항목대로 이어서 한다.

## 2026-10-01 QA Intelligence Agent 개편 마무리 (중단된 세션 이어받음)

- 이어받은 범위: 인계 문서 `2026-09-30-qa-intelligence-agent.md` 8절. 1~3번(종류 열, xfail 3건, TEST-QAINTEL-013)과 문서·SPEC 5.4절 정리는 앞 세션들이 끝내 두었다. 이 세션은 코드 리뷰 지적과 사용자 결정을 반영했다.
- 사용자 결정(앞 세션에서 받음): 상태 이름 `확인 필요` → `주의`, 전체 pytest 를 `botyard.json` verify 로 등록, 수집이 모두 성공해야 `NO_CHANGE`, 기간 실행·이벤트 세부 규칙 9가지 추천안.
- 리뷰 반영: 지난 날 기간 실행이 대기 이벤트를 포기하던 문제(Critical), `run_file` 경로 탈출, 이벤트 하나의 두 분석, 기간 실행 중복 판정, 같은 전이의 재발, 답변 제품 필터, catch-up 유실, 인증 오탐, 멈춘 실행의 토큰, 매뉴얼 점검 작업 상한, 비용 대시보드의 `PARTIAL` 집계.
- 고치지 않은 리뷰 항목: 시간 초과 뒤 재시도는 SPEC.md REQ-DAILY-016 표("제한 시간 초과 → 한 번 더")가 정한 동작이라 그대로 둔다. 상세 화면이 첫 제품 설정으로 저장소를 여는 점(`dash.choose("")`)은 DB·출력 폴더가 제품 공통이라 지금은 맞다.
- 검증: 새 재현 테스트 `tests/test_qa_intel_review_fixes.py` 20개 통과. 핵심 가드 4개와 스냅샷 댓글 대체·경로 탈출 가드를 지워 테스트가 실패하는 것을 확인하고 되돌렸다. `run_daily_qa.py --check` 종료 코드 2(Polarion·토큰 없음 `[주의]`), `--dry-run --no-email` 은 `PARTIAL`·종료 코드 1·Claude 0회. 12000 포트 서버에서 `/qa-agent`·기간 조회·실행 상세·`/knowledge`·`/cost-dashboard` 200, `/daily-qa` 307, 경로 탈출 404, 1920·390px 가로 스크롤 없음.
- 남은 일: Knowledge 재설계(인계 문서 `2026-09-30-knowledge-dashboard-redesign.md` 4절). SPEC 의 REQ-KNOW-001~005·012·017 은 이미 개정됐고 코드는 아직 옛 화면이다. REQ-KNOW-018~020 은 `draft` 다.

## 2026-09-30 저녁 Manual Hub 포트 24358 · 이슈 수집 제안 · 지식 검토 준비

- 완료: Manual Hub 백엔드 포트를 `9180` → `24358` 로 바꿨다(저장소만). 서버 `ss -ltn` 에서 비어 있고 임시 포트 범위(32768~60999) 밖인 번호다. 다섯 곳의 포트가 어긋나면 `tests/test_serve_bind.py` 가 실패한다(파일 하나를 옛 포트로 되돌려 실패하는 것 확인).
- 서버 미적용: 운영 서버는 아직 `9180` 이다. 전환 절차와 되돌리기는 `docs/local/OPERATIONS_LOCAL.md` 에 적었다. 다음 배포 때 핵심 앱(Claude CLI 전환)과 함께 한다.
- 결정 기록: TC 옛 번호 대응표는 만들지 않는다. VP 번호만으로 맞추는 것은 보류했다. 실측으로 TC 없음이 214건에서 266건으로 늘어난다(`OPEN_QUESTIONS.md` 5절).
- 제안 기록: 새 이슈 조회식 대신 이슈 스냅샷 비교로 신규·업데이트를 나누는 안(`OPEN_QUESTIONS.md` 5절). 구현은 전면 개편 때 한다.
- 지식 검토(`akela/CURATE.md`): `akela stats` 로 대기열을 만들고 검토표를 사용자에게 올렸다. 승인 전이라 `knowledge/` 는 고치지 않았다.

## 2026-09-30 오후 화면 기능 AI 를 Claude CLI 로 전환 (REQ-AICALL-005) · 전면 개편 전 정리

- 완료: `ai.provider`(기본 `claude_cli`)로 Regression 분석·QA Agent·매뉴얼 개정 검증의 AI 를 고른다. 도구를 주지 않는 격리 호출(`app/core/claude_cli.py`), 제공자별 모델 등급, Claude 알림 종류, 상태 표시줄을 만들었다. Gemini 경로는 설정으로 계속 쓸 수 있다.
- 검증(자동): 새 테스트 18개(`tests/test_claude_cli_provider.py`)와 전체 pytest 를 돌렸다. 기본 제공자가 바뀌어 Gemini 전용 동작을 보는 테스트 5개는 제공자를 `gemini` 로 고정하거나 기대값을 고쳤다.
- 검증(이 PC, 실제 Claude CLI 2.1.285, Team 계정 로그인):
  - 격리 호출: 도구를 모두 끈 상태에서도 `structured_output` 이 온다. `claude-opus-5-5` 가 통한다. 입력 토큰 1,321개로 전역 `CLAUDE.md` 는 섞이지 않았다. Claude Code 가 넣는 계정 이메일 한 줄은 보였다.
  - Regression 분석(API, 메모만 입력): 약 35초, 토큰 69,655, TC 판정 31건. 메모(400→800%)와 TC(최대 3200%)의 차이를 근거로 확인 요청을 붙였다.
  - QA Agent(VP-6669): 호출 1회, 약 70초, 토큰 32,054, 관문 G1~G5 판정까지 정상.
  - 매뉴얼 개정 검증 2단계 판정(합성 변경 2건): 사양과 다른 배율은 `MODIFICATION_REQUIRED` 0.93, 맞는 문구는 `PASS` 로 상세 판정을 건너뛰어 호출 3회.
- 확인 못 한 것:
  1. 일일 QA 점검을 실제 Claude CLI 로 끝까지 돌리는 검증. 이 PC 에 Polarion 접속 정보가 없다. 가짜 Polarion 자료로 파이프라인을 돌리는 스크립트는 만들었지만 사용자 요청으로 여기서 멈췄다.
  2. 일일 QA 점검의 작업 폴더 밖 읽기 거부, `claude_logs/` 도구 기록 형식(앞 항목과 같음).
  3. 서버 배포. 서버는 아직 Gemini 경로의 옛 코드다. 배포 전에 서버에 Claude CLI 설치와 `CLAUDE_CODE_OAUTH_TOKEN` 이 필요하다(`docs/modules/daily-qa.md` 서버 설치 1~2단계).
- 참고: 이 PC 의 Claude 계정은 13:50 까지 세션 한도에 걸려 있었다. 한도에 닿으면 세 화면 기능이 모두 실패하므로, 운영에서 자주 닿으면 `ai.claude.models.*` 를 `claude-sonnet-5-5` 로 낮춘다.
- 다음: 사용자가 이 자동화의 전면 개편을 예고했다. 개편 전 기준점으로 이 상태를 커밋한다.
- 저장소: 사용자 요청으로 GitHub 저장소를 비공개(PRIVATE)로 바꾸고 `docs/local/` 운영 메모 2개를 넣었다. 비밀값·사내 문서 사본·DB(`data/` 977MB)·실행 결과는 사용자 선택에 따라 올리지 않았다.
- 지식 반증: `REF-core-documentation#local-only-docs`("이 저장소는 공개된다"), `REF-workflow#execution-flow`, `REF-core-ai-integration#known-issues`. `akela/CURATE.md` 검토가 필요하다.

## 2026-09-30 HTML 사양서 가로 넘침 개선

- 키트 렌더러 v7과 관리 도구를 적용했다. 본문 최대 폭을 1800px로 넓히고 표·경로·명령을 줄바꿈한다. 작은 화면의 넓은 표는 항목 이름이 붙은 세로 목록으로 표시한다. SPEC 본문·ID·추적성은 유지했다.
- 검증: 렌더러 49개, migration 22개 검사 통과. Chrome에서 390·768·1024·1366·1920px 화면의 카드 392개와 표·코드·카드 정보 가로 넘침 0건을 확인했다. 데스크톱·모바일 스크린샷도 확인했다. 프로젝트 준비 검사 오류 0이며 기존 경고 2종은 남아 있다.
- 목차 검색과 요구사항 이동도 브라우저에서 확인했다. 핵심 앱 코드와 실행 동작은 바뀌지 않아 pytest를 반복 실행하지 않았다.

## 2026-09-30 Claude 중단 작업 재개: 사양서 읽기 정리와 하루 일정

- SPEC 4절에 자동화 일정·결과·알림·실패 시 영향을 추가했다. 5.1~5.6절 초안을 회수해 합쳤다. 미완성 QA Manual Hub 절은 원본의 남은 내용을 보존해 마무리했다.
- 요구사항·테스트 ID 392개와 순서, 카드별 코드·숫자·오류 문구·참조 ID·기존 흐름도, 11절 테스트 사양과 12절 추적성 보존을 확인했다. 13.1·13.6의 번호와 결정 기록도 유지했다.
- 이 세션에서 PC 작업 스케줄러의 시작 시각을 다시 확인했다: ALM 09:00, 사양서 동기화 09:40, 지식 업로드 10:00, Redmine 10:00. 예약 등록 상태이며 실제 성공·메일 수신을 뜻하지 않는다.
- 이전 Claude 세션의 운영 확인 기록: 서버 일일 QA 점검은 미설치(`/daily-qa` 404), Redmine 알림 설정은 꺼짐. 이번 세션에서 서버 설치·백업·메일 전송 상태는 다시 확인하지 않았다.
- 검증: 내용 보존 검사 통과, HTML 재생성, HTML 내부 링크·중복 ID·스크립트 문법 검사, 프로젝트 준비 검사 실패 0. 미확정 사항·300자 초과 문단 25개 경고는 남아 있다. 전체 pytest 841 passed · 1 skipped. 워크스페이스 검사 failed=0 · gateFailures=0. 브라우저 보안 정책이 file:// 열기를 거부해 화면 검토는 수행하지 못했다.

## 2026-09-30 오후 키트 작업 규칙 v8 반영 · 한눈에 보기

- 완료: 앞 세션(한도로 중단)의 작업을 다시 검증해 `106589a` 로 커밋했다. 키트 migration 으로 `AGENTS.md` 사양 규칙과 `.project-check/` 를 v8 로 올리고, SPEC 1절에 "한눈에 보기" 흐름도를 넣었다.
- 검증: 핵심 앱 pytest 841 passed · 1 skipped, Hub DB 없는 테스트 46 passed, 배포 셸 스크립트 `bash -n`, 준비 검사 실패 0, 워크스페이스 검사 `failed=0 gateFailures=0`.
- 남은 일: 바로 아래 항목의 "확인 못 한 것" 4가지와 "남은 일"이 그대로 남아 있다.

## 2026-09-30 결정 18건 반영 · 사양–코드 불일치 60건 수정

- 완료: `OPEN_QUESTIONS.md` 8-1~8-18 을 추천안대로 구현했다. `docs/SPEC_CODE_MISMATCH.md` 65건 가운데 60건을 고치고 1건을 일부 고쳤다. 영역은 공통 기반, 매뉴얼 개정 검증, Regression 분석, QA Agent, 일일 QA 점검, QA Manual Hub 다. SPEC·12절 추적성·13절·CHANGELOG 를 같이 고쳤다.
- 완료: REQ-MANUAL-018 Claude 대화용 매뉴얼 개정 검증(`scripts/manual_review_local.py`, `.claude/skills/vxvue-manual-revision-review/`). 표본 `.docx` 로 extract → finish 를 실제로 돌려 Excel·Word Comment·`comments.json` 이 나오는 것을 확인했다.
- 검증: 핵심 앱 pytest 841 passed · 1 skipped, QA Manual Hub 의 DB 없이 도는 테스트 46 passed, 배포 셸 스크립트 `bash -n`, 주요 화면 9개 200, 준비 검사 실패 0.
- 확인 못 한 것:
  1. QA Manual Hub 의 PostgreSQL 테스트(이 PC 에 PostgreSQL 없음). DB 있는 곳에서 `services/qa-manual-hub/backend` 의 `pytest tests -q` 를 돌린다.
  2. Docker Compose 실제 실행, 프런트엔드 `npm run build`(`node_modules` 없음).
  3. 일일 QA 점검에서 Claude 의 작업 폴더 밖 읽기가 서버에서 실제로 거부되는지(`docs/modules/daily-qa.md` 알려진 제한).
  4. 실제 개정 매뉴얼로 매뉴얼 분석 한 번(사용자에게 파일 경로 요청 중).
- 남은 일: 서버 배포(구현 완료 뒤 사용자 요청 시). 남은 불일치 4건(1-2, 2-10, 3-9, 5-11)과 SPEC 13절의 확인 필요 항목은 사용자 결정이 필요하다.

## 2026-09-29 오후 사양서 전체 상세화 · 약칭 제거

- 완료: SPEC 전체 상세화(요구사항 251개, 테스트 137개), `docs/SPEC_CODE_MISMATCH.md`(65건), `OPEN_QUESTIONS.md` 8절(결정 18건), 일일 QA 점검 화면·메일의 약칭 제거.
- 검증: 전체 pytest, 준비 검사(실패 0).
- 남은 일: `OPEN_QUESTIONS.md` 8절 결정 → 결정마다 SPEC·코드 수정. 결정 없이 고칠 수 있는 `CODE 수정` 항목(예: Knowledge 화면 스크립트 문법 오류, Manual Hub 세션 연장·감사 IP)은 별도 작업으로 진행한다. 서버 배포는 구현이 끝난 뒤.

## 2026-09-29 전체 자동화 흐름 사양화 · 지식 업로드 결함 수정 (REQ-SYNC-001·002)

- 완료: SPEC 4절 흐름도 3개와 끊김 표, REQ-SYNC-001·002, 읽지 못하는 새 판이 이전 판을 지우던 결함 수정, PARTIAL 종료 코드 1.
- 검증: 새 테스트 7개(해당 코드를 빼면 실패 확인), 전체 pytest, 준비 검사.
- 남은 일: **서버 배포** — 결함 수정은 서버의 `app/core/knowledge_upload.py` 가 바뀌어야 적용된다. 배포 전까지 서버는 옛 동작이다. 일일 QA 점검 모듈도 아직 서버에 배포하지 않았다.

## 2026-09-28 저녁 지식 업로드 예약 · 감사 기록 · 문서 보강

- 완료: `QA_ProductKnowledge_Sync` 등록(평일 10:00) 및 실제 실행 2회(결과 0, 서버 규칙 Rev1.17), 지침 프롬프트 밑줄 파일명 분류, `polarion_query_backup.pdf` 수집 제외, 도구 호출 감사 기록(`claude_logs/`), README·AUTOMATION·daily-qa·보안 문서·SPEC 보강.
- ALM-QA-Automation 로컬 설정(git 제외)의 옛 경로 2개를 고쳤다: `knowledge_folder`, `mail.credentials_ini`. 고치기 전 `automation.py --check-local` 이 메일 설정 오류로 실패했고(내일 09:00 실행이 수집 전에 멈출 상태), 고친 뒤 OK 다. 백업은 그 저장소 `.project-governance-backups/config-20260928/`.
- 확인만 한 것: Redmine 알림 작업 두 개는 이미 새 경로를 쓰고 `--dry-run` 이 정상 종료했다(알림 설정은 꺼져 있음).
- 남은 일: 서버 설치(`docs/modules/daily-qa.md`), 서버 첫 정식 실행 뒤 `claude_logs/` 에 도구 기록이 찍히는지 확인. 결정 대기는 `OPEN_QUESTIONS.md` 3~6.

## 2026-09-28 재배치 뒤 경로·동기화 시각 정리 (REQ-ISSUE-001)

- 완료: `vxvue.yaml` 경로 3개 교체, 사양서 동기화 평일 09:40 (설정과 작업 스케줄러 둘 다, XML 백업 있음), QA Agent 이슈 Export 실행 폴더 구조 대응, 옛 프로젝트 이름 정리.
- 검증 완료: 새 경로에서 사양서 dry-run 6건 감지, 이슈 목록 41건(이전 코드 20건), 새 테스트 4개의 이전 코드 실패 확인.
- 남은 일: 이 PC 에는 지식 폴더를 서버로 올리는 예약 작업이 없다. 서버의 QA 규칙을 Rev1.17 로 올리려면 `scripts/sync_product_knowledge.py --upload-to <서버>` 를 사람이 실행하거나 예약 작업을 새로 등록해야 한다 (등록 여부는 사용자 결정).

## 2026-09-28 VXvue 일일 QA 점검 (REQ-DAILY-001~010, NFR-SEC-001)

- 완료: `app/modules/daily_qa/` 모듈(수집·색인·격리 실행·검증·메일·검토 화면), Skill 5개, CLI, 앱 내장 예약(평일 07:30, 분리 프로세스), SPEC·문서.
- 검증 완료: 전체 `pytest` 685 passed, 보안 검사 변이 5건 실패 확인, 실제 Claude CLI 로 B 작업 1건 end-to-end, 로컬 dry-run(E 실데이터), 검토 화면 승인 흐름.
- 미완료 (서버에서 사람이 할 일, `docs/modules/daily-qa.md` "서버 설치"):
  1. 서버에 Claude CLI 설치, 회사 Team 계정으로 `claude setup-token` 발급 → `secrets.txt`.
  2. 읽기 전용 Polarion PAT·주소, 메일 수신자, `daily_qa.review_base_url` 설정.
  3. 담당자 PC 지식 폴더 동기화로 QA 규칙 Rev1.17 을 서버에 올리기 (현재 서버 수집본 판 미확인, 개발 PC 수집본은 Rev1.12).
  4. `--check` → `--dry-run` → 정식 1회 → 앱 재시작(내장 예약 등록 확인).
  5. 정보보안팀 검토 (`docs/SECURITY_AI_AGENT.md` 1·14~16번).
- 결정 필요 (SPEC §13): 새 이슈 조회식, TC Legacy 번호 대응표 존재 여부. Codex 교차 검증은 이후 고도화.

## 2026-09-22 listen 포트 단일 원본화 (REQ-DEPLOY-001)

- 완료: `app/serve.py` 신규, `app_bind()`/`app_self_url()` 도입, 진입점 3곳·운영 스크립트 3곳의
  포트 하드코딩 제거, 운영 포트 `24357` 선정(서버 `ss -ltn`·ephemeral 범위 실측), 문서·SPEC·
  CHANGELOG 갱신.
- 검증 완료: `pytest` 634 passed / 1 skipped, 새 검사 6종의 negative control, 실제 기동 후
  `/health` 200, `config.yaml` 만 바꿔 바인딩 포트가 따라오는 것까지 확인.
- **서버 반영 완료 (2026-09-22)**: ufw `24357/tcp` 허용 · nginx 설정 교체 후 reload ·
  systemd 유닛 재설치(`__PORT__` 제거) · 서비스 재기동 · ufw `12000/tcp` 규칙 제거.
  전환 스크립트는 실패 시 자동 원복하도록 했고 이전 설정은 서버
  `/root/qa-port-cutover-20260921-205343/` 에 백업돼 있다.
- **전환 중 발견해 함께 고친 것**: 서버 crontab 의 `monitor_health.py` 줄이
  `--base-url http://127.0.0.1:12000` 을 들고 있었다. 그대로 두면 10분마다 상시 alert 가
  된다. 그 인자만 제거했다(백업 `/home/ubuntu/crontab-backup-20260921-205528.txt`).
- 서버 검증: 주요 경로 9개 + 하위 서비스 2개 모두 nginx 경유 200, 옛 포트 무응답,
  **외부 클라이언트에서** `:80` 과 `:24357` 200(= ufw 통과 확인), cron 명령 실제 실행 시
  `alerts: []`.
- 크롤러 PC 스케줄 작업은 인자 없이 돌아 새 포트를 자동으로 따라간다 — 조치 불필요.
- 미완료(이전부터): SPEC 13절 전체 기능 사양화, 그 외 운영 검증.

## 2026-09-21 공통 개발 기준 도입

- 완료: 기존 지침·컴파일된 Akela slice·테스트·구현 근거 확인, 제한된 3개 요구사항과 변경 이력 작성.
- 미완료: SPEC 13절 전체 기능 사양화, 운영 검증, 공통 workflow 및 독립 완료 검사 연결 확인.
- 테스트: 이번 문서 작업에서는 실행하지 않았다. 실행 명령과 안전한 선행조건은 SPEC 11절에 있다.
- 기능 코드·비밀 설정·운영 데이터·기존 문서 변경 및 Git push 없음.
- 전체 준수 또는 실제 CLI/운영 검증 완료로 선언하지 않는다.

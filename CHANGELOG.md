# 변경 이력

## 2026-09-28

### `projects/` 재배치·ALM-QA-Automation 통합 뒤의 경로와 동기화 시각 정리

- `config/products/vxvue.yaml` 의 없어진 경로 3개를 새 위치로 바꿨다. 지식 폴더는 `projects/vxvue/VXvue 지식파일`, 사양서 PDF 는 `projects/ALM-QA-Automation/apps/srs-spec/output`, 이슈 Export 는 `projects/ALM-QA-Automation/apps/issue-export/polarion_backup` 이다. 곧 지워질 `projects/_org/` 는 가리키지 않는다. 새 경로에서 `sync_vxvue_spec.py --dry-run` 이 9/28 사양서 PDF 6건을 찾는 것을 확인했다.
- 사양서 동기화 시각을 월 07:30 에서 평일 09:40 으로 옮겼다 (`sync.day_of_week: mon-fri`, `schedule_time: 09:40`). ALM 수집이 평일 09:00 통합 수집으로 바뀌어, 옛 시각으로는 월요일에 지난주 사양서를 올리게 되기 때문이다. 이 PC 의 작업 스케줄러 `AIRegressionAnalyzer_VXvueSpecSync` 트리거도 같은 시각으로 바꿨고, 바꾸기 전 XML 은 로컬 `backups/` 에 두었다.
- REQ-ISSUE-001: QA Agent 가 이슈 Export 폴더의 실행 폴더 구조(`<실행폴더>/<이슈ID>/backup.json`)도 읽는다. 같은 이슈가 여러 실행에 있으면 가장 최근 실행의 것을 쓰고, `.staging-`·`.previous-` 폴더는 읽지 않는다. 실데이터에는 예전 구조 20건과 실행 폴더 21건이 있어, 이전 코드로는 최신 21건이 목록에서 빠졌다.
- 실제 Export 폴더 확인 테스트는 폴더가 없으면 건너뛰게 되어 있어, 재배치로 경로가 없어진 뒤로 조용히 skip 되고 있었다. 경로를 고친 뒤 실제로 돈다. "목록이 비어 있지 않다"만 보던 검사가 실행 폴더 누락을 못 잡아서, 디스크의 `backup.json` 을 따로 세어 목록과 대조하는 검사를 더했다. 이 검사와 합성 테스트 3개는 이전 코드로 되돌리면 실패하는 것을 확인했다.
- 화면 안내·코드 주석·문서의 옛 프로젝트 이름(`alm-issue-export`, `vxvue-srs-spec-automation`)을 ALM-QA-Automation 의 앱 이름으로 고쳤다. 이력을 적은 곳은 "통합 전 이름"으로 남겼다.

### VXvue 일일 QA 점검 (`/daily-qa`, 서버 예약 실행)

- REQ-DAILY-001: `scripts/run_daily_qa.py` 가 평일 아침 한 번 도는 일일 점검을 추가했다. 잠금 파일로 중복 실행을 막고, 단계 하나가 실패해도 나머지는 계속 돈다. 실행마다 실행 기록과 `output/daily_qa/<실행ID>/` 가 생긴다.
- REQ-DAILY-002: Polarion 을 GET 만 하는 읽기 전용 클라이언트로 SRS·이슈를 읽는다. SRS 는 날짜별 스냅샷으로 저장해 전날과 비교한다. 조회 결과가 0건이면 스냅샷을 저장하지 않는다(다음 날 전부 삭제로 보이는 것을 막는다).
- REQ-DAILY-003: 바뀐 SRS 의 후보 TC 를 SRS 번호 열로 고르고 Skill `vxvue-spec-change-impact` 가 판정한다. 삭제된 SRS 를 가리키는 TC 는 코드로 찾는다.
- REQ-DAILY-004: Skill `vxvue-issue-verification` 이 이슈 유형을 나누고 Program Fixed 만 TC 초안을 만든다. 초안은 영향성평가 Checklist 와 같은 열 순서의 새 Excel 로 저장하고, AI 판단은 `Review` 시트에 분리한다.
- REQ-DAILY-005: 추적 공백(E)을 AI 없이 계산한다. 실데이터에서 TC 의 Legacy 번호 317종 가운데 239종이 현재 `oldId` 와 맞지 않아, Legacy 번호 불일치는 삭제로 단정하지 않고 건수로만 남긴다.
- REQ-DAILY-006: Skill `vxvue-manual-completeness` 가 지난 7일 바뀐 SRS 의 매뉴얼 누락 후보를 낸다. 운영 첫 주처럼 7일 전 스냅샷이 없으면 가장 오래된 스냅샷과 비교한다.
- REQ-DAILY-007: Skill 결과 JSON 을 검증한다. 근거 위치가 없거나, 판정 값이 목록 밖이거나, 이슈 종료·TC 덮어쓰기를 담은 Finding 은 버린다. 결과 파일이 깨지면 한 번 다시 실행한다.
- REQ-DAILY-008: 실행이 끝나면 건수·상태·링크만 담은 요약 메일을 보낸다 (`app/core/notifier.py` 의 `send_report`).
- REQ-DAILY-009: `/daily-qa` 검토 화면(실행 목록, 대기열 필터, 승인·거절·근거 추가 필요, AI 질문 답변)을 추가했다. 답변은 다음 실행 입력에 들어간다. 한글 검토자 이름이 쿠키 저장에서 오류 나던 문제를 테스트로 잡아 URL 인코딩으로 고쳤다.
- REQ-DAILY-010: 수집된 QA 규칙 판이 Skill 기준(Rev1.17)과 다르면 AI 단계를 멈추고 메일 첫 줄에 알린다.
- NFR-SEC-001: Claude 는 저장소 밖 격리 작업 폴더에서 `--permission-mode dontAsk`, `--setting-sources project`, `--strict-mcp-config` 로 돈다. 읽기·검색·결과 폴더 쓰기만 허용하고, 입력은 마스킹 후 보관하며, 프로세스에는 Claude 토큰 외의 비밀값을 넘기지 않는다. 보안 통제 목록은 `docs/SECURITY_AI_AGENT.md`.
- 비밀 설정에 `POLARION_HOST`, `POLARION_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `DAILY_QA_EMAIL_TO` 를 추가했다. `data/daily_qa/`, `output/daily_qa/` 는 `.gitignore` 에 넣었다.
- 예약: 앱 내장 스케줄러가 평일 07:30(KST)에 점검을 분리된 별도 프로세스로 띄운다 (`scheduled_jobs.py`). 신규 systemd 유닛을 만들지 않는다는 프로젝트 규칙을 따랐다. 자격증명이 없는 호스트에서는 건너뛴다.
- 저장: 일일 점검 테이블의 SQL 은 `app/core/daily_qa_storage.py`(`Storage` 믹스인)에 두어 DB 접근을 `Storage` 한 곳으로 유지했다.
- 선택 적용용 서버 전체 Claude 정책 `deploy/claude/managed-settings.json`.
- 검증: `pytest` 685 passed / 6 skipped. 신규 테스트 59개 중 보안 검사 5건은 코드를 일부러 망가뜨려 실패하는 것까지 확인했다. 실제 Claude CLI 로 B 작업 1개(VP-531·VP-532, 후보 TC 6개, QA 규칙 Rev1.17)를 끝까지 돌려 결과 파일 1개만 쓰고 Finding 15건이 검증을 통과하는 것을 확인했다. 로컬 dry-run 에서 실데이터 E 단계(SRS 437, TC 7,686행)와 검토 화면 승인 흐름을 확인했다. 운영 서버 설치·Polarion 실연결·메일 수신은 아직 확인하지 않았다.

## 2026-09-22

### listen 포트를 `config.yaml` 단일 원본으로, 운영 포트 12000 → 24357

- `config.yaml` 의 `app.port` 가 실제 바인딩을 결정하지 않던 문제를 고쳤다. 이전에는 포트가
  네 곳(`scripts/run.sh`, `scripts/run.ps1`, `deploy/systemd/qa-verification.service`,
  `config.yaml`)에 각각 적혀 있었고, `app.port` 는 예약/Knowledge 동기화가 자기 자신을
  호출하는 URL 에만 쓰였다 — `config.yaml` 만 고치면 앱은 옛 포트에 뜬 채 예약 작업만 새
  포트를 두드렸다.
- 진입점을 `python -m app.serve` 로 통일했다 (`app/serve.py` 신규). `app/core/config.py` 의
  `app_bind()` / `app_self_url()` 이 `config.yaml` 에서 주소·포트를 읽는 유일한 경로다.
- `scripts/monitor_health.py` 의 `--base-url` 기본값과 `scripts/deploy.ps1` 의 헬스체크 포트,
  `scripts/sync_vxvue_spec.py` 의 `--target-url` 기본 포트도 같은 원본에서 파생한다.
- `deploy/systemd/qa-verification.service` 의 `__PORT__` 플레이스홀더를 제거했다 (치환 대상
  3개 → 2개).
- 운영 포트를 `12000` 에서 `24357` 로 바꿨다. 1024 초과이고, 서버의 ephemeral 범위(실측
  `32768~60999`) 밖이며, `ss -ltn` 으로 해당 호스트에서 비어 있음을 확인했다.
- nginx 는 YAML 을 읽지 못해 `deploy/nginx/qa-platform.conf` 의 upstream 만 자동으로 따라가지
  못한다. `tests/test_serve_bind.py` 가 `config.yaml` 과 어긋나면 실패시킨다.
- SPEC 에 REQ-DEPLOY-001 / TEST-DEPLOY-001 을 추가했다.
- 검증: `pytest` 634 passed / 1 skipped. 새 검사는 고의 파손으로 실패하는 것까지 확인했다.
  `python -m app.serve` 로 실제 기동해 `0.0.0.0:24357` LISTEN 과 `/health` 200 을 확인했고,
  `config.yaml` 만 24361 로 바꿔 재기동하면 그 포트로 따라오는 것도 확인했다.
- **운영 서버(10.13.0.222)에 반영 완료.** `scripts/deploy.ps1` 로 파일·의존성을 올린 뒤
  ufw `24357/tcp` 허용 → nginx 설정 배치 및 `nginx -t` 선검사 → systemd 유닛 재설치
  (`__PORT__` 없음) → `systemctl restart` → `nginx -s reload` 순서로 전환했다. 실패 시
  자동 원복하도록 했고, 이전 설정은 서버의 `/root/qa-port-cutover-20260921-205343/` 에
  백업했다. 전환 후 ufw 의 `12000/tcp` 규칙은 제거했다.
- 서버 crontab 의 10분 주기 `monitor_health.py` 가 `--base-url http://127.0.0.1:12000` 을
  들고 있어 전환 즉시 상시 alert 가 될 상태였다. 그 인자를 제거해 `config.yaml` 을 따르게
  했다(같은 crontab 의 다른 프로젝트 줄은 그대로 두었고, 백업은 서버의
  `/home/ubuntu/crontab-backup-20260921-205528.txt`).
- 서버 검증: 유닛 `active`/`enabled`, `0.0.0.0:24357` LISTEN, nginx 경유 `/health`
  `/config/status` `/operations/status` `/` `/knowledge` `/impact-analyzer`
  `/manual-review` `/cost-dashboard` `/qa-agent` 모두 200, 하위 서비스
  `/manual-hub/api/health` 와 `/manual-hub/` 도 200. 옛 포트 12000 은 응답 없음.
  **외부 클라이언트(개발 PC)** 에서 `http://10.13.0.222/` 와 `http://10.13.0.222:24357/health`
  200 확인 — loopback 은 ufw 를 통과하지 않으므로 이 확인이 방화벽 검증이다.
  cron 이 실제로 돌릴 명령을 그대로 실행해 `alerts: []`, `checks: {nginx: ok, manual_hub: ok}`
  를 확인했다.
- 크롤러 PC 의 작업 스케줄러 `AIRegressionAnalyzer_VXvueSpecSync` 는 인자 없이 실행되므로
  기본 `--target-url` 이 `http://10.13.0.222:24357` 로 자동으로 따라간다. 별도 조치 없음.

## 2026-09-21

- 기존 테스트와 구현에 근거한 핵심 요구사항 3개를 SPEC 첫 기준선으로 작성했다.
- 구현·테스트 추적 경로 및 전체 사양에서 아직 확인하지 않은 범위를 명시했다.
- 기능/설정/운영 데이터/예약 작업/인증/기존 문서를 변경하지 않았다.
- 실제 테스트 및 운영 검증 완료를 주장하지 않는다.

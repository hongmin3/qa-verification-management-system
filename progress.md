# 진행 상태

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

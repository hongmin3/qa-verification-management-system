# 운영 서버 반영 계획 (2026-10-02 작성, 아직 실행 안 함)

이 문서는 계획이다. 2026-10-02 08:00 무렵 서버를 읽기만 해서 확인한 사실로 썼다.
서버·Manual Hub·메일·예약 설정은 바꾸지 않았다. 실제 배포는 사용자가 따로 요청할 때 한다.

## 1. 지금 서버 상태 (읽기 전용 확인)

| 항목 | 서버 | 로컬(`978c1c9` 이후) |
|---|---|---|
| 핵심 앱 실행 | systemd `qa-verification.service`, `python -m app.serve`, 2026-09-21 20:53 시작 | 같음 |
| 코드 | 2026-09-21 사본(Git 없음). `app/modules/daily_qa`·`app/core/claude_cli.py` 없음 | 일일 QA 점검·Claude CLI·현재 상태 점검 있음, Regression 화면 제거됨 |
| 파일 비교(app·scripts·config 등, HEAD 기준) | 서버 144개 | 같음 31 · 바뀜 74 · 새 파일 62 · 서버에만 있는 옛 파일 39(`app/modules/impact_analyzer/` 등 옛 Regression) |
| `requirements.txt` | 지문 같음 | 같음. 새 패키지 설치는 필요 없다 |
| `config.yaml` | 2026-09-21 판. `ai:`·`daily_qa:` 절 없음 | 두 절 있음. `ai.claude.models` 세 등급 모두 `claude-opus-5-5` |
| 화면 | `/health` ok, `/qa-agent` 200(옛 화면), `/daily-qa` 404 | |
| Claude CLI·Node | 둘 다 없음 | |
| `secrets.txt` 키 | Gemini·Manual Hub·SMTP·메일 수신자만. `CLAUDE_CODE_OAUTH_TOKEN` 없음 | |
| Manual Hub | 백엔드 `127.0.0.1:9180` 에서 응답, health ok | 저장소는 `24358` |
| DB | `data/app.db` 151KB(10-01 변경) | |
| 백업 | cron 매일 14:15 UTC `scripts/backup_data.py`, 최신 `qa-backup-20261001T181501Z.zip` | |
| 스냅샷 | 서버 `data/daily_qa/snapshots/` 없음 | 로컬 11MB(과거 SRS 10개 날짜 + 이슈 10-01·10-02) |

> **주의** `docs/local/OPERATIONS_LOCAL.md` 의 "재기동 절차"는 `nohup` 으로 적혀 있지만 서버는 systemd 로 돈다. 아래 절차는 systemd 기준이다.

## 2. 배포 전에 정할 것 (사용자 결정)

1. 서버의 Claude 인증 방식. Claude CLI 와 Node 를 설치하고 `CLAUDE_CODE_OAUTH_TOKEN` 을 `secrets.txt` 에 넣을지(`docs/modules/daily-qa.md` 서버 설치 1~2단계).
   인증 없이 올리면 일일 QA 점검은 매일 `PARTIAL`(AI 건너뜀)로 끝나고 화면 AI 기능(QA Agent·매뉴얼 개정 검증)은 실패한다.
2. 모델 등급. 세 등급이 모두 Opus 라 서버에서 한도에 자주 닿을 수 있다. 우선순위 2 측정 결과를 보고 정한다.
3. 일일 QA 점검을 서버에서 언제부터 켤지. 인증이 준비되기 전에는 서버 `config.yaml` 의 `daily_qa.enabled: false` 로 올린다.
4. 로컬에서 가져온 SRS·이슈 스냅샷을 서버로 옮길지. 옮기지 않으면 서버 첫 실행은 기준 스냅샷만 만든다.
5. Manual Hub 포트 `9180 → 24358` 전환을 같은 날 할지.

## 3. 배포 순서 (요청을 받으면)

1. 서버 백업: `backup_data.py --destination backups` 를 한 번 더 돌리고, 앱 폴더 전체를 `~/qa-app-backup-<시각>.tgz` 로 묶는다(`.venv`·`backups` 제외). 서버 `config.yaml`·`secrets.txt` 를 따로 복사해 둔다.
2. 서버 `config.yaml` 과 저장소 판의 차이를 본다. 서버 고유 값(주소·포트·경로·메일)이 있으면 새 판에 옮긴다.
3. 코드 올리기: `scripts/deploy.ps1`(재시작 없이)은 `scp -r` 덮어쓰기라 옛 파일 39개가 남는다.
   남아도 새 `app/main.py` 가 불러오지 않지만, 혼동을 막으려고 백업 뒤 `app/modules/impact_analyzer/`·`app/analyzers/`·`app/reports/`·`app/sync/`·`app/web/routes.py` 등 목록(1절)의 파일을 지운다.
4. 인증을 정했으면 Claude CLI 설치·토큰 등록 → 서버에서 `run_daily_qa.py --check`.
5. `sudo systemctl restart qa-verification` → `/health`, `/qa-agent`, `/knowledge`, `/cost-dashboard` 200, `/daily-qa` 307, 옛 Regression 주소 308 확인.
6. DB 표는 앱 시작 때 `CREATE TABLE IF NOT EXISTS` 로 더해진다. 시작 뒤 `daily_qa_runs`·`qa_change_events` 표가 생겼는지 본다.
7. Manual Hub 포트 전환을 함께 하면 `OPERATIONS_LOCAL.md` 의 전환 절차를 따른다.

## 4. 되돌리기

1. `sudo systemctl stop qa-verification`
2. 1단계 tgz 를 앱 폴더에 풀고, 따로 둔 `config.yaml`·`secrets.txt` 를 되돌린다.
3. DB 는 새 표만 더해지므로 옛 코드가 그대로 읽는다. 문제가 있으면 1단계 백업 zip 의 `app.db` 로 바꾼다.
4. `sudo systemctl start qa-verification` → `/health` 확인.

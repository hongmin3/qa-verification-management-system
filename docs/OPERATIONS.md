# 운영 신뢰성·백업·모니터링

## Claude 메모리와 캡처 정리

기존 `scripts/monitor_health.py` 예약이 10분마다 Claude의 RAM·스왑 사용량과 이 앱의 캡처 묶음을 확인한다. 결과는 `output/logs/monitor.log`의 `claude_resources`에 남는다.

- 일반 Claude와 사용자 세션은 조회만 한다. 한 프로세스의 RSS와 스왑 합계가 1GiB 이상이면 `claude_resource_alert`를 남긴다.
- 자동 종료는 이름과 설명이 일치하는 이 앱의 캡처 묶음 중 실행 시간 상한을 넘긴 것만 수행한다. PID 이름이나 과거 임시 경로로 종료 대상을 고르지 않는다.
- 캡처 자체에도 RAM 1GiB·스왑 256MiB와 실행 시간 상한이 있다. 커널의 제한을 확인하지 못하면 Claude를 띄우지 않는다. 앱이 종료되어도 systemd 시간 제한은 유지된다.
- 캡처 서버에는 systemd 사용자 관리자와 위임된 메모리 제어가 필요하다. 사용할 수 없으면 캡처를 건너뛰며 일반 분석은 계속한다. 재부팅과 로그아웃 뒤에도 동작하려면 서비스 계정의 `Linger`가 켜져 있어야 한다. 운영 서버 적용 결과는 `progress.md`에 있다.

캡처를 운영하는 Linux 서비스 계정으로 로그인해 사용자 관리자를 유지하도록 한 번 설정한다.

```text
loginctl enable-linger
```

이 명령의 정책상 권한이 없으면 관리자에게 서비스 계정의 `Linger` 활성화를 요청한다. 설정이 어려운 환경에서는 캡처를 제한 없이 실행하지 않고 건너뛴다.

수동 확인도 기존 운영 점검 명령을 사용한다.

```text
python scripts/monitor_health.py --disk-path .
```

높은 메모리 사용만으로 일반 Claude를 강제 종료하지 않는다. 해당 작업의 소유자와 진행 여부를 확인하고 정리한다. 스왑 사용률만 높고 현재 입출력이 없다면 과거에 밀려난 메모리일 수 있으므로 현재 메모리 대기도 함께 확인한다.

## 분석 작업 복구

- `analysis.max_concurrent_jobs`를 초과한 신규 요청은 HTTP 429로 거절한다.
- 분석 요청에는 업로드 원본의 내부 저장 경로와 입력값을 `request_json`에 보존한다.
- 서버 시작 시 실행 중이던 `RUNNING` 작업은 안전하게 `FAILED`로 전환하며 상세 화면의
  **같은 입력으로 재실행** 버튼으로 새 분석을 만들 수 있다.
- 아직 실행을 시작하지 않은 `QUEUED` 작업은 원본 파일이 존재하면 자동 재제출한다.
- `analysis.job_timeout_minutes` 동안 단계 갱신이 없는 작업은 `/operations/status`의
  `stale_jobs`에 집계된다. 실행 중인 Python thread를 강제 종료하지 않고 운영자에게 경고한다.

## 백업과 복구 검증

```bash
.venv/bin/python scripts/backup_data.py --destination backups
.venv/bin/python scripts/backup_data.py --verify backups/qa-backup-YYYYMMDDTHHMMSSZ.zip
```

SQLite Online Backup API로 일관된 DB 사본을 만들고 업로드·사양서·TC·인덱스·매뉴얼 원본을
ZIP에 함께 저장한다. 챗봇 대화 DB, 첨부와 답변이 조회한 원본 판도 포함한다. 다시 만들 수 있는 챗봇 검색 색인은 제외한다. `manifest.json` SHA-256과 임시 복원 DB의 `PRAGMA integrity_check`를
모두 통과해야 성공이다. 원본 운영 데이터를 덮어쓰는 자동 복원 기능은 의도적으로 제공하지 않는다.

### 오래된 백업 자동 정리

새 백업 확인이 끝나면 같은 명령이 보관 일수가 지난 것을 지운다(REQ-OPS-002, `config.yaml` `retention.*`).

| 대상 | 보관 |
|---|---|
| 백업 ZIP `backups/qa-backup-<UTC 시각>.zip` | 7일. 가장 새 것 하나는 남긴다 |
| 배포 전 백업 `backups/deploy-<이름>-<UTC 시각>` (폴더 또는 `.tgz`·`.tar`·`.tar.gz`·`.zip`) | 14일. 가장 새 것 하나는 남긴다 |

배포하기 전에 서버 코드·설정·DB 를 따로 백업할 때는 반드시 위 이름으로 `backups/` 안에 만든다.

```bash
cd /home/ubuntu/ai-regression-impact-analyzer
tar czf "backups/deploy-app-$(date -u +%Y%m%dT%H%M%SZ).tgz" app scripts config config.yaml
```

다른 이름이나 홈 폴더(`/home/ubuntu/qa-*.tgz` 등)에 둔 백업은 자동으로 지워지지 않는다.
2026-10-16 에 이렇게 쌓인 백업 약 13GB 를 손으로 지웠다.

## 모니터링

```bash
.venv/bin/python scripts/monitor_health.py --disk-path .
```

`--base-url` 을 생략하면 `config.yaml` 의 `app.port` 로 자기 자신을 본다 (REQ-DEPLOY-001).
다른 대상을 보려면 `--base-url` 로 명시한다.

`/health`, `/config/status`, `/operations/status`를 확인하고 디스크 부족, 토큰 한도 초과,
DB 무결성 오류, stale 작업, 마지막 지식 동기화 실패 시 exit code 1을 반환한다.

운영 서버의 사용자 crontab에는 다음 두 작업을 등록한다. 시각은 서버 시간대(`America/New_York`) 기준이다.
14:15 는 한국 시간 03:15(미국 서머타임이 아닐 때 04:15)다.

```cron
15 14 * * * cd /home/ubuntu/ai-regression-impact-analyzer && .venv/bin/python scripts/backup_data.py --destination backups >> output/logs/backup.log 2>&1
*/10 * * * * cd /home/ubuntu/ai-regression-impact-analyzer && .venv/bin/python scripts/monitor_health.py --check nginx=http://127.0.0.1/health --check manual_hub=http://127.0.0.1/manual-hub/api/health --disk-path . >> output/logs/monitor.log 2>&1
```

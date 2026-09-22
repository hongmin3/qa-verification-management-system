# 운영 신뢰성·백업·모니터링

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
ZIP에 함께 저장한다. `manifest.json` SHA-256과 임시 복원 DB의 `PRAGMA integrity_check`를
모두 통과해야 성공이다. 원본 운영 데이터를 덮어쓰는 자동 복원 기능은 의도적으로 제공하지 않는다.

## 모니터링

```bash
.venv/bin/python scripts/monitor_health.py --disk-path .
```

`--base-url` 을 생략하면 `config.yaml` 의 `app.port` 로 자기 자신을 본다 (REQ-DEPLOY-001).
다른 대상을 보려면 `--base-url` 로 명시한다.

`/health`, `/config/status`, `/operations/status`를 확인하고 디스크 부족, 토큰 한도 초과,
DB 무결성 오류, stale 작업, 마지막 지식 동기화 실패 시 exit code 1을 반환한다.

운영 서버의 사용자 crontab에는 다음 두 작업을 등록한다.

```cron
15 2 * * * cd /home/ubuntu/ai-regression-impact-analyzer && .venv/bin/python scripts/backup_data.py --destination backups >> output/logs/backup.log 2>&1
*/10 * * * * cd /home/ubuntu/ai-regression-impact-analyzer && .venv/bin/python scripts/monitor_health.py --disk-path . >> output/logs/monitor.log 2>&1
```

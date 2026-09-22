# 변경 이력

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

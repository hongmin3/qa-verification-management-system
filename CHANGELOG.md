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
- **서버에는 아직 반영되지 않았다.** 방화벽·nginx·systemd 조치가 필요하다 —
  `docs/DEPLOYMENT.md` 참고.

## 2026-09-21

- 기존 테스트와 구현에 근거한 핵심 요구사항 3개를 SPEC 첫 기준선으로 작성했다.
- 구현·테스트 추적 경로 및 전체 사양에서 아직 확인하지 않은 범위를 명시했다.
- 기능/설정/운영 데이터/예약 작업/인증/기존 문서를 변경하지 않았다.
- 실제 테스트 및 운영 검증 완료를 주장하지 않는다.

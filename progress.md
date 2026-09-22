# 진행 상태

## 2026-09-22 listen 포트 단일 원본화 (REQ-DEPLOY-001)

- 완료: `app/serve.py` 신규, `app_bind()`/`app_self_url()` 도입, 진입점 3곳·운영 스크립트 3곳의
  포트 하드코딩 제거, 운영 포트 `24357` 선정(서버 `ss -ltn`·ephemeral 범위 실측), 문서·SPEC·
  CHANGELOG 갱신.
- 검증 완료: `pytest` 634 passed / 1 skipped, 새 검사 6종의 negative control, 실제 기동 후
  `/health` 200, `config.yaml` 만 바꿔 바인딩 포트가 따라오는 것까지 확인.
- **남은 사람 조치 (서버에서 실행)**: 이 저장소 밖이며 아직 하지 않았다.
  1. `sudo ufw allow 24357/tcp` · 기존 `12000/tcp` 규칙 정리
  2. `deploy/nginx/qa-platform.conf` 배치 후 `sudo nginx -t && sudo systemctl reload nginx`
  3. systemd 유닛 재설치(`__PORT__` 없어짐) 후 `sudo systemctl restart qa-verification`
  4. 크롤러 Windows PC 의 작업 스케줄러에 `:12000` 이 박힌 인자가 있으면 갱신
  - 조치 전까지 서버는 `12000` 으로 동작하고, 새 nginx 설정을 먼저 적용하면 502 가 된다.
- 미완료(이전부터): SPEC 13절 전체 기능 사양화, 그 외 운영 검증.

## 2026-09-21 공통 개발 기준 도입

- 완료: 기존 지침·컴파일된 Akela slice·테스트·구현 근거 확인, 제한된 3개 요구사항과 변경 이력 작성.
- 미완료: SPEC 13절 전체 기능 사양화, 운영 검증, 공통 workflow 및 독립 완료 검사 연결 확인.
- 테스트: 이번 문서 작업에서는 실행하지 않았다. 실행 명령과 안전한 선행조건은 SPEC 11절에 있다.
- 기능 코드·비밀 설정·운영 데이터·기존 문서 변경 및 Git push 없음.
- 전체 준수 또는 실제 CLI/운영 검증 완료로 선언하지 않는다.

# 핵심 앱 — 배포와 운영

> 서버 주소·계정·경로 같은 사내 고유 정보는 여기에 적지 않는다. Git 제외 대상인
> `docs/local/OPERATIONS_LOCAL.md`에 둔다.

## 배포 스크립트가 옮기는 것
<!-- akela: id=deploy-script-scope scope=deployment tier=must -->

- `scripts/deploy.ps1`은 로컬 `pytest`를 통과시킨 뒤 `app`, `scripts`, `tests`, `config`, `docs`, `deploy`, `requirements.txt`, `config.yaml`, 예제 파일만 서버로 복사한다.
- `secrets.txt` / `secrets.json` / `.env` / `data/` / `output/`은 **복사하지 않는다.** 서버의 실제 설정과 운영 데이터를 덮어쓰지 않기 위한 의도적 설계다.
- 서버 배포 폴더는 Git 저장소로 두지 않는다. 로컬에서 검증한 파일만 옮긴다.
- `config.yaml`은 복사 대상이다. 서버에만 있는 설정 차이를 만들면 다음 배포에서 덮어써진다 — 그런 값은 환경변수나 `secrets.*`로 빼야 한다.
- 의존성 동기화·재기동은 이 스크립트가 이어서 수행한다 — §의존성 동기화는 따로 한다, §재기동 절차 참고.

## 의존성 동기화는 따로 한다
<!-- akela: id=dependency-sync scope=deployment tier=must -->

`deploy.ps1`은 파일 전송 뒤 서버에서 `.venv/bin/pip install -r requirements.txt`를 항상 자동으로 실행한다(2026-09-02부터). `requirements.txt`가 바뀌었어도 별도로 챙길 필요가 없다 — 다만 배포 스크립트를 거치지 않고 파일만 수동으로 옮겼다면 이 단계를 직접 실행해야 한다.

## 재기동 절차
<!-- akela: id=restart-procedure scope=deployment tier=must -->

- 핵심 앱은 systemd 유닛(`qa-verification.service`)으로 떠 있다(2026-09-02 systemd 전환 완료). 재기동은 `sudo systemctl restart qa-verification` 한 줄이면 된다 — nohup/kill 방식이 아니다.
- `scripts/deploy.ps1 -Restart`를 쓰면 파일 전송 + 의존성 설치 + 위 재기동 + 헬스체크까지 한 번에 수행한다(`secrets.txt`의 `SERVER_SUDO_PASSWORD` 필요). `-Restart` 없이 실행하면 재기동은 하지 않고 사람이 나중에 고른다.
- 재기동 후 `/health`가 `{"status":"ok"}`를 반환하는지, 주요 화면(`/`, `/knowledge`, `/analyses`)이 뜨는지 확인한다.
- **다른 서비스나 포트는 건드리지 않는다.**

## 같은 호스트의 다른 서비스를 보호한다
<!-- akela: id=host-isolation scope=deployment tier=must -->

- 이 프로젝트가 설치하지 않은 systemd 유닛·nginx 사이트·PostgreSQL 설정은 변경하지 않는다. 이 프로젝트의 것만 추가하거나 갱신한다.
- 배포 대상 디렉터리 밖의 파일은 열람·수정하지 않는다.
- 방화벽은 이 프로젝트가 쓰는 포트를 여는 등 범위 내 변경만 한다.
- 서버 등록·설정 변경은 영향 범위를 사용자에게 확인한 뒤 진행한다.

## 하위 서비스 통합 배포
<!-- akela: id=platform-nginx scope=deployment tier=should -->

- 같은 호스트의 nginx가 `/`는 핵심 앱(:24357)으로, `/manual-hub/`는 하위 서비스로 라우팅한다 (`deploy/nginx/qa-platform.conf`).
- 하위 서비스 프론트엔드를 재배포할 때 **반드시 `BUILD_MODE=platform`**을 준다. 빠뜨리면 단독용(base `/`)으로 빌드돼 화면이 빈 채로 뜬다. `deploy.sh`가 전송 전에 검사해 중단시킨다.
- 하위 서비스의 `install.sh`를 다시 돌릴 일이 있으면 `SKIP_NGINX=1`을 준다. 안 주면 단독용 nginx 사이트가 다시 설치돼 `/`가 하위 서비스로 돌아간다.
- 통합 전 주소 호환 리다이렉트 블록은 2026-09-02에 제거했다(core 앱 라우터 전체와 겹치는 경로가 없음을 확인한 뒤). **지금은 평문 HTTP만 쓴다** — 같은 날 self-signed 인증서로 HTTPS 강제 리다이렉트를 적용했다가, 브라우저의 "안전하지 않음" 경고가 실제 사용성 문제라 곧바로 롤백했다. 정식 CA(또는 사내 CA) 인증서를 발급받기 전에는 재적용하지 않는다.
- nginx `server{}` 블록 최상위에 바로 쓴 `return`(예: `return 301 https://$host$request_uri;`)은 rewrite phase에서 location 매칭보다 먼저 실행돼, exact-match `location = /health` 같은 예외 규칙이 있어도 무시하고 리다이렉트가 먼저 발생한다. 특정 경로를 리다이렉트에서 빼려면 그 `return`을 `location / { return ...; }`로 감싸야 한다(HTTPS를 다시 켤 때 재사용할 것).
- **포트를 새로 열 때는 nginx 설정만으로 끝난 게 아니다.** `curl 127.0.0.1` 같은 loopback
  검증은 서버 자체 방화벽(`ufw`)을 절대 통과하지 않으므로, ufw가 그 포트를 막고 있어도
  loopback 테스트는 전부 통과해버린다. 실제로 443을 열지 않은 채 HTTP→HTTPS 강제
  리다이렉트를 적용했다가, 외부 사용자가 "연결할 수 없음" 오류를 겪은 뒤에야 발견한 적이
  있다(2026-09-02, 이후 HTTPS 자체를 롤백하며 443도 다시 닫음). 새 포트를 리스닝하게
  만들 때는 `sudo ufw allow <port>/tcp`까지 같이 하고, **loopback이 아니라 실제 외부
  클라이언트에서** 접속을 재확인한다.

## 점검 순서
<!-- akela: id=check-order scope=deployment tier=should -->

Health endpoint → app.log → 입력 형식 → TC ID 컬럼 → `.env` Key → Gemini Rate Limit/Timeout → 검색 Chunk 존재 여부 순서로 확인한다.

## 백업과 상태 확인
<!-- akela: id=ops-monitoring scope=deployment tier=should -->

- `/operations/status`가 실행 중·대기 중 작업 수와 stale 작업을 보고한다. `analysis.job_timeout_minutes` 동안 단계 갱신이 없으면 stale이다.
- SQLite 백업은 Online Backup + SHA-256 + 임시 복원 무결성 검증까지 수행한다 (`scripts/backup_data.py`).
- 상세 절차는 `docs/OPERATIONS.md`에 있다.

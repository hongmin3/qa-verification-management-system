"""핵심 앱 서버 진입점 — listen 주소·포트의 단일 원본은 `config.yaml` 이다.

이 모듈이 생기기 전에는 포트가 네 곳(`scripts/run.sh`, `scripts/run.ps1`,
`deploy/systemd/qa-verification.service`, `config.yaml`)에 각각 적혀 있었고, 그중
`config.yaml` 의 `app.port` 는 **바인딩에 아무 영향이 없었다** — uvicorn 은 명령행의
`--port` 로 떴고, `app.port` 는 예약 동기화가 자기 자신을 호출하는 URL 을 만드는
데에만 쓰였다. 그래서 `config.yaml` 만 고치면 서버는 옛 포트에 붙은 채 예약 작업만
새 포트를 두드리다 실패했다.

이제 세 진입점이 모두 `python -m app.serve` 를 부르므로 `config.yaml` 한 곳만 고치면
된다. 단, **nginx 는 여기서 읽지 못한다** — `deploy/nginx/qa-platform.conf` 의 upstream
포트는 별도 배포 산출물이라 손으로 맞춰야 하고, 어긋나면 `tests/test_serve_bind.py`
가 실패시킨다.
"""

from __future__ import annotations

from app.core.config import app_bind


def main() -> None:
    import uvicorn

    host, port = app_bind()
    uvicorn.run("app.main:app", host=host, port=port)


if __name__ == "__main__":
    main()

"""운영 상태 점검. cron 이 주기적으로 실행하고 JSON 한 줄을 로그에 남긴다.

핵심 앱뿐 아니라 **사용자가 실제로 들어오는 경로 전체**를 확인해야 의미가 있다. 앱은 살아
있는데 nginx 가 죽었거나, 핵심 앱은 멀쩡한데 하위 서비스(매뉴얼 서버)가 내려간 상황을
`--check` 로 함께 감시한다.

    # --base-url 을 생략하면 config.yaml 의 app.port 로 자기 자신을 본다.
    python scripts/monitor_health.py \
        --base-url http://127.0.0.1:24357 \
        --check nginx=http://127.0.0.1/health \
        --check manual_hub=http://127.0.0.1/manual-hub/api/health \
        --disk-path .

감시 대상이 죽었을 때 traceback 으로 끝나면 안 된다 — cron 로그에 남는 것은 파싱 불가능한
스택뿐이고, 다른 점검 항목은 아예 실행되지 않는다. 그래서 모든 조회를 개별적으로 감싸고
실패도 alert 로 표현한다. 종료 코드는 alert 가 하나라도 있으면 1이다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import app_self_url  # noqa: E402


def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:
        if response.status != 200:
            raise RuntimeError(f"HTTP {response.status}: {url}")
        return json.load(response)


def probe(url: str) -> tuple[dict, str | None]:
    """(응답, 오류 메시지) 를 돌려준다. 예외를 밖으로 내보내지 않는다."""
    try:
        return fetch_json(url), None
    except urllib.error.HTTPError as exc:
        return {}, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 alert 로 바꾼다
        return {}, type(exc).__name__


def parse_check(raw: str) -> tuple[str, str]:
    name, separator, url = raw.partition("=")
    if not separator or not name.strip() or not url.strip():
        raise argparse.ArgumentTypeError(f"--check 는 name=url 형식이어야 합니다: {raw}")
    return name.strip(), url.strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    # 기본값을 문자열로 굳히지 않는다 — 포트의 단일 원본은 config.yaml 의 app.port 다.
    parser.add_argument("--base-url", default=None)
    parser.add_argument(
        "--check",
        type=parse_check,
        action="append",
        default=[],
        metavar="NAME=URL",
        help="추가로 감시할 health endpoint. 여러 번 지정할 수 있다.",
    )
    parser.add_argument("--disk-path", type=Path, default=Path("."))
    parser.add_argument("--min-free-gb", type=float, default=2.0)
    args = parser.parse_args()

    # --base-url 을 주지 않으면 config.yaml 의 app.port 로 자기 자신을 본다. 여기에
    # 포트를 굳혀 두면 config.yaml 을 바꾼 뒤 점검만 옛 포트를 두드리게 된다.
    base = (args.base_url or app_self_url()).rstrip("/")
    alerts: list[str] = []

    health, health_error = probe(f"{base}/health")
    config, config_error = probe(f"{base}/config/status")
    operations, operations_error = probe(f"{base}/operations/status")

    if health_error or health.get("status") != "ok":
        alerts.append("health_not_ok")
    if config_error:
        alerts.append("config_status_unreachable")
    if operations_error:
        alerts.append("operations_status_unreachable")

    token = config.get("daily_token_usage", {})
    if token.get("exceeded"):
        alerts.append("daily_token_limit_exceeded")
    if not operations_error:
        if operations.get("database_integrity") != "ok":
            alerts.append("database_integrity_failed")
        if operations.get("stale_jobs", 0):
            alerts.append("stale_jobs")
        if (operations.get("last_sync") or {}).get("status") == "FAILED":
            alerts.append("last_sync_failed")

    # 추가 endpoint. status 필드가 있으면 "ok" 인지까지 확인하고, 없으면 200 응답만으로 본다.
    checks: dict[str, str] = {}
    for name, url in args.check:
        payload, error = probe(url)
        if error:
            checks[name] = error
            alerts.append(f"check_failed:{name}")
        elif payload.get("status", "ok") != "ok":
            checks[name] = str(payload.get("status"))
            alerts.append(f"check_failed:{name}")
        else:
            checks[name] = "ok"

    try:
        free_gb = shutil.disk_usage(args.disk_path).free / (1024 ** 3)
    except OSError:
        free_gb = -1.0
        alerts.append("disk_unreadable")
    if 0 <= free_gb < args.min_free_gb:
        alerts.append("disk_low")

    print(json.dumps({
        "status": "alert" if alerts else "ok",
        "alerts": alerts,
        "free_gb": round(free_gb, 2),
        "daily_token_usage": token,
        "operations": operations,
        "checks": checks,
    }, ensure_ascii=False))
    return 1 if alerts else 0


if __name__ == "__main__":
    raise SystemExit(main())

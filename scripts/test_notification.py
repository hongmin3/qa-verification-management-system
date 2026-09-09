"""운영 알림 설정을 확인하고 테스트 메일을 보낸다.

    python scripts/test_notification.py              # 설정 상태만 확인 (메일 안 보냄)
    python scripts/test_notification.py --send       # 실제로 테스트 메일 1통 발송
    python scripts/test_notification.py --send --kind gemini_model_unavailable

SMTP 자격증명과 수신자는 `secrets.txt` 에 있다 (`secrets.example.txt` 형식 참고).
Gmail 은 2단계 인증을 켠 뒤 **앱 비밀번호**를 발급해 `SMTP_PASSWORD` 에 넣어야 한다 —
계정 비밀번호로는 SMTP 로그인이 되지 않는다.

`--send` 는 쿨다운을 우회한다. 실제 운영에서는 같은 문제로 `notifications.email.
cooldown_minutes` 안에 한 번만 나간다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.console import configure_stdout  # noqa: E402

configure_stdout()

from app.core import notifier  # noqa: E402
from app.core.notifier import KIND_LABELS, KIND_QUOTA_EXHAUSTED  # noqa: E402
from app.core.storage import Storage  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="운영 알림 설정 확인 / 테스트 발송")
    parser.add_argument("--send", action="store_true", help="실제로 메일을 보낸다 (쿨다운 무시)")
    parser.add_argument("--kind", default=KIND_QUOTA_EXHAUSTED, choices=sorted(KIND_LABELS), help="알림 종류")
    args = parser.parse_args()

    status = notifier.status()
    print("알림 설정 상태 (값은 표시하지 않습니다)")
    print(json.dumps(status, ensure_ascii=False, indent=2))

    if not status["configured"]:
        print("\n설정이 완료되지 않아 알림이 꺼진 상태입니다. secrets.txt 에 다음을 넣으세요:")
        print("  NOTIFY_EMAIL_TO=받는사람@example.com")
        print("  SMTP_USER=보내는계정@gmail.com")
        print("  SMTP_PASSWORD=앱비밀번호")
        print("\n이 상태에서도 분석은 정상 동작합니다 (알림만 나가지 않습니다).")
        return 1

    print("\n최근 알림 이력")
    rows = Storage().notification_log()
    print(json.dumps(rows, ensure_ascii=False, indent=2) if rows else "  (없음)")

    if not args.send:
        print("\n실제 발송은 --send 로 실행하세요.")
        return 0

    # 테스트는 쿨다운을 우회한다 — 설정을 고칠 때마다 기다릴 수 없다.
    storage = Storage()
    with storage.connect() as db:
        db.execute("DELETE FROM notifications WHERE kind=?", (args.kind,))
    result = notifier.notify(
        args.kind,
        "테스트 발송입니다. 실제 오류가 아닙니다. (scripts/test_notification.py)",
        context={"발송 경로": "scripts/test_notification.py"},
        storage=storage,
    )
    print(f"\n결과: {json.dumps(result, ensure_ascii=False)}")
    if result["status"] != "sent":
        print("발송에 실패했습니다. host/port/use_tls 와 SMTP 자격증명을 확인하세요.")
        return 1
    print("발송 성공. 받은 편지함(스팸함 포함)을 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

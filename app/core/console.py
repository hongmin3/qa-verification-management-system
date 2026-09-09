"""CLI 출력이 콘솔 인코딩 때문에 죽지 않게 한다.

Windows 콘솔의 기본 인코딩은 여전히 cp949 인 경우가 많다. 이 저장소의 메시지에는 `—`
(em dash) 처럼 cp949 에 없는 문자가 섞여 있어서, 그대로 `print` 하면 다음으로 죽는다.

    UnicodeEncodeError: 'cp949' codec can't encode character '\\u2014'

**작업 스케줄러가 돌리는 스크립트에서 이게 나면 그 주 동기화가 통째로 실패한다.** 실제로
`sync_product_knowledge.py --upload-to` 첫 실행이 이 오류로 멈췄다.

인코딩을 UTF-8 로 바꾸지 않고 `errors="replace"` 만 건다. 콘솔이 cp949 면 한글은 그대로
읽히고 `—` 만 `?` 가 된다 — UTF-8 로 바꿨다면 한글 전체가 깨져서 로그를 읽을 수 없다.
"""

from __future__ import annotations

import sys


def configure_stdout() -> None:
    """`print` 가 인코딩 때문에 예외를 던지지 않도록 만든다. 여러 번 불러도 안전하다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # 파이프로 감싸인 스트림 등
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            # 이미 닫혔거나 재설정을 지원하지 않는 스트림. 출력이 목적이지 이것이 목적이 아니다.
            pass

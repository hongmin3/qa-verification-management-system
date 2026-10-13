"""로그 설정. 콘솔 + 파일(logs/automation_YYYYMMDD.log) 동시 출력."""
from __future__ import annotations

import logging
import sys
from pathlib import Path


class RedactingFilter(logging.Filter):
    """토큰/비밀번호로 보이는 문자열이 로그에 그대로 찍히는 것을 방지."""

    REDACT_KEYS = ("token", "password", "authorization", "bearer")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage().lower()
        if any(k in msg for k in self.REDACT_KEYS) and "***" not in record.getMessage():
            record.msg = "[REDACTED - 민감정보 포함 가능성이 있어 로그에 기록하지 않음]"
            record.args = ()
        return True


def setup_logging(logs_dir: Path, run_date_str: str) -> logging.Logger:
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"automation_{run_date_str}.log"

    logger = logging.getLogger("srs_automation")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    fh.addFilter(RedactingFilter())

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    ch.addFilter(RedactingFilter())

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info("로그 파일: %s", log_file)
    return logger

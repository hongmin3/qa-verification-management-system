"""공통 유틸리티: 안전한 파일명, SRS ID/모듈 파싱, 날짜 포맷."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Polarion Work Item ID: 접두어-숫자. 자리수 제한을 두지 않는다 (VP-40 같은 2자리도 있음).
SRS_ID_RE = re.compile(r"^[A-Za-z]+-\d+$")

_WINDOWS_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str, max_len: int = 180) -> str:
    cleaned = _WINDOWS_FORBIDDEN.sub("_", name).strip().rstrip(". ")
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len]
    return cleaned or "unnamed"


def is_valid_srs_id(value: str) -> bool:
    return bool(SRS_ID_RE.match(value or ""))


def module_key_from_old_id(old_id: str | None) -> str | None:
    """'01-10-10' 또는 'SRS 01-10-10' 형태에서 최상위 모듈 번호('01')를 추출한다."""
    if not old_id:
        return None
    m = re.search(r"(\d{1,3})(?:-|$)", old_id.strip())
    if not m:
        return None
    return m.group(1).zfill(2)


def today_yymmdd(dt: datetime | None = None, fmt: str = "%y%m%d") -> str:
    dt = dt or datetime.now()
    return dt.strftime(fmt)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_pdf_filename(prefix: str, display_name: str, date_str: str) -> str:
    return safe_filename(f"{prefix}{display_name}({date_str}).pdf")


def atomic_write_bytes(dest: Path, data: bytes) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)

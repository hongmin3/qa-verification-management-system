"""VXvue Issue 보조 스크립트 공용 함수.

- 한글 결과는 cp949 콘솔을 거치지 않도록 UTF-8 파일로 저장하고, 콘솔에는 경로와 요약만 출력한다.
  (`python -I`는 PYTHONIOENCODING을 무시하므로 콘솔 인코딩에 기대지 않는다.)
- 추가 패키지는 시스템 Python을 바꾸지 않도록 작업 전용 폴더에 `pip --target`으로 설치한다.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

TOOL_HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "vxvue-issue-tools"
PYLIB = TOOL_HOME / "pylib"

# 작업 PC 기본 위치(운영 4, Section 9.6-8). Database는 C: 또는 D: 아래에 있을 수 있다.
# 명령행 옵션 --database 또는 각 폴더 옵션으로 바꿀 수 있다.
DATABASE_CANDIDATES = [Path(f"{d}:/Database") for d in "CD"]


def find_database(sub: str = "") -> Path:
    """sub(예: 'log/VSS.Vieworks')가 있는 첫 Database 폴더. 없으면 Database가 있는 첫 드라이브, 그래도 없으면 C:."""
    for root in DATABASE_CANDIDATES:
        if sub and (root / sub).is_dir():
            return root
    for root in DATABASE_CANDIDATES:
        if root.is_dir():
            return root
    return DATABASE_CANDIDATES[0]


DATABASE = find_database("log/VSS.Vieworks")
VIEWER_LOG_DIR = find_database("log/Viewer") / "log" / "Viewer"
VSS_LOG_DIR = find_database("log/VSS.Vieworks") / "log" / "VSS.Vieworks"
VSS_CONFIG_DIR = find_database("Integration/VSS.Vieworks") / "Integration" / "VSS.Vieworks"
PICPICK_DIR = Path.home() / "Documents" / "PicPick"
TERATERM_LOG = Path.home() / "Downloads" / "teraterm.log"


def say(text: str) -> None:
    """콘솔 인코딩이 한글을 못 쓰면 깨진 글자 대신 이스케이프로 출력한다."""
    try:
        sys.stdout.write(text + "\n")
    except UnicodeEncodeError:
        sys.stdout.write(text.encode("ascii", "backslashreplace").decode("ascii") + "\n")
    sys.stdout.flush()


def write_utf8(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_json(path: Path, value) -> Path:
    return write_utf8(path, json.dumps(value, ensure_ascii=False, indent=2, default=str))


def ensure_package(module: str, pip_names: list[str]):
    """module을 import한다. 없으면 PYLIB에 pip --target으로 설치한 뒤 다시 import한다."""
    if str(PYLIB) not in sys.path and PYLIB.is_dir():
        sys.path.insert(0, str(PYLIB))
    try:
        return __import__(module)
    except ImportError:
        pass
    PYLIB.mkdir(parents=True, exist_ok=True)
    say(f"[설치] {', '.join(pip_names)} -> {PYLIB} (pip --target, 시스템 Python 변경 없음)")
    subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--quiet",
                    "--target", str(PYLIB), *pip_names], check=True)
    if str(PYLIB) not in sys.path:
        sys.path.insert(0, str(PYLIB))
    return __import__(module)


# ---- 시각 해석 -----------------------------------------------------------------

_PICPICK = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ _](\d{2})[ _\-](\d{2})[ _\-](\d{2})")


def video_start_from_name(path: Path) -> datetime | None:
    """PicPick 파일명 `2026-11-12 13 55 44.mp4`의 시각 = 녹화 시작 시각(Section 9.6-3)."""
    m = _PICPICK.search(path.stem)
    return datetime(*map(int, m.groups())) if m else None


_VSS_NAME = re.compile(r"Vieworks_(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})")
_VSS_LINE = re.compile(r"^(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2}),(\d{3})")
_VIEWER_LINE = re.compile(r"^\ufeff?\[(\d{4})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
_TT_FULL = re.compile(r"^\[(\d{2})/(\d{2})/(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")
_TT_TIME = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\.(\d{3})\]")


def _dt(groups, two_digit_year=False) -> datetime:
    y, mo, d, h, mi, s, ms = map(int, groups)
    if two_digit_year:
        y += 2000
    return datetime(y, mo, d, h, mi, s, ms * 1000)


def vss_session_start(path: Path) -> datetime | None:
    m = _VSS_NAME.search(path.name)
    return datetime(*map(int, m.groups())) if m else None


def read_text_any(path: Path) -> str:
    """UTF-16(BOM) Viewer 로그와 ASCII/UTF-8 VSS·teraterm 로그를 모두 읽는다(Section 9.6-3)."""
    raw = read_shared(path)
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    if len(raw) > 3 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


def line_times(path: Path, kind: str):
    """(시각, 줄) 목록. kind = vss | viewer | teraterm. teraterm의 시각만 있는 줄은 직전 날짜를 쓴다."""
    out = []
    last_date = None
    for line in read_text_any(path).splitlines():
        if kind == "vss":
            m = _VSS_LINE.match(line)
            if m:
                out.append((_dt(m.groups()), line))
        elif kind == "viewer":
            m = _VIEWER_LINE.match(line)
            if m:
                out.append((_dt(m.groups()), line))
        else:
            m = _TT_FULL.match(line)
            if m:
                t = _dt(m.groups(), two_digit_year=True)
                last_date = t.date()
                out.append((t, line))
                continue
            m = _TT_TIME.match(line)
            if m and last_date:
                h, mi, s, ms = map(int, m.groups())
                out.append((datetime(last_date.year, last_date.month, last_date.day, h, mi, s, ms * 1000), line))
    return out


def content_range(path: Path, kind: str):
    times = [t for t, _ in line_times(path, kind)]
    return (min(times), max(times)) if times else (None, None)


def overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start is not None and a_end is not None and a_start <= b_end and b_start <= a_end


def fmt(t: datetime | None) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") if t else "-"


# ---- 쓰기 중인 파일 읽기 ------------------------------------------------------------

def read_shared(path: Path) -> bytes:
    """다른 프로세스가 쓰는 중인 로그도 읽도록 FILE_SHARE_READ|WRITE|DELETE로 연다(Windows)."""
    if os.name != "nt":
        return Path(path).read_bytes()
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = k32.CreateFileW
    create.restype = ctypes.c_void_p
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
                       ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    GENERIC_READ, SHARE_ALL, OPEN_EXISTING = 0x80000000, 0x7, 3
    handle = create(str(path), GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, 0x80, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError(ctypes.get_last_error(), f"공유 읽기 열기 실패: {path}")
    try:
        import msvcrt
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        k32.CloseHandle(ctypes.c_void_p(handle))
        raise
    with os.fdopen(fd, "rb") as fh:
        return fh.read()


def copy_shared(src: Path, dst: Path) -> dict:
    """공유 읽기로 복사하고 복사 시점 원본 크기·복사본 크기·복사 후 원본 크기를 돌려준다."""
    before = src.stat().st_size
    data = read_shared(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(data)
    after = src.stat().st_size
    return {"source": str(src), "copy": str(dst), "source_size_before": before, "read_size": len(data),
            "copy_size": dst.stat().st_size, "source_size_after": after,
            "match": dst.stat().st_size == len(data) == before,
            "note": "" if before == after else "복사 중 원본 크기 변화(쓰기 중) — 복사 시점 내용까지만 포함"}


def window(start: datetime, end: datetime, margin_min: float):
    pad = timedelta(minutes=margin_min)
    return start - pad, end + pad

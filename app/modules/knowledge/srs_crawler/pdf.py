"""HTML -> PDF 변환 (Playwright Chromium 인쇄, 별도 프로세스+시간제한) + PDF 검사(PyMuPDF).

일부 SRS Rich Text가 Chromium 인쇄 페이지네이션에서 수만 페이지로 무한 팽창하는
사례가 실제로 발견되어(정확한 HTML 원인은 특정하지 못함), 렌더링을 별도 프로세스
(`src/pdf_worker.py`)로 격리하고 시간제한을 두었다. 시간을 초과하면 해당 프로세스
트리를 강제 종료하고 실패로 처리한다 - 특정 SRS 하나 때문에 전체 자동화가
수십 분씩 멈추는 것을 막기 위함이다. 상위 호출부(`render.py`의 재시도 로직)는
이 실패를 신호로 받아 문제 SRS를 이분 탐색으로 찾아내 격리한다.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import fitz

from .logging_setup import RedactingFilter

logger = logging.getLogger("srs_automation")

DEFAULT_TIMEOUT_SECONDS = 90


@dataclass
class PdfResult:
    path: Path
    size_bytes: int
    page_count: int
    ok: bool
    error: str | None = None


def _kill_process_tree(pid: int) -> None:
    if sys.platform != "win32":
        return
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def html_to_pdf(html_path: Path, pdf_path: Path, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> PdfResult:
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout_seconds
    for attempt in (1, 2):
        if pdf_path.exists():
            pdf_path.unlink()
        # A temporary file avoids a full stderr pipe blocking the worker.
        with tempfile.TemporaryFile() as stderr_file:
            proc = subprocess.Popen(
                [sys.executable, "-m", "app.modules.knowledge.srs_crawler.pdf_worker", html_path.resolve().as_uri(), str(pdf_path)],
                cwd=str(Path(__file__).resolve().parents[4]),
                stderr=stderr_file,
            )
            try:
                returncode = proc.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                logger.error(
                    "PDF 생성 시간 초과(%ds) - 프로세스 강제 종료: %s (해당 그룹 안에 렌더링을 멈추게 하는 SRS가 있을 수 있음)",
                    timeout_seconds,
                    html_path,
                )
                _kill_process_tree(proc.pid)
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                return PdfResult(path=pdf_path, size_bytes=0, page_count=0, ok=False, error=f"timeout after {timeout_seconds}s")

            if returncode == 0:
                break
            stderr_file.seek(0)
            diagnostic = stderr_file.read().decode("utf-8", errors="replace").strip()
            # Check the entire message before truncating; secrets can precede the tail.
            if any(key in diagnostic.lower() for key in RedactingFilter.REDACT_KEYS):
                diagnostic = "[REDACTED - 민감정보 포함 가능성이 있어 오류 상세를 가림]"
            logger.warning(
                "PDF 변환 오류(시도=%d, exit=%d): %s\n%s",
                attempt, returncode, html_path, diagnostic[-4000:] or "상세 오류 없음",
            )
        if attempt == 2:
            return PdfResult(path=pdf_path, size_bytes=0, page_count=0, ok=False, error=f"worker exit code {returncode}")
        if time.monotonic() >= deadline:
            return PdfResult(path=pdf_path, size_bytes=0, page_count=0, ok=False, error=f"timeout after {timeout_seconds}s")
        logger.warning("PDF 변환 프로세스를 새로 시작해 한 번 재시도합니다: %s", html_path)

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        return PdfResult(path=pdf_path, size_bytes=0, page_count=0, ok=False, error="PDF 파일이 비어있거나 생성되지 않음")

    size = pdf_path.stat().st_size
    try:
        with fitz.open(pdf_path) as document:
            page_count = document.page_count
            if page_count < 1:
                raise ValueError("PDF has no pages")
    except Exception as exc:  # noqa: BLE001
        logger.error("PDF 페이지 수 확인 실패: %s - %s", pdf_path, exc)
        return PdfResult(path=pdf_path, size_bytes=size, page_count=0, ok=False, error=f"page count 확인 실패: {exc}")

    logger.info("PDF 생성 완료: %s (%.1fMB, %d pages)", pdf_path.name, size / 1024 / 1024, page_count)
    return PdfResult(path=pdf_path, size_bytes=size, page_count=page_count, ok=True)

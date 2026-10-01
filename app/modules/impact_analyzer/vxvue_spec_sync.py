"""VXvue 최신 사양서를 "ALM 사양서 최신화 크롤링" 프로젝트의 산출물에서 가져와 등록한다.

이 모듈은 그 크롤러 프로젝트의 코드나 설정(config/config.yaml, knowledge_folder 등— 그
프로젝트 AGENTS.md가 비공개로 지정)을 전혀 읽지 않고, 이미 공개된
output/<YYYY-MM-DD>/pdf/ 폴더만 읽는다.

`scripts/sync_vxvue_spec.py`(Windows 작업 스케줄러용 CLI)와 `app/modules/impact_analyzer/router.py`의
수동 "지금 동기화" 버튼이 동일하게 이 모듈의 run()을 호출한다 — 로직은 한 곳에만 있다.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.core.product_config import ProductConfig, load_product_config
from app.parsers.document_parser import extract_document_text

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DATE_SUFFIX_RE = re.compile(r"\(\d{6}\)(?=\.pdf$)", re.IGNORECASE)


def _base_name(name: str) -> str:
    """파일명에서 (YYMMDD) 리비전 날짜만 제거한 안정적인 키. 같은 문서의 서로 다른 주차
    리비전인지 판단하는 데 쓴다 (예: "...사양서1(260824).pdf"와 "...사양서1(260831).pdf")."""
    return DATE_SUFFIX_RE.sub("", name)


def _paths() -> tuple[Path, Path, Path]:
    root = get_settings().root
    return (root / "data" / "spec_sync.lock", root / "data" / "spec_sync_state.json", root / "output" / "logs" / "sync_vxvue_spec.log")


def _configure_logging() -> logging.Logger:
    _, _, log_file = _paths()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sync_vxvue_spec")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


def is_available_on_this_host(config: ProductConfig | None = None) -> bool:
    """크롤러 output 폴더에 이 호스트에서 접근 가능한지 (Windows 전용, 서버에서는 보통 False)."""
    config = config or load_product_config("vxvue")
    if config is None or config.specification.source != "alm_crawler":
        return False
    return Path(config.specification.crawler_output_dir).is_dir()


def _latest_date_dir(output_dir: Path) -> Path | None:
    if not output_dir.is_dir():
        return None
    candidates = [p for p in output_dir.iterdir() if p.is_dir() and DATE_DIR_RE.match(p.name)]
    return max(candidates, key=lambda p: p.name) if candidates else None


def _matching_pdfs(pdf_dir: Path, patterns: list[str]) -> list[Path]:
    if not pdf_dir.is_dir():
        return []
    found: dict[str, Path] = {}
    for pattern in patterns:
        for path in sorted(pdf_dir.glob(pattern)):
            found[path.name] = path
    return list(found.values())


def _load_state(state_file: Path) -> dict:
    if state_file.is_file():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_state(state_file: Path, state: dict) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _file_signature(path: Path) -> dict:
    stat = path.stat()
    return {"size": stat.st_size, "mtime": stat.st_mtime}


def _register_specification(client: httpx.Client, target_url: str, path: Path, product: str, version: str) -> None:
    with path.open("rb") as handle:
        response = client.post(
            f"{target_url}/knowledge/specification",
            data={"product": product, "version": version, "source": "alm_crawler"},
            files={"file": (path.name, handle, "application/pdf")},
            follow_redirects=False,
            timeout=60,
        )
    if response.status_code not in (200, 303):
        raise RuntimeError(f"{path.name} 등록 실패: HTTP {response.status_code}")


def _replace_stale_revisions(client: httpx.Client, target_url: str, product: str, new_name: str, logger: logging.Logger) -> int:
    """새로 올린 문서와 같은 논리적 문서(파일명에서 날짜만 다름)의 이전 리비전을 지운다.

    "다중 문서 관리"(서로 다른 문서는 전부 유지)와는 별개로, 동기화로 들어오는 파일은 매주
    같은 문서의 새 리비전이므로 옛 날짜본을 무한히 쌓아두지 않는다."""
    target_base = _base_name(new_name)
    try:
        response = client.get(f"{target_url}/knowledge/documents", params={"kind": "specification", "product": product}, timeout=15)
        response.raise_for_status()
        existing = response.json()
    except httpx.HTTPError as exc:
        logger.warning("기존 문서 목록 조회 실패 (구버전 정리 건너뜀): %s", exc)
        return 0
    removed = 0
    for doc in existing:
        if doc["name"] == new_name:
            continue
        if _base_name(doc["name"]) != target_base:
            continue
        try:
            client.post(f"{target_url}/knowledge/delete/{doc['id']}", data={"source": "alm_crawler"}, timeout=15, follow_redirects=False)
            removed += 1
            logger.info("이전 리비전 삭제: %s (id=%s)", doc["name"], doc["id"])
        except httpx.HTTPError as exc:
            logger.warning("이전 리비전 삭제 실패: %s (%s)", doc["name"], exc)
    return removed


def adapter_product() -> str:
    """이 ALM 수집 어댑터가 맡는 제품의 표시 이름 (REQ-KNOW-018). 공통 Knowledge 코드는 제품 이름을 적지 않고 이 값을 쓴다."""
    config = load_product_config("vxvue")
    return config.product if config else ""


def report_sync_log(target_url: str, product: str, kind: str, source: str, status: str, detail: str, logger: logging.Logger | None = None) -> None:
    """원격 서버의 sync 로그에 결과를 보고한다 (동일 프로세스가 아닐 때만 필요 — HTTP 경유)."""
    try:
        with httpx.Client() as client:
            client.post(
                f"{target_url}/knowledge/sync-log",
                data={"product": product, "kind": kind, "source": source, "status": status, "detail": detail},
                timeout=15,
            )
    except httpx.HTTPError as exc:
        (logger or _configure_logging()).warning("sync-log 보고 실패 (동기화 자체는 %s로 진행됨): %s", status, exc)


def run(target_url: str, dry_run: bool = False) -> dict:
    """반환값: {"status": ..., "detail": ...} — 실패해도 예외를 올리지 않고 기존 데이터는 그대로 둔다.

    이 함수는 sync 로그를 스스로 기록하지 않는다 (호출자가 직접 DB에 쓸 수 있으면 Storage로,
    원격 서버 대상이면 report_sync_log()로 별도 보고한다).
    """
    logger = _configure_logging()
    lock_file, state_file, _ = _paths()
    config = load_product_config("vxvue")
    if config is None or config.specification.source != "alm_crawler":
        return {"status": "NEEDS_CONFIG", "detail": "config/products/vxvue.yaml에 specification.source=alm_crawler 설정이 없습니다."}

    crawler_output = Path(config.specification.crawler_output_dir)
    latest_dir = _latest_date_dir(crawler_output)
    if latest_dir is None:
        detail = f"크롤러 output 폴더에서 날짜 폴더를 찾지 못했습니다: {crawler_output}"
        logger.error(detail)
        return {"status": "FAILED", "detail": detail}

    pdfs = _matching_pdfs(latest_dir / "pdf", config.specification.filename_patterns)
    if not pdfs:
        detail = f"최신 폴더 {latest_dir}에서 조건에 맞는 PDF를 찾지 못했습니다."
        logger.warning(detail)
        return {"status": "SUCCESS", "detail": detail}

    date_tag = latest_dir.name
    root = get_settings().root
    original_dir = root / "data" / "specifications" / "vxvue" / "original" / date_tag
    normalized_dir = root / "data" / "specifications" / "vxvue" / "normalized" / date_tag

    state = _load_state(state_file)
    changed = [pdf for pdf in pdfs if state.get(pdf.name) != _file_signature(pdf)]
    unchanged = len(pdfs) - len(changed)
    logger.info("최신 날짜=%s, 대상 PDF=%d건, 변경=%d건, 미변경=%d건, dry_run=%s", date_tag, len(pdfs), len(changed), unchanged, dry_run)

    if dry_run:
        for pdf in changed:
            logger.info("[dry-run] 변경 감지: %s", pdf.name)
        return {"status": "DRY_RUN", "detail": f"변경 {len(changed)}건 감지 (미실행)"}

    if not changed:
        return {"status": "SUCCESS", "detail": f"변경 없음 (미변경 {unchanged}건, 날짜 {date_tag})"}

    original_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)
    uploaded, failed, replaced_total = [], [], 0
    with httpx.Client() as client:
        for pdf in changed:
            try:
                shutil.copy2(pdf, original_dir / pdf.name)
                text = extract_document_text(pdf)
                (normalized_dir / f"{pdf.stem}.md").write_text(text, encoding="utf-8")
                _register_specification(client, target_url, pdf, config.product, config.version)
                replaced_total += _replace_stale_revisions(client, target_url, config.product, pdf.name, logger)
                state[pdf.name] = _file_signature(pdf)
                uploaded.append(pdf.name)
                logger.info("등록 완료: %s", pdf.name)
            except Exception:
                failed.append(pdf.name)
                logger.exception("등록 실패: %s", pdf.name)

        status = "SUCCESS" if not failed else ("PARTIAL" if uploaded else "FAILED")
        detail = f"업로드 {len(uploaded)}건, 이전 리비전 삭제 {replaced_total}건, 실패 {len(failed)}건, 미변경 {unchanged}건 (날짜 {date_tag})"
        if failed:
            detail += f" / 실패 파일: {', '.join(failed)}"

    _save_state(state_file, state)
    logger.info("동기화 종료: %s", detail)
    return {"status": status, "detail": detail}


def acquire_lock() -> bool:
    lock_file, _, _ = _paths()
    if lock_file.exists():
        return False
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    return True


def release_lock() -> None:
    lock_file, _, _ = _paths()
    lock_file.unlink(missing_ok=True)

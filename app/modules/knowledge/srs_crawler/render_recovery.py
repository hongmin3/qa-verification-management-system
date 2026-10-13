"""PDF 렌더링 시간 초과 시 문제 SRS를 자동으로 찾아 격리하는 복구 로직.

실제 운영 중 특정 SRS 하나의 Rich Text 콘텐츠가 Chromium 인쇄 페이지네이션에서
수만 페이지로 무한 팽창하는 사례가 발견되었으나, 정확한 HTML 원인 요소는
특정하지 못했다. 원인을 모르는 상태에서도 안전하게 동작하도록, 그룹 렌더링이
시간 초과되면 레코드를 절반씩 나눠 재귀적으로 시도해 문제를 일으키는 SRS를
찾아내고(이분 탐색), 그 SRS만 서식을 제거한 순수 텍스트로 대체한 뒤 전체를
다시 렌더링한다. 어떤 SRS도 문서에서 통째로 빠지지 않는다 - 서식만 낮아질 뿐이다.
"""
from __future__ import annotations

import html as html_lib
import logging
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

from .partition import FileGroup
from .pdf import PdfResult, html_to_pdf
from .problem_state import ProblemState
from .render import render_group_html

logger = logging.getLogger("srs_automation")

DEFAULT_ATTEMPT_TIMEOUT = 300
DEFAULT_MAX_BISECT_DEPTH = 8
# 이분 탐색으로 개별 문제 SRS를 찾지 못한 경우 = 폭주가 아니라 그룹 전체가 느린 경우.
# 이때는 실패로 처리하지 않고 시간제한을 늘려 한 번 더 시도한다.
SLOW_GROUP_TIMEOUT_FACTOR = 3


def _plaintext_fallback(rec: dict[str, Any]) -> None:
    """복잡한 서식(중첩 리스트 등 렌더링 폭주를 유발한 것으로 추정되는 구조)만 제거하고,
    본문 텍스트와 이미지는 최대한 그대로 보존한다 - SRS 자체를 문서에서 빼지 않는다."""
    soup = BeautifulSoup(rec.get("content_html") or "", "html.parser")
    text = soup.get_text("\n").strip()
    escaped = html_lib.escape(text).replace("\n", "<br/>")

    # 이미 로컬 file:// 경로로 치환되어 있던 이미지는 단순 레이아웃으로 그대로 유지한다.
    img_html = "".join(f'<div style="margin:6px 0;"><img src="{img["src"]}" style="max-width:100%;"/></div>' for img in soup.find_all("img") if img.get("src"))

    rec["content_html"] = (
        '<div style="border:2px solid #CC0000;background:#FFF5F5;padding:8px;">'
        "<b>[자동 렌더링 오류로 상세 서식(중첩 목록 등)만 제거되었습니다 - 텍스트/이미지는 보존됨. "
        "정확한 서식은 Polarion 원본을 확인하세요]</b>"
        f"<br/><br/>{escaped}{img_html}</div>"
    )
    rec["render_fallback_applied"] = True
    rec.setdefault("render_fallback_reason", "bisect_timeout")


def _try_render(
    records: list[dict], display_name: str, project_label: str, tmp_dir: Path, tag: str, timeout_seconds: int
) -> bool:
    group = FileGroup(file_key=tag, display_name=display_name, records=records)
    html_str = render_group_html(group, project_label=project_label)
    tmp_html = tmp_dir / f"{tag}.html"
    tmp_pdf = tmp_dir / f"{tag}.pdf"
    tmp_html.write_text(html_str, encoding="utf-8")
    result = html_to_pdf(tmp_html, tmp_pdf, timeout_seconds=timeout_seconds)
    return result.ok


def _isolate(
    records: list[dict],
    display_name: str,
    project_label: str,
    tmp_dir: Path,
    depth: int,
    max_depth: int,
    timeout_seconds: int,
    problematic: list[dict],
) -> None:
    if len(records) <= 1 or depth >= max_depth:
        problematic.extend(records)
        for r in records:
            logger.error("문제 SRS로 격리됨(렌더링 시간 초과): %s - %s", r.get("uid"), r.get("title"))
        return
    mid = len(records) // 2
    for idx, chunk in enumerate([records[:mid], records[mid:]]):
        if not chunk:
            continue
        tag = f"bisect_d{depth}_{idx}_{abs(hash(tuple(r['uid'] for r in chunk)))}"
        if not _try_render(chunk, display_name, project_label, tmp_dir, tag, timeout_seconds):
            _isolate(chunk, display_name, project_label, tmp_dir, depth + 1, max_depth, timeout_seconds, problematic)


def _apply_known_problem_cache(
    group: FileGroup,
    known_problem_srs: set[str],
    problem_state: ProblemState | None,
    recheck_known: bool,
    timeout_seconds: int,
    tmp_dir_factory,
) -> list[dict]:
    """이미 문제로 확인된 SRS 중 '본문이 그대로인' 것만 이분 탐색 없이 즉시 격리한다.

    본문이 바뀐 SRS는 격리하지 않고 정상 렌더링 경로로 흘려보낸다 - 수정으로 렌더링이
    정상화되었을 수 있으므로, 그 기회를 놓치지 않기 위해 매번 재확인한다.
    """
    if recheck_known or not known_problem_srs:
        return []

    pre_isolated: list[dict] = []
    stale: list[dict] = []
    for rec in group.records:
        uid = rec.get("uid")
        if uid not in known_problem_srs:
            continue
        if problem_state is not None and not problem_state.is_still_valid(uid, rec.get("content_html")):
            stale.append(rec)
            continue
        rec["render_fallback_reason"] = "known_problem_cache"
        _plaintext_fallback(rec)
        pre_isolated.append(rec)
        logger.info(
            "[%s] %s - 이미 확인된 렌더링 문제 SRS이고 본문 변경이 없어 이분 탐색을 생략하고 "
            "서식 단순화를 적용합니다.",
            group.display_name,
            uid,
        )

    # 본문(또는 렌더링 파이프라인)이 바뀐 기존 문제 SRS는 반드시 재확인해야 한다. 다만
    # 그룹 전체 이분 탐색으로 다시 찾는 것은 낭비다 - 어느 SRS를 의심해야 하는지 이미
    # 알고 있으므로 **그 SRS만 단독으로 렌더링**해 판정한다.
    # (실측: 2026-08-24 실행에서 VP-1277 본문이 바뀌어 이분 탐색이 다시 돌았고
    #  depth 6까지 내려가며 전체 실행이 48분 걸렸다. 단독 검증은 1회 시도로 끝난다.)
    if stale:
        pre_isolated.extend(
            _recheck_stale_known_problems(stale, group, problem_state, timeout_seconds, tmp_dir_factory())
        )
    return pre_isolated


def _recheck_stale_known_problems(
    stale: list[dict],
    group: FileGroup,
    problem_state: ProblemState | None,
    timeout_seconds: int,
    tmp_dir: Path,
) -> list[dict]:
    """본문/파이프라인이 바뀐 기존 문제 SRS를 단독 렌더링으로 재판정한다."""
    project_label = group.records[0].get("project_id", "") if group.records else ""
    isolated: list[dict] = []
    for rec in stale:
        uid = rec.get("uid")
        logger.warning(
            "[%s] %s 는 렌더링 문제로 등록되어 있으나 본문/렌더링 파이프라인이 변경되었습니다 "
            "- 이분 탐색 대신 해당 SRS만 단독 렌더링해 재확인합니다.",
            group.display_name,
            uid,
        )
        tag = f"recheck_{str(uid).replace('/', '_')}"
        if _try_render([rec], group.display_name, project_label, tmp_dir, tag, timeout_seconds):
            logger.info(
                "[%s] %s - 단독 렌더링 정상 통과. 서식을 유지하며 정상 렌더링합니다. "
                "config의 render.known_problem_srs에서 제거하는 것을 검토하세요.",
                group.display_name,
                uid,
            )
            if problem_state is not None:
                problem_state.forget(uid)
            continue

        logger.error(
            "[%s] %s - 단독 렌더링도 시간 초과(%ds). 여전히 문제 SRS로 판정하고 서식을 단순화합니다.",
            group.display_name,
            uid,
            timeout_seconds,
        )
        rec["render_fallback_reason"] = "known_problem_recheck"
        _plaintext_fallback(rec)
        isolated.append(rec)
    return isolated


def render_group_pdf_with_recovery(
    group: FileGroup,
    *,
    project_label: str,
    html_dir: Path,
    pdf_path: Path,
    timeout_seconds: int = DEFAULT_ATTEMPT_TIMEOUT,
    max_depth: int = DEFAULT_MAX_BISECT_DEPTH,
    known_problem_srs: set[str] | None = None,
    problem_state: ProblemState | None = None,
    recheck_known: bool = False,
) -> tuple[PdfResult, list[dict]]:
    """정상 렌더링을 먼저 시도하고, 시간 초과 시 이분 탐색으로 문제 SRS를 격리 후 재시도한다.

    `known_problem_srs`에 등록된 SRS는 본문이 이전에 문제로 확인된 시점과 동일할 때만
    이분 탐색을 생략한다(본문이 바뀌면 다시 정상 렌더링을 시도한다).

    반환값: (최종 PdfResult, 격리되어 서식이 단순화된 레코드 목록)
    """
    known = set(known_problem_srs or ())
    # fallback이 content_html을 덮어쓰기 전에 원본 해시 기준값을 확보해 둔다.
    original_content = {r.get("uid"): r.get("content_html") for r in group.records}

    def _tmp_dir() -> Path:
        d = html_dir / f"_recovery_{group.file_key}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    pre_isolated = _apply_known_problem_cache(
        group, known, problem_state, recheck_known, timeout_seconds, _tmp_dir
    )

    main_html_path = html_dir / f"{group.file_key}.html"
    main_html_path.write_text(render_group_html(group, project_label=project_label), encoding="utf-8")

    result = html_to_pdf(main_html_path, pdf_path, timeout_seconds=timeout_seconds)
    if result.ok:
        _sync_state(group, known, pre_isolated, problem_state, original_content, result.page_count)
        return result, pre_isolated
    if not result.error or "timeout" not in result.error:
        # 타임아웃이 아닌 다른 실패(디스크/권한 등)는 이분 탐색으로 해결되지 않는다.
        return result, pre_isolated

    logger.warning("[%s] 전체 렌더링 시간 초과(%ds) - 문제 SRS 이분 탐색 시작", group.display_name, timeout_seconds)
    tmp_dir = html_dir / f"_recovery_{group.file_key}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    problematic: list[dict] = []
    _isolate(list(group.records), group.display_name, project_label, tmp_dir, 0, max_depth, timeout_seconds, problematic)

    if not problematic:
        # 절반씩 나눈 두 덩어리가 모두 정상 렌더링되었다는 것은, 특정 SRS가 페이지네이션을
        # 폭주시킨 것이 아니라 그룹 전체가 제한시간에 근접하게 느렸다는 뜻이다(Task Scheduler로
        # 실행되면 우선순위가 낮아 대화형 실행보다 느려진다). 실패로 처리하지 않고 시간제한을
        # 늘려 한 번 더 시도한다.
        extended = timeout_seconds * SLOW_GROUP_TIMEOUT_FACTOR
        logger.warning(
            "[%s] 이분 탐색에서 개별 문제 SRS가 발견되지 않았습니다 - 렌더링 폭주가 아니라 "
            "그룹 전체가 느린 경우로 보고 시간제한을 %ds로 늘려 재시도합니다.",
            group.display_name,
            extended,
        )
        retry_result = html_to_pdf(main_html_path, pdf_path, timeout_seconds=extended)
        if retry_result.ok:
            logger.info("[%s] 시간제한 연장 후 정상 생성됨 - config의 render.pdf_timeout_seconds 상향을 검토하세요.", group.display_name)
        else:
            logger.error("[%s] 시간제한을 %ds로 늘려도 실패 - PDF 생성 실패로 처리", group.display_name, extended)
        _sync_state(group, known, pre_isolated, problem_state, original_content, retry_result.page_count)
        return retry_result, pre_isolated

    for rec in problematic:
        _plaintext_fallback(rec)

    logger.info("[%s] 문제 SRS %d건 격리 완료(%s) - 최종 재렌더링", group.display_name, len(problematic), [r["uid"] for r in problematic])
    for rec in problematic:
        if rec.get("uid") not in known:
            logger.warning(
                "config의 render.known_problem_srs 에 \"%s\" 를 추가하면 다음 실행에서 "
                "이분 탐색(약 %d분)을 생략할 수 있습니다.",
                rec.get("uid"),
                max(1, round(timeout_seconds * max_depth / 60)),
            )
    main_html_path.write_text(render_group_html(group, project_label=project_label), encoding="utf-8")
    final_result = html_to_pdf(main_html_path, pdf_path, timeout_seconds=timeout_seconds)

    isolated = pre_isolated + [r for r in problematic if r not in pre_isolated]
    _sync_state(group, known, isolated, problem_state, original_content, final_result.page_count)
    return final_result, isolated


def _sync_state(
    group: FileGroup,
    known: set[str],
    isolated: list[dict],
    problem_state: ProblemState | None,
    original_content: dict[str | None, str | None],
    page_count: int | None,
) -> None:
    """격리된 SRS는 '문제로 확인된 본문 해시'를 갱신하고, 재확인 결과 정상 렌더링된
    기존 등록 SRS는 상태에서 제거해 config 정리를 안내한다."""
    if problem_state is None:
        return

    isolated_uids = {r.get("uid") for r in isolated}
    for rec in isolated:
        uid = rec.get("uid")
        if uid is None:
            continue
        problem_state.record(
            uid,
            content_html=original_content.get(uid),
            title=rec.get("title"),
            page_count=page_count,
        )

    for rec in group.records:
        uid = rec.get("uid")
        if uid in known and uid not in isolated_uids:
            logger.warning(
                "[%s] %s 는 known_problem_srs로 등록되어 있으나 이번 실행에서는 정상 렌더링되었습니다 "
                "- config에서 제거하는 것을 검토하세요.",
                group.display_name,
                uid,
            )
            problem_state.forget(uid)

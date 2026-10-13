"""Polarion Rich Text(HTML) 정규화.

처리하는 것:
1. 위험 태그/속성 제거(script, iframe, on* 이벤트, javascript: 링크) - XSS 방지
2. `workitemimg:파일명` 형태의 임베드 이미지 참조를 실제 첨부파일 다운로드 후
   로컬 상대경로로 치환 (다운로드 실패는 조용히 무시하지 않고 실패 목록으로 반환)
3. `<span class="polarion-rte-link" data-item-id="VP-xxx">` 형태의 Work Item
   참조는 Polarion 웹 UI의 자바스크립트가 화면에서 채워 넣는 빈 placeholder이므로,
   그대로 두면 PDF에서 빈 링크가 된다. 대상 항목의 ID/제목을 채운 하이퍼링크로 치환한다.

취소선(text-decoration: line-through), 밑줄(underline), bold/italic/색상/표 등의
inline style은 절대 제거하지 않고 그대로 보존한다 - VXvue 검증에서 서식 변경 자체가
중요한 정보이기 때문이다.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger("srs_automation")

DANGEROUS_TAGS = {"script", "iframe", "object", "embed", "form"}
WORKITEMIMG_PREFIX = "workitemimg:"
_WORKITEM_HREF_RE = re.compile(r"workitem\?id=([A-Za-z0-9_\-]+)")


@dataclass
class RichTextResult:
    html: str
    images: list[dict] = field(default_factory=list)  # {filename, local_path, ok, error}
    links: list[dict] = field(default_factory=list)  # {target_id, resolved, title}


def _sanitize(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(list(DANGEROUS_TAGS)):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs.keys()):
            if attr.lower().startswith("on"):
                del tag.attrs[attr]
        href = tag.attrs.get("href")
        if href and href.strip().lower().startswith("javascript:"):
            del tag.attrs["href"]


def normalize_rich_text(
    html: str | None,
    *,
    resolve_image: Callable[[str], tuple[Path | None, str | None]],
    resolve_link_title: Callable[[str], str | None],
) -> RichTextResult:
    """html: Polarion description/descriptionKR 필드의 원본 HTML.

    resolve_image(filename) -> (다운로드된 로컬 파일 경로 또는 None, 에러 메시지 또는 None)
    resolve_link_title(item_id) -> 대상 SRS의 제목(찾지 못하면 None)
    """
    if not html or not html.strip():
        return RichTextResult(html="")

    soup = BeautifulSoup(html, "html.parser")
    _sanitize(soup)

    result = RichTextResult(html="")

    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith(WORKITEMIMG_PREFIX):
            # Rich Text 안의 workitemimg: 참조는 URL-인코딩된 파일명(한글 포함)을 쓰지만,
            # 첨부파일 목록 API의 filename은 인코딩되지 않은 원문이라 그대로면 매칭에 실패한다.
            filename = unquote(src[len(WORKITEMIMG_PREFIX):])
            local_path, err = resolve_image(filename)
            if local_path is not None:
                # 그룹 HTML/PDF가 어디서 렌더링되든 이미지가 항상 해석되도록 절대 file:// URI를 사용한다.
                # (상대경로 "images/파일명"을 쓰면 서로 다른 SRS가 같은 첨부파일 번호를 재사용할 때
                #  파일명이 충돌할 수 있어, 항목별 폴더로 격리된 절대경로를 그대로 참조한다.)
                img["src"] = local_path.resolve().as_uri()
                result.images.append({"filename": filename, "local_path": str(local_path), "ok": True, "error": None})
            else:
                # 깨진 링크를 그대로 남기지 않는다 - 실패를 화면에서도 보이게 표시
                placeholder = soup.new_tag("span")
                placeholder.string = f"[이미지 로드 실패: {filename}]"
                placeholder["style"] = "color:#CC0000;border:1px dashed #CC0000;padding:2px 4px;"
                img.replace_with(placeholder)
                result.images.append({"filename": filename, "local_path": None, "ok": False, "error": err})
                logger.warning("이미지 다운로드 실패: %s (%s)", filename, err)
        elif src.startswith("/polarion/"):
            # Polarion 서버 상대경로를 가리키는 장식용 타입 아이콘(예: srs 아이콘) - 로컬에서
            # 해석 불가능하므로 깨진 이미지 아이콘으로 남기지 않고 제거한다 (콘텐츠 손실 아님).
            img.decompose()

    for link_span in soup.find_all("span", attrs={"class": "polarion-rte-link"}):
        item_id = link_span.get("data-item-id")
        if not item_id:
            continue
        title = resolve_link_title(item_id)
        a_tag = soup.new_tag("a", href=f"#srs-{item_id}")
        a_tag["class"] = "srs-ref-link"
        a_tag.string = f"{item_id} - {title}" if title else f"{item_id} (참조 대상 확인 불가)"
        link_span.replace_with(a_tag)
        result.links.append({"target_id": item_id, "resolved": title is not None, "title": title})
        if title is None:
            logger.warning("Work Item 참조 대상을 찾지 못함: %s", item_id)

    # Polarion 원문에는 <span class="polarion-rte-link">가 아니라, 이미 아이콘+텍스트가
    # 채워진 <a href="/polarion/#/project/.../workitem?id=VP-xxx"> 형태의 링크도 섞여 있다.
    # 이 href를 그대로 두면 로컬 PDF에서 "file:///C:/polarion/#/..." 같은 깨진 경로로
    # 열리므로, 같은 문서 안에서 실제 SRS 섹션으로 이동하는 내부 앵커로 바꾼다.
    for a_tag in soup.find_all("a", href=True):
        m = _WORKITEM_HREF_RE.search(a_tag["href"])
        if not m:
            continue
        item_id = m.group(1)
        a_tag["href"] = f"#srs-{item_id}"
        title = resolve_link_title(item_id)
        result.links.append({"target_id": item_id, "resolved": title is not None, "title": title})

    result.html = str(soup)
    return result

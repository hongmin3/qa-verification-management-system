"""Word Track Changes(<w:ins>/<w:del>/<w:moveFrom>/<w:moveTo>) 구조화 추출.

document_parser.py와 동일하게 python-docx 없이 zipfile+ElementTree로 word/document.xml만
읽는다 — 순수 추출(읽기)만 하는 이 모듈은 그걸로 충분하다. Word Comment 삽입(쓰기)은
python-docx가 필요해(`comment_writer.py`) 별도 의존성으로 추가돼 있다. 단순 서식/페이지
번호 등 NON_FUNCTIONAL_CHANGE 필터링, section/heading 매핑도 이 모듈 범위 밖이며 이
모듈은 순수 추출만 담당한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_KIND_BY_TAG = {
    f"{W_NS}ins": "insertion",
    f"{W_NS}del": "deletion",
    f"{W_NS}moveFrom": "move_from",
    f"{W_NS}moveTo": "move_to",
}
# 최종 승인본(plain_text)에 남는 종류 — 삽입과 이동 도착지만 포함하고, 삭제와 이동 출발지는 제외한다.
_KEPT_IN_PLAIN_TEXT = {"insertion", "move_to"}


@dataclass
class TrackedChange:
    kind: str  # insertion | deletion | move_from | move_to
    author: str
    date: str
    text: str
    paragraph_index: int
    source_page: int | None = None
    review_required: bool = False
    # 같은 문단(paragraph_index) 안에서 이 변경이 몇 번째 <w:ins>/<w:del>/<w:moveFrom>/<w:moveTo>
    # 인지(0부터). comment_writer.py가 문단 전체가 아니라 이 변경만 정확히 앵커링하는 데 쓴다.
    change_index_in_paragraph: int = 0
    # 이 변경보다 앞에 나온 마지막 제목 문단의 글자와, 변경을 모두 받아들였을 때 이 문단에 남는
    # 글자 (REQ-MANUAL-005 7번). 결과 화면 위치·AI 맥락·문장 점검(REQ-MANUAL-020)에 쓴다.
    section_heading: str = ""
    paragraph_text: str = ""


@dataclass
class TrackChangesResult:
    changes: list[TrackedChange] = field(default_factory=list)
    plain_text: str = ""


_BREAK_TAGS = {f"{W_NS}br": "\n", f"{W_NS}cr": "\n", f"{W_NS}tab": "\t"}


def _text_of(element: ET.Element) -> str:
    """<w:r> 하위의 <w:t>/<w:delText>를 문서 순서대로 이어붙인다. 줄바꿈(<w:br>/<w:cr>)과 탭(<w:tab>)은
    공백 문자로 남긴다 — 빼면 "지정합니다.<줄바꿈>저장할" 이 붙은 문장처럼 보인다 (REQ-MANUAL-020)."""
    parts: list[str] = []
    for node in element.iter():
        if node.tag in (f"{W_NS}t", f"{W_NS}delText"):
            parts.append(node.text or "")
        elif node.tag in _BREAK_TAGS:
            parts.append(_BREAK_TAGS[node.tag])
    return "".join(parts)


def _has_image(element: ET.Element) -> bool:
    return any(node.tag.rsplit("}", 1)[-1] in {"drawing", "pict", "blip"} for node in element.iter())


def _image_description(element: ET.Element) -> str:
    for node in element.iter():
        if node.tag.rsplit("}", 1)[-1] == "docPr":
            return node.get("descr") or node.get("name") or ""
    return ""


_HEADING_NAME_RE = re.compile(r"^(heading|제목)\s*[1-9]$", re.IGNORECASE)


def heading_style_ids(styles_xml: bytes | None) -> set[str]:
    """제목 문단 스타일 id 목록. 이름이 `heading N`/`제목 N` 인 문단 스타일과, 그것을 바탕(basedOn)으로
    만든 문단 스타일이다. 개요 수준(outlineLvl)은 보지 않는다 — 실제 VXvue 매뉴얼은 본문 번호 목록
    스타일에도 개요 수준을 둔다 (REQ-MANUAL-005 주의)."""
    if not styles_xml:
        return set()
    root = ET.fromstring(styles_xml)
    names: dict[str, str] = {}
    based_on: dict[str, str | None] = {}
    for style in root.iter(f"{W_NS}style"):
        if style.get(f"{W_NS}type") != "paragraph":
            continue
        style_id = style.get(f"{W_NS}styleId") or ""
        name = style.find(f"{W_NS}name")
        names[style_id] = name.get(f"{W_NS}val", "") if name is not None else ""
        base = style.find(f"{W_NS}basedOn")
        based_on[style_id] = base.get(f"{W_NS}val") if base is not None else None

    def is_heading(style_id: str | None, depth: int = 0) -> bool:
        if not style_id or depth > 20 or style_id not in names:
            return False
        return bool(_HEADING_NAME_RE.match(names[style_id].strip())) or is_heading(based_on.get(style_id), depth + 1)

    return {style_id for style_id in names if is_heading(style_id)}


def _paragraph_style(paragraph: ET.Element) -> str:
    style = paragraph.find(f"{W_NS}pPr/{W_NS}pStyle")
    return style.get(f"{W_NS}val", "") if style is not None else ""


def extract_track_changes_from_xml(xml_bytes: bytes, heading_styles: set[str] | None = None) -> TrackChangesResult:
    root = ET.fromstring(xml_bytes)
    heading_styles = heading_styles or set()
    result = TrackChangesResult()
    plain_lines: list[str] = []
    section_heading = ""
    for paragraph_index, paragraph in enumerate(root.iter(f"{W_NS}p")):
        paragraph_plain: list[str] = []
        paragraph_changes: list[TrackedChange] = []
        change_index_in_paragraph = 0
        for child in paragraph:
            kind = _KIND_BY_TAG.get(child.tag)
            if kind:
                text = _text_of(child)
                has_image = _has_image(child)
                if has_image and not text.strip():
                    description = _image_description(child)
                    text = f"이미지 변경{f' ({description})' if description else ''}"
                paragraph_changes.append(
                    TrackedChange(
                        kind=f"image_{kind}" if has_image else kind,
                        author=child.get(f"{W_NS}author", ""),
                        date=child.get(f"{W_NS}date", ""),
                        text=text,
                        paragraph_index=paragraph_index,
                        review_required=has_image,
                        change_index_in_paragraph=change_index_in_paragraph,
                        section_heading=section_heading,
                    )
                )
                change_index_in_paragraph += 1
                if kind in _KEPT_IN_PLAIN_TEXT and not has_image:
                    paragraph_plain.append(text)
            elif child.tag == f"{W_NS}r":
                paragraph_plain.append(_text_of(child))
        line = "".join(paragraph_plain).strip()
        if line:
            plain_lines.append(line)
        if heading_styles and line and _paragraph_style(paragraph) in heading_styles:
            # 제목 문단 안의 변경은 그 제목 자신을 절 제목으로 본다.
            section_heading = line
            for change in paragraph_changes:
                change.section_heading = line
        for change in paragraph_changes:
            change.paragraph_text = line
        result.changes.extend(paragraph_changes)
    result.plain_text = "\n".join(plain_lines)
    return result


def extract_track_changes(path: Path) -> TrackChangesResult:
    with ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
        styles_xml = archive.read("word/styles.xml") if "word/styles.xml" in archive.namelist() else None
    return extract_track_changes_from_xml(xml_bytes, heading_style_ids(styles_xml))

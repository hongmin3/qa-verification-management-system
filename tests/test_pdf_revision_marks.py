import fitz

from app.core.document_schemas import RevisionMark
from app.parsers.pdf_parser import parse_specification


def _marked_pdf(path, underline=False):
    document = fitz.open()
    page = document.new_page()
    point = fitz.Point(72, 100)
    page.insert_text(point, "Changed requirement", fontsize=12)
    rect = page.search_for("Changed requirement")[0]
    y = rect.y1 if underline else (rect.y0 + rect.y1) / 2
    page.draw_line(fitz.Point(rect.x0, y), fitz.Point(rect.x1, y), width=0.8)
    document.save(path)


def test_pdf_strikethrough_is_detected(tmp_path):
    path = tmp_path / "strike.pdf"
    _marked_pdf(path)
    assert RevisionMark.STRIKETHROUGH_DETECTED in parse_specification(path, "spec")[0].revision_marks


def test_pdf_underline_is_detected(tmp_path):
    path = tmp_path / "underline.pdf"
    _marked_pdf(path, underline=True)
    assert RevisionMark.UNDERLINE_DETECTED in parse_specification(path, "spec")[0].revision_marks

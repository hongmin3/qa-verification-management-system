"""Validates: REQ-PLMSKILL-005. Synthetic documents only, no operational writes."""
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy/qa-validation/scripts/qa_document_guard.py"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def docx(path, *, model="Viewer V1.0.12", month="September", header="Report", extra="", tracked=False,
         table_first=False, merge=False):
    paragraph = lambda text: f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    revision = '<w:ins w:id="1">' + paragraph("pending") + "</w:ins>" if tracked else ""
    table = ("<w:tbl><w:tr><w:tc>" + ('<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>' if merge else "")
             + paragraph("PC #1") + "</w:tc></w:tr></w:tbl>")
    body = ((table + paragraph(model) + paragraph(month)) if table_first
            else (paragraph(model) + paragraph(month) + table)) + extra + revision
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
        archive.writestr("word/header1.xml", f'<w:hdr xmlns:w="{W}">{paragraph(header)}</w:hdr>')
    return path


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8")


def read_result(result):
    assert result.stdout, result.stderr
    return json.loads(result.stdout)


def test_inspect_and_exact_allowlist(tmp_path):
    old = docx(tmp_path / "old.docx")
    new = docx(tmp_path / "new.docx", month="November")
    old_bytes = old.read_bytes()
    inspection = read_result(run("inspect", old))
    month = next(k for k, v in inspection["values"].items() if v == "September")
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps({"baseline_sha256": inspection["sha256"], "changes": [{"path": month, "before": "September", "after": "November",
                                              "reason": "current test month"}]}), encoding="utf-8")
    result = run("compare", old, new, "--allow", allow)
    report = read_result(result)
    assert result.returncode == 0 and report["passed"] is True
    assert len(report["allowed"]) == 1 and not report["unexpected"]
    assert old.read_bytes() == old_bytes
    assert report["baseline_sha256"] != report["candidate_sha256"]


@pytest.mark.parametrize("change", [
    {"model": "Viewer 1.0.12.105"}, {"header": "Changed"},
    {"extra": "<w:p><w:r><w:t>Unexpected explanatory note</w:t></w:r></w:p>"},
    {"tracked": True},
    {"table_first": True}, {"merge": True},
])
def test_unexpected_document_change_blocks(tmp_path, change):
    old = docx(tmp_path / "old.docx")
    new = docx(tmp_path / "new.docx", **change)
    result = run("compare", old, new)
    report = read_result(result)
    assert result.returncode != 0 and report["passed"] is False
    assert report["unexpected"] or report["review_required"]


def test_unused_or_wrong_allowlist_blocks(tmp_path):
    old = docx(tmp_path / "old.docx")
    new = docx(tmp_path / "new.docx")
    report = read_result(run("inspect", old))
    path = next(k for k, v in report["values"].items() if v == "September")
    allow = tmp_path / "allow.json"
    allow.write_text(json.dumps({"baseline_sha256": report["sha256"], "changes": [{"path": path, "before": "September", "after": "November",
                                              "reason": "current test month"}]}), encoding="utf-8")
    result = run("compare", old, new, "--allow", allow)
    assert result.returncode and read_result(result)["unused"]


def test_embedded_object_is_fingerprinted_and_requires_visual_review(tmp_path):
    old = docx(tmp_path / "old.docx", extra='<w:p><w:r><w:object w:dxaOrig="100"/></w:r></w:p>')
    new = docx(tmp_path / "new.docx", extra='<w:p><w:r><w:object w:dxaOrig="200"/></w:r></w:p>')
    identity = read_result(run("compare", old, old))
    assert identity["passed"] and identity["visual_review_required"]
    assert not read_result(run("compare", old, new))["passed"]


def test_json_tc_id_steps_and_relationship_are_compared(tmp_path):
    old = tmp_path / "old.json"
    new = tmp_path / "new.json"
    old.write_text(json.dumps({"id": "TC-1", "steps": ["open", "verify"], "links": ["SRS-1"]}), encoding="utf-8")
    new.write_text(json.dumps({"id": "TC-1", "steps": ["open"], "links": ["SRS-2"]}), encoding="utf-8")
    result = run("compare", old, new)
    report = read_result(result)
    assert result.returncode and len(report["unexpected"]) >= 2


def test_blank_pdf_cannot_be_accepted(tmp_path):
    from pypdf import PdfWriter
    pdf = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    result = run("pdf", pdf, "--pages", 1, "--require", "1:Report No")
    report = read_result(result)
    assert result.returncode and not report["passed"] and report["review_required"]


def test_pdf_required_fields_and_reference(tmp_path):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

    def write_pdf(path, text):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
        writer.write(path)
    baseline, actual = tmp_path / "baseline.pdf", tmp_path / "actual.pdf"
    write_pdf(baseline, "Report R-1 Windows 11 25H2")
    write_pdf(actual, "Report R-1 Windows 11 25H2")
    args = ["pdf", actual, "--pages", "1", "--require", "1:R-1", "--reference", baseline]
    assert read_result(run(*args))["passed"]
    write_pdf(actual, "Report R-1 Windows 11 wrong")
    assert not read_result(run(*args))["passed"]


def test_invalid_input_is_fail_closed(tmp_path):
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not a document")
    result = run("inspect", bad)
    assert result.returncode and not read_result(result)["passed"]


def test_all_skill_packages_use_identical_guard(tmp_path):
    from scripts import build_alm_skill, build_plm_skill, build_vxvue_desktop
    import io
    from app.modules.vxvue_chatbot.desktop import download_bellalun_skill
    archives = []
    for name, builder in [("alm", build_alm_skill), ("plm", build_plm_skill)]:
        path = tmp_path / f"{name}.zip"
        builder.build_skill("https://qa.example.test", path)
        archives.append((name, path.read_bytes()))
    archives.append(("vxvue-ai", build_vxvue_desktop.skill_bytes()))
    archives.append(("bellalun-server-ai", download_bellalun_skill().body))
    guide = ROOT / "deploy/qa-validation/references/legacy-validation.md"
    for name, content in archives:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert archive.read(name + "/scripts/qa_document_guard.py") == SCRIPT.read_bytes()
            assert archive.read(name + "/references/legacy-validation.md") == guide.read_bytes()
            assert "references/legacy-validation.md" in archive.read(name + "/SKILL.md").decode()


def test_candidate_builder_produces_hash_manifest_without_installing(tmp_path):
    from scripts.build_qa_skill_candidates import build
    import hashlib
    destination = tmp_path / "candidate"
    manifest = build("https://qa.example.test", destination)
    assert set(manifest["packages"]) == {"alm", "plm", "vxvue-ai", "bellalun-server-ai"}
    assert json.loads((destination / "manifest.json").read_text(encoding="utf-8")) == manifest
    for package in manifest["packages"].values():
        path = destination / package["file"]
        assert package["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert package["bytes"] == path.stat().st_size
    with pytest.raises(FileExistsError):
        build("https://qa.example.test", destination)


def test_managed_claude_installer_embeds_guard_and_preserves_fallback():
    from scripts.build_vxvue_desktop import installer_text
    import base64
    text = installer_text("https://qa.example.test", "candidate-test-ticket-123456")
    assert base64.b64encode(SCRIPT.read_bytes()).decode() in text
    assert "__QA_GUARD_BASE64__" not in text
    assert "qa-server-fallback:begin" in text and "qa-server-fallback:end" in text

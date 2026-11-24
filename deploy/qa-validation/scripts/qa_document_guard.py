"""Read-only, fail-closed QA document and record comparison.

The allowlist must be authored from the approved baseline and current evidence,
not inferred from the candidate diff. Layout/visual and upload provenance are
separate checks; this script never writes to PLM, ALM or the input files.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET
import zipfile


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/package/2006/relationships}"
STORY = re.compile(r"word/(document|header\d*|footer\d*|footnotes|endnotes|comments)\.xml$")
BLOCKS = {"p", "tbl", "tr", "tc", "sdt", "txbxContent", "footnote", "endnote", "comment"}
REVISIONS = {"ins", "del", "moveFrom", "moveTo", "pPrChange", "rPrChange", "tblPrChange",
             "trPrChange", "tcPrChange", "sectPrChange", "cellIns", "cellDel", "cellMerge"}
UNSUPPORTED = {"altChunk", "subDoc"}
TEXT = {"t", "delText"}
STRUCTURE_PROPERTIES = {"gridSpan", "vMerge", "hMerge", "gridBefore", "gridAfter", "tblHeader"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_json(data):
    def unique(pairs):
        values = {}
        for key, value in pairs:
            if key in values:
                raise ValueError(f"중복 JSON 키: {key}")
            values[key] = value
        return values
    return json.loads(data, object_pairs_hook=unique,
                      parse_constant=lambda val: (_ for _ in ()).throw(ValueError(f"잘못된 JSON 값: {val}")))


def pointer(key):
    return str(key).replace("~", "~0").replace("/", "~1")


def json_values(value):
    values = {}
    def visit(node, path):
        if isinstance(node, dict):
            values[path] = {"type": "object"}
            for key, child in node.items():
                visit(child, path + "/" + pointer(key))
        elif isinstance(node, list):
            values[path] = {"type": "array", "length": len(node)}
            for i, child in enumerate(node):
                visit(child, path + "/" + str(i))
        elif node is None:
            values[path] = {"type": "null"}
        else:
            values[path] = node
    visit(value, "$")
    return values


def word_values(data):
    values = {}
    review = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or "word/document.xml" not in names:
            raise ValueError("DOCX 본문이 없거나 ZIP 항목이 중복되었습니다.")
        for name in sorted(names):
            if name.startswith(("word/media/", "word/embeddings/", "word/charts/", "word/diagrams/")) and not name.endswith("/"):
                values[name] = {"sha256": sha(archive.read(name))}
                continue
            if name.startswith("word/") and name.endswith(".rels"):
                root = ET.fromstring(archive.read(name))
                # Relationships can change hyperlinks or embedded image targets even
                # when the displayed text is identical.
                for rel in root.findall(R + "Relationship"):
                    key = name + "/" + pointer(rel.get("Id"))
                    if key in values:
                        raise ValueError("DOCX 관계 ID가 중복되었습니다.")
                    values[key] = dict(rel.attrib)
                continue
            if not STORY.fullmatch(name):
                continue
            root = ET.fromstring(archive.read(name))
            values[name] = {"type": "story", "tag": root.tag}
            def text_of(node):
                parts = []
                def walk(child):
                    if child is not node and child.tag == W + "p":
                        return
                    local = child.tag.removeprefix(W)
                    if local in TEXT:
                        parts.append(child.text or "")
                    elif local == "tab":
                        parts.append("\t")
                    elif local in {"br", "cr"}:
                        parts.append("\n")
                    elif local == "noBreakHyphen":
                        parts.append("\u2011")
                    elif local == "softHyphen":
                        parts.append("\u00ad")
                    elif local == "instrText":
                        parts.append("<FIELD:" + (child.text or "") + ">")
                    elif local in {"fldSimple", "hyperlink", "footnoteReference", "endnoteReference"}:
                        parts.append("<" + local + ":" + repr(sorted(child.attrib.items())) + ">")
                    elif local == "sym":
                        parts.append("<SYMBOL:" + repr(sorted(child.attrib.items())) + ">")
                    for sub in child:
                        walk(sub)
                walk(node)
                return "".join(parts)
            def walk(node, parent_path):
                counts = {}
                order = []
                def visit(child):
                    local = child.tag.removeprefix(W)
                    if local in REVISIONS | UNSUPPORTED:
                        review.append(f"{name}:{parent_path}/{local}")
                    if local in BLOCKS:
                        counts[local] = counts.get(local, 0) + 1
                        path = f"{parent_path}/{local}[{counts[local]}]"
                        order.append(f"{local}[{counts[local]}]")
                        # Paragraph strings are exact (including spaces, tabs, breaks);
                        # semantic structure is separately inventoried.
                        values[path] = text_of(child) if local == "p" else {"type": local}
                        walk(child, path)
                    elif local in STRUCTURE_PROPERTIES:
                        counts[local] = counts.get(local, 0) + 1
                        values[f"{parent_path}/@{local}[{counts[local]}]"] = dict(child.attrib)
                    elif local == "object":
                        counts[local] = counts.get(local, 0) + 1
                        values[f"{parent_path}/@object[{counts[local]}]"] = {
                            "sha256": sha(ET.tostring(child, encoding="utf-8"))}
                        for sub in child:
                            visit(sub)
                    else:
                        for sub in child:
                            visit(sub)
                for child in node:
                    visit(child)
                if order:
                    values[parent_path + "/@order"] = order
            walk(root, name)
        if not any(key.startswith("word/document.xml/") for key in values):
            raise ValueError("읽을 수 있는 DOCX 문단 또는 표가 없습니다.")
    return values, sorted(set(review))


def inspect(path):
    path = Path(path)
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".docx":
        values, review = word_values(data)
    elif suffix == ".json":
        values, review = json_values(load_json(data)), []
    else:
        raise ValueError("본문 대조에는 DOCX 또는 JSON을 사용하세요.")
    return {"sha256": sha(data), "format": suffix[1:], "values": values,
            "review_required": review, "passed": not review, "value_count": len(values)}


def same(a, b):
    return json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(b, ensure_ascii=False, sort_keys=True)


def compare(baseline, candidate, allow=None):
    old, new = inspect(baseline), inspect(candidate)
    if old["format"] != new["format"]:
        raise ValueError("같은 종류의 파일을 비교하세요.")
    changes = {}
    allow_sha = None
    if allow:
        raw = Path(allow).read_bytes()
        manifest = load_json(raw)
        allow_sha = sha(raw)
        if manifest.get("baseline_sha256") != old["sha256"] or not isinstance(manifest.get("changes"), list):
            raise ValueError("허용 목록의 기준 SHA256 또는 changes를 확인하세요.")
        for item in manifest["changes"]:
            if (not isinstance(item, dict) or set(item) != {"path", "before", "after", "reason"}
                    or not isinstance(item["path"], str) or not isinstance(item["reason"], str)
                    or not item["reason"].strip() or item["path"] in changes
                    or same(item["before"], item["after"])):
                raise ValueError("허용 변경에는 중복 없는 위치·이전/새 값·근거가 필요합니다.")
            changes[item["path"]] = item
    expected, unexpected = [], []
    for key in sorted(old["values"].keys() | new["values"].keys()):
        before, after = old["values"].get(key), new["values"].get(key)
        if same(before, after):
            continue
        diff = {"path": key, "before": before, "after": after}
        item = changes.get(key)
        if item and same(before, item["before"]) and same(after, item["after"]):
            expected.append(item)
        else:
            unexpected.append(diff)
    unused = sorted(changes.keys() - {item["path"] for item in expected})
    review = old["review_required"] + new["review_required"]
    return {"passed": not (unexpected or unused or review), "format": old["format"],
            "baseline_sha256": old["sha256"], "candidate_sha256": new["sha256"],
            "allow_sha256": allow_sha, "baseline_count": old["value_count"], "candidate_count": new["value_count"],
            "allowed": expected, "unexpected": unexpected, "unused": unused, "review_required": review,
            "visual_review_required": old["format"] == "docx"}


def pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError("PDF 검증에는 pypdf가 필요합니다. 설치되지 않으면 PDF 원문 확인을 별도로 진행하세요.") from exc
    data = Path(path).read_bytes()
    pdf = PdfReader(io.BytesIO(data))
    if pdf.is_encrypted:
        raise ValueError("암호화 PDF는 확인할 수 없습니다.")
    pages = [page.extract_text() or "" for page in pdf.pages]
    return sha(data), pages


def normalize(text):
    return re.sub(r"\s+", " ", text).strip()


def verify_pdf(path, expected_pages, required, reference=None):
    digest, pages = pdf_text(path)
    review = [f"page:{i + 1}:텍스트 미확인" for i, text in enumerate(pages) if not normalize(text)]
    failures = []
    if expected_pages <= 0 or len(pages) != expected_pages:
        failures.append({"pages_expected": expected_pages, "pages_actual": len(pages)})
    for item in required:
        index, sep, phrase = item.partition(":")
        if not sep or not index.isdecimal() or not phrase.strip():
            raise ValueError("필수 문구는 쪽번호:문구 형식입니다.")
        page = int(index)
        if not 1 <= page <= len(pages) or normalize(phrase) not in normalize(pages[page - 1]):
            failures.append({"page": page, "missing": phrase})
    reference_sha = None
    if reference:
        reference_sha, reference_pages = pdf_text(reference)
        if len(reference_pages) != len(pages):
            failures.append({"reference_pages": len(reference_pages), "candidate_pages": len(pages)})
        for i, (old, new) in enumerate(zip(reference_pages, pages)):
            if not normalize(old):
                review.append(f"reference:{i+1}:텍스트 미확인")
            elif normalize(old) != normalize(new):
                failures.append({"page": i + 1, "difference": "reference_text_mismatch"})
    if not reference and not required:
        raise ValueError("확인할 필수 문구 또는 기준 PDF를 지정하세요.")
    return {"passed": not (failures or review), "sha256": digest,
            "reference_sha256": reference_sha, "pages": len(pages), "failures": failures,
            "review_required": review, "scope": "page_text" if reference else "required_fields",
            "visual_review_required": True, "conversion_provenance_required": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("inspect")
    p.add_argument("path")
    p = commands.add_parser("compare")
    p.add_argument("baseline")
    p.add_argument("candidate")
    p.add_argument("--allow")
    p = commands.add_parser("pdf")
    p.add_argument("path")
    p.add_argument("--pages", type=int, required=True)
    p.add_argument("--require", action="append", default=[])
    p.add_argument("--reference")
    args = parser.parse_args()
    try:
        if args.command == "inspect":
            result = inspect(args.path)
        elif args.command == "compare":
            result = compare(args.baseline, args.candidate, args.allow)
        else:
            result = verify_pdf(args.path, args.pages, args.require, args.reference)
    except Exception as exc:
        result = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Validates: REQ-VXVUECHATBOT-024 (get_generator_evidence compact·columns)."""
import copy
import importlib
import json
from pathlib import Path

import pytest


def _cell(ref, text, strike=False, **extra):
    runs = [{"text": text, "strike": strike}] if text else []
    value = {"cell": ref, "anchor": ref, "text": text, "data_type": "s", "formula": None,
             "validity": "deleted" if strike else "current", "runs": runs, "comment": None,
             "hidden_row": False, "hidden_column": False}
    value.update(extra)
    return value


def _bundle(root):
    tc_file = str(Path(root) / "(TC) Generator.xlsx")
    vss_file = str(Path(root) / "(사양서) VSS.xlsx")
    headers_u = [_cell("U1", "EGEN"), _cell("U2", "사양")]
    headers_v = [_cell("V1", "EGEN"), _cell("V2", "1차 결과")]
    model_header = [_cell(f"E{r}", "EGEN" if r == 4 else "") for r in (4, 5, 6, 8, 9, 10)]
    rows = []
    for row in (3, 4):
        rows.append({"file": tc_file, "sheet": "Generator", "row": row, "tc_id": f"TC-{row}",
                     "common": [dict(_cell(f"A{row}", f"TC-{row}"), field="TC_ID"),
                                dict(_cell(f"G{row}", "mAs 확인"), field="Step")],
                     "model_cells": [dict(_cell(f"U{row}", "Supported", strike=(row == 4)), role="model-spec", header=copy.deepcopy(headers_u)),
                                     dict(_cell(f"V{row}", "Pass", comment="QA 메모"), role="execution-history", header=copy.deepcopy(headers_v))],
                     "unclassified": [], "context": "primary"})
    vss = [{"model": "EGEN", "device": "Generator", "subject": "Exposure", "item": item, "description": "",
            "value": _cell(f"E{row}", "Supported"), "support": "supported",
            "source": {"file": vss_file, "sheet": "VSS", "cell": f"E{row}", "anchor": f"E{row}"},
            "model_header": copy.deepcopy(model_header)} for row, item in ((7, "kV"), (8, "mAs"))]
    return {"schema_version": 1, "model": "EGEN", "sources": [], "sheet_order": ["Generator"],
            "model_columns": {"Generator": [{"column": "U", "role": "model-spec", "headers": headers_u},
                                            {"column": "V", "role": "execution-history", "headers": headers_v}]},
            "vss": vss, "tc": rows, "scope": {"vss": {"total": 2, "matched": 2, "returned": 2, "filtered": False, "truncated": False},
                                              "tc": {"total": 9, "matched": 9, "returned": 2, "filtered": False, "truncated": True}},
            "limitations": ["Records are evidence; semantic coverage and product Pass/Fail require separate review."]}


@pytest.fixture
def session(tmp_path, monkeypatch):
    mcp = importlib.import_module("app.modules.vxvue_chatbot.mcp_server")
    root = tmp_path / "snap"
    root.mkdir()
    documents = [{"document_id": "(TC) Generator.xlsx", "name": "(TC) Generator.xlsx", "sha256": "a" * 64},
                 {"document_id": "(사양서) VSS.xlsx", "name": "(사양서) VSS.xlsx", "sha256": "b" * 64}]
    monkeypatch.setattr(mcp, "Corpus", lambda *a: None)
    monkeypatch.setattr(mcp.generator, "bundle", lambda r, *a: _bundle(r))
    snapshot = {"id": "s", "root": str(root), "index": str(root / "i.db"), "documents": documents}
    return mcp, snapshot, tmp_path


def _all_cells(value):
    """Every source cell the response carries: (sheet/row or record, cell, text, validity, comment, strike)."""
    found = set()
    for row in value["tc"]:
        for group in ("common", "model_cells", "unclassified"):
            for c in row[group]:
                strike = any(r.get("strike") for r in c.get("runs", []))
                found.add((row["row"], c["cell"], c["text"], c["validity"], c.get("comment"), strike))
    for rec in value["vss"]:
        found.add((rec["item"], rec["value"]["cell"], rec["value"]["text"], rec["value"]["validity"], None, False))
    return found


def test_compact_removes_only_repeats_and_keeps_every_cell_fact(session):
    mcp, snapshot, tmp = session
    tools = mcp.KnowledgeTools(snapshot, [], tmp / "trace")
    full = tools.call("get_generator_evidence", {"model": "EGEN"})
    slim = tools.call("get_generator_evidence", {"model": "EGEN", "compact": True})
    assert "compact" not in full and "header" in full["tc"][0]["model_cells"][0]
    assert slim["compact"] is True
    assert _all_cells(slim) == _all_cells(full)
    cell = slim["tc"][0]["model_cells"][0]
    assert "header" not in cell and cell["role"] == "model-spec"
    assert set(cell) == {"cell", "text", "data_type", "validity", "role"}
    struck = slim["tc"][1]["model_cells"][0]
    assert struck["validity"] == "deleted" and struck["runs"] == [{"text": "Supported", "strike": True}]
    assert slim["tc"][0]["model_cells"][1]["comment"] == "QA 메모"
    assert slim["model_columns"]["Generator"][0]["headers"][0]["text"] == "EGEN"
    assert "model_header" not in slim["vss"][0] and slim["vss_model_headers"]["VSS!E"][0]["text"] == "EGEN"
    docs = {d["document_id"]: d for d in slim["documents"]}
    assert docs["(TC) Generator.xlsx"]["sha256"] == "a" * 64
    assert "sha256" not in slim["tc"][0] and slim["tc"][0]["document_id"] == "(TC) Generator.xlsx"
    assert slim["tc"][0]["location"] == {"sheet": "Generator", "row": 3}
    assert slim["scope"] == full["scope"] and slim["limitations"] == full["limitations"]
    assert len(json.dumps(slim, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False)) * 0.6


def test_header_kept_when_it_differs_from_model_columns(session, monkeypatch):
    mcp, snapshot, tmp = session
    value = _bundle(snapshot["root"])
    value["tc"][0]["model_cells"][0]["header"][0]["text"] = "다른 머리글"
    monkeypatch.setattr(mcp.generator, "bundle", lambda *a: value)
    slim = mcp.KnowledgeTools(snapshot, [], tmp / "trace").call("get_generator_evidence", {"model": "EGEN", "compact": True})
    assert slim["tc"][0]["model_cells"][0]["header"][0]["text"] == "다른 머리글"
    assert "header" not in slim["tc"][1]["model_cells"][0]


def test_columns_keep_only_selected_cells(session):
    mcp, snapshot, tmp = session
    tools = mcp.KnowledgeTools(snapshot, [], tmp / "trace")
    value = tools.call("get_generator_evidence", {"model": "EGEN", "columns": ["A", "U"]})
    assert value["selection"] == {"columns": ["A", "U"]}
    assert [c["cell"] for c in value["tc"][0]["common"]] == ["A3"]
    assert [c["cell"] for c in value["tc"][0]["model_cells"]] == ["U3"]
    assert [c["column"] for c in value["model_columns"]["Generator"]] == ["U"]
    assert value["vss"] == []
    for bad in ([], ["a"], ["A"] * 101, "A", ["AAAA"]):
        with pytest.raises(ValueError):
            tools.call("get_generator_evidence", {"model": "EGEN", "columns": bad})


def test_desktop_defaults_to_compact_and_keeps_scope(session, monkeypatch):
    mcp, snapshot, tmp = session
    from app.modules.vxvue_chatbot.desktop import DesktopTools
    tools = DesktopTools(snapshot, [], tmp / "trace")
    value = tools.call("get_generator_evidence", {"model": "EGEN"})
    assert value["compact"] is True
    assert value["scope"]["tc"]["truncated"] is True  # truncation must stay visible
    legacy = tools.call("get_generator_evidence", {"model": "EGEN", "compact": False})
    assert "compact" not in legacy and "header" in legacy["tc"][0]["model_cells"][0]

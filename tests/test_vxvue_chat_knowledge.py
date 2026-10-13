"""Validates: REQ-VXVUECHATBOT-005, REQ-VXVUECHATBOT-006."""
from pathlib import Path
from types import SimpleNamespace
import importlib
import json
import pytest
from app.core.storage import Storage


@pytest.fixture
def sources(tmp_path):
    store = Storage(tmp_path / "app.db")
    for kind, name, text in (
        ("instruction_prompt", "VXvue_지침_프롬프트_Rev1.9.txt", "old prompt"),
        ("instruction_prompt", "VXvue_지침_프롬프트_Rev1.18.txt", "LATEST PROMPT\nkeep full last line"),
        ("qa_rules", "[QA 작성 규칙] guide_Rev1.18.md", "# QA GUIDE\nKeep source TC unchanged."),
        ("specification", "(사양서) Exposure.md", "# Exposure\nVP-1234 Trigger is Ready.\n"),
    ):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        store.add_document(kind, "VXvue", "1", "registration-sequence", name, path)
    return store


def module():
    return importlib.import_module("app.modules.vxvue_chatbot.knowledge")


def test_numeric_latest_rules_full_text_and_immutable_snapshot(sources, tmp_path):
    snapshot = module().prepare_knowledge(sources, tmp_path / "snapshots")
    assert "LATEST PROMPT\nkeep full last line" in snapshot["rules"]
    assert "# QA GUIDE" in snapshot["rules"]
    assert "old prompt" not in snapshot["rules"]
    path = tmp_path / "(사양서) Exposure.md"
    path.write_text("changed", encoding="utf-8")
    original = Path(snapshot["root"]) / "(사양서) Exposure.md"
    assert "VP-1234" in original.read_text(encoding="utf-8")
    newer = module().prepare_knowledge(sources, tmp_path / "snapshots")
    assert newer["id"] != snapshot["id"]


def test_missing_latest_rules_blocks_ai_instead_of_using_old(sources, tmp_path):
    (tmp_path / "VXvue_지침_프롬프트_Rev1.18.txt").unlink()
    with pytest.raises(ValueError, match="지침"):
        module().prepare_knowledge(sources, tmp_path / "snapshots")


def test_search_and_document_preserve_locations_and_no_match_is_unknown(sources, tmp_path):
    snapshot = module().prepare_knowledge(sources, tmp_path / "snapshots")
    mcp = importlib.import_module("app.modules.vxvue_chatbot.mcp_server")
    session = mcp.KnowledgeTools(snapshot, [], tmp_path / "trace.jsonl")
    result = session.call("search_documents", {"query": "VP-1234"})
    assert "VP-1234" in json.dumps(result, ensure_ascii=False)
    assert "document_id" in json.dumps(result)
    assert "/vxvue-chatbot/jobs/" in json.dumps(result)
    empty = session.call("search_documents", {"query": "Missing_Function_Z123"})
    assert "unknown" in json.dumps(empty) or "not-found" in json.dumps(empty)
    with pytest.raises(ValueError):
        session.call("get_document", {"document_id": "../outside.txt"})


def test_unreadable_spec_is_reported_and_attachments_do_not_replace_rules(sources, tmp_path):
    missing = tmp_path / "missing.pdf"
    sources.add_document("specification", "VXvue", "1", "", missing.name, missing)
    snapshot = module().prepare_knowledge(sources, tmp_path / "snapshots")
    assert snapshot["failures"]
    assert "LATEST PROMPT" in snapshot["rules"]


def test_generator_cell_records_have_snapshot_citations(sources, tmp_path, monkeypatch):
    snapshot = module().prepare_knowledge(sources, tmp_path / "snapshots")
    mcp = importlib.import_module("app.modules.vxvue_chatbot.mcp_server")
    monkeypatch.setattr(mcp.generator, "bundle", lambda *args: {"vss": [{"source": {"file": str(Path(snapshot["root"]) / "(사양서) Exposure.md"), "sheet": "VSS", "cell": "E7"}, "value": {"text": "Ready"}}]})
    session = mcp.KnowledgeTools(snapshot, [], tmp_path / "trace", "job")
    value = session.call("get_generator_evidence", {"model": "Ecoray"})
    reference = value["vss"][0]["source"]
    assert reference["reference_url"].startswith("/vxvue-chatbot/jobs/job/source")
    assert reference["location"] == {"sheet": "VSS", "cell": "E7"}

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


def test_synced_rules_are_read_from_manifest_without_db_registration(tmp_path, monkeypatch):
    import app.core.product_config as configs
    from app.core.knowledge_upload import store_asset, commit
    monkeypatch.setattr(configs, "_products_dir", lambda root=None: Path(__file__).resolve().parents[1] / "config/products")
    store = Storage(tmp_path / "data/app.db")
    assets = []
    for kind, name, text in [("instruction_prompt", "VXvue_지침_프롬프트_Rev1.18.txt", "CANONICAL PROMPT FULL"), ("qa_rules", "[QA 작성 규칙] guide_Rev1.18.md", "CANONICAL GUIDE FULL")]:
        data = text.encode()
        record = store_asset("VXvue", kind, name, data, root=tmp_path)
        assets.append(record)
    commit("VXvue", {"assets": assets}, root=tmp_path, storage=store)
    assert store.active_documents("qa_rules", "VXvue") == []
    snapshot = module().prepare_knowledge(store, tmp_path / "snapshots")
    assert "CANONICAL PROMPT FULL" in snapshot["rules"] and "CANONICAL GUIDE FULL" in snapshot["rules"]
    # An unrelated stale DB rule cannot replace the canonical folder's rule.
    stale = tmp_path / "stale.md"
    stale.write_text("WRONG RULE", encoding="utf-8")
    store.add_document("qa_rules", "VXvue", "", "", "stale_Rev99.md", stale)
    assert "WRONG RULE" not in module().prepare_knowledge(store, tmp_path / "snapshots")["rules"]


def test_export_upload_commit_snapshot_and_changed_skill_are_connected(tmp_path, monkeypatch):
    from tests.test_skill_knowledge import package, config
    from app.core.skill_knowledge import export_skill
    from app.core.knowledge_upload import store_asset, commit
    from app.modules.vxvue_chatbot.knowledge import skill_status
    home, folder = tmp_path / "codex", tmp_path / "source"
    plugin = package(home, "1.0", "FRESH SKILL")
    cfg = config(folder)
    cfg.product = "VXvue"
    exported = export_skill(cfg, codex_home=home)
    store = Storage(tmp_path / "server/data/app.db")
    assets = []
    for kind, name, data in [("qa_rules", "[QA 작성 규칙] Guide_Rev1.18.md", b"CANONICAL GUIDE"), ("instruction_prompt", "VXvue_지침_프롬프트_Rev1.18.txt", b"CANONICAL PROMPT"), ("skill_instruction", "[Skill 지침] VXvue.md", Path(exported["path"]).read_bytes())]:
        assets.append(store_asset("VXvue", kind, name, data, root=tmp_path / "server"))
    commit("VXvue", {"assets": assets}, root=tmp_path / "server", storage=store)
    first = module().prepare_knowledge(store, tmp_path / "snapshots")
    assert "FRESH SKILL" in first["skill_text"] and "full reference" in first["skill_text"]
    assert first["skill_metadata"]["version"] == "1.0"
    assert skill_status(tmp_path / "server", exported["code_hash"])["code_review_needed"] is False
    assert skill_status(tmp_path / "server", "old-hash")["code_review_needed"] is True
    (plugin / "skills/qa/references/qa.md").write_text("UPDATED REFERENCE", encoding="utf-8")
    exported = export_skill(cfg, codex_home=home)
    assets[-1] = store_asset("VXvue", "skill_instruction", "[Skill 지침] VXvue.md", Path(exported["path"]).read_bytes(), root=tmp_path / "server")
    commit("VXvue", {"assets": assets}, root=tmp_path / "server", storage=store)
    newer = module().prepare_knowledge(store, tmp_path / "snapshots")
    assert "UPDATED REFERENCE" in newer["skill_text"]
    assert newer["id"] != first["id"] and "UPDATED REFERENCE" not in first["skill_text"]
    assert "CANONICAL GUIDE" in newer["rules"] and "CANONICAL PROMPT" in newer["rules"]


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

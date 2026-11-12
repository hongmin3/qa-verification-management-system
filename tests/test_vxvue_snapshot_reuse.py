"""Validates: REQ-VXVUECHATBOT-022 — 새 지식 스냅샷은 바뀐 문서만 다시 파싱하고, 첫 조회에서 색인을 다시 만들지 않는다."""
import importlib
from pathlib import Path

import pytest

from app.core.storage import Storage


@pytest.fixture
def sources(tmp_path):
    store = Storage(tmp_path / "app.db")
    for kind, name, text in (
        ("instruction_prompt", "VXvue_지침_프롬프트_Rev1.18.txt", "LATEST PROMPT"),
        ("qa_rules", "[QA 작성 규칙] guide.md", "# QA GUIDE v1"),
        ("specification", "(사양서) A.md", "# A\nVP-1 alpha\n"),
        ("specification", "(사양서) B.md", "# B\nVP-2 beta\n"),
    ):
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        store.add_document(kind, "VXvue", "1", "registration-sequence", name, path)
    return store


@pytest.fixture
def parsed(monkeypatch):
    corpus_module = importlib.import_module("app.modules.vxvue_chatbot.skill.scripts.corpus")
    real, names = corpus_module.parse, []

    def counting(path):
        names.append(Path(path).name)
        return real(path)
    monkeypatch.setattr(corpus_module, "parse", counting)
    return names


def knowledge():
    return importlib.import_module("app.modules.vxvue_chatbot.knowledge")


def query(snapshot):
    from app.modules.vxvue_chatbot.skill.scripts.corpus import Corpus
    return Corpus(snapshot["root"], snapshot["index"]).search_documents("alpha")


def test_first_query_does_not_reindex_a_new_snapshot(sources, tmp_path, parsed):
    snapshot = knowledge().prepare_knowledge(sources, tmp_path / "snapshots")
    built = len(parsed)
    assert built >= 3
    assert query(snapshot)["results"]
    assert len(parsed) == built, f"첫 조회에서 다시 파싱함: {parsed[built:]}"


def test_new_snapshot_reparses_only_changed_documents(sources, tmp_path, parsed):
    first = knowledge().prepare_knowledge(sources, tmp_path / "snapshots")
    query(first)
    parsed.clear()
    (tmp_path / "[QA 작성 규칙] guide.md").write_text("# QA GUIDE v2", encoding="utf-8")
    second = knowledge().prepare_knowledge(sources, tmp_path / "snapshots")
    assert second["id"] != first["id"]
    assert parsed == ["[QA 작성 규칙] guide.md"]
    assert "alpha" in str(query(second)["results"])
    assert "# QA GUIDE v1" in (Path(first["root"]) / "[QA 작성 규칙] guide.md").read_text(encoding="utf-8")
    from app.modules.vxvue_chatbot.skill.scripts.corpus import Corpus
    status = Corpus(second["root"], second["index"]).get_status()
    assert status["scope"]["indexed_files"] == 4 and not status["scope"]["errors"]

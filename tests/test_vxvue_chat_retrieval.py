"""Validates: REQ-VXVUECHATBOT-010. Exhaustive candidates are not full evidence."""
import json
import pytest
import fitz
from app.modules.vxvue_chatbot.skill.scripts.corpus import Corpus
from app.modules.vxvue_chatbot.mcp_server import KnowledgeTools, estimate_tokens, tools

@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    for n in range(107):
        (root / f"spec-{n:03}.md").write_text(f"ID: VP-{1000+n}\nShimadzu License feature {n}\n" + "context " * 100, encoding="utf-8")
    (root / "spec-other.md").write_text("Other License only", encoding="utf-8")
    return Corpus(root, tmp_path / "index.sqlite3")

def test_pagination_is_stable_and_complete_independent_of_limit(corpus):
    pages = [corpus.search_documents("Shimadzu", limit=17, offset=n) for n in range(0,107,17)]
    ids = [r["chunk_id"] for p in pages for r in p["results"]]
    assert len(ids) == len(set(ids)) == 107
    first = corpus.search_documents("Shimadzu", limit=100)
    assert ids[:100] == [r["chunk_id"] for r in first["results"]]
    assert pages[0]["remaining"] == 90 and pages[0]["next_offset"] == 17
    assert pages[-1]["remaining"] == 0 and not pages[-1]["truncated"]
    assert corpus.search_documents("Shimadzu", offset=200)["results"] == []

def test_all_terms_prevents_generic_license_noise_and_preview_is_small(corpus):
    broad = corpus.search_documents("Shimadzu License")
    narrow = corpus.search_documents("Shimadzu License", match_mode="all", preview=True)
    assert broad["matched"] == 108 and narrow["matched"] == 107
    row = narrow["results"][0]
    assert row["preview_only"] and len(row["preview"]) <= 300
    assert "evidence" not in row and "sha256" in row
    full = corpus.get_document(row["document_id"], section=row["chunk_id"])
    assert "context" in full["results"][0]["evidence"]
    assert estimate_tokens(narrow) < estimate_tokens(corpus.search_documents("Shimadzu License", match_mode="all"))
    assert corpus.search_documents("Shimadzu nonexistent", match_mode="all")["matched"] == 0

@pytest.mark.parametrize("args", [{"offset":-1},{"offset":True},{"match_mode":"invalid"},{"preview":"yes"}])
def test_invalid_search_options_rejected(corpus,args):
    with pytest.raises(ValueError):
        corpus.search_documents("Shimadzu", **args)

def test_preview_does_not_claim_read_citation(corpus,tmp_path):
    session = KnowledgeTools({"root":str(corpus.root),"index":str(corpus.index_path)},[],tmp_path/"trace","job")
    session.call("search_documents",{"query":"Shimadzu","preview":True})
    assert not (tmp_path/"trace").exists()
    session.call("search_documents",{"query":"Shimadzu","limit":1})
    assert (tmp_path/"trace").read_text().strip()
    schema = next(x for x in tools() if x["name"]=="search_documents")["inputSchema"]["properties"]
    assert {"offset","preview","match_mode"} <= schema.keys()

def test_pdf_page_reports_text_and_image_without_claiming_verified(tmp_path):
    root = tmp_path/"original"
    root.mkdir()
    path = root/"spec.pdf"
    doc=fitz.open()
    doc.new_page().insert_text((40,40),"Text page")
    doc.new_page()
    image = doc[0].get_pixmap().tobytes("png")
    doc.new_page().insert_image(fitz.Rect(0,0,300,400), stream=image)
    doc.save(path)
    session=KnowledgeTools({"root":str(root),"index":str(tmp_path/"idx"),"documents":[{"document_id":"spec.pdf"}]},[],tmp_path/"trace","job")
    for page, state in [(1,"text-available"),(2,"no-extractable-text"),(3,"no-extractable-text")]:
        blocks=session.call("get_document_page",{"document_id":"spec.pdf","page":page})
        assert any(b["type"]=="image" for b in blocks)
        assert state in json.dumps(blocks[:2],ensure_ascii=False)
        assert "rendered-not-reviewed" in json.dumps(blocks[:2])
        if page == 3:
            assert json.loads(blocks[1]["text"])["embedded_images"] == 1
    with pytest.raises(ValueError):
        session.call("get_document_page",{"document_id":"spec.pdf","page":4})

def test_document_filter_keeps_counts_and_rejects_unknown_id(corpus):
    result=corpus.search_documents("Shimadzu", document_id="spec-000.md", preview=True)
    assert result["matched"] == 1
    assert result["matched_documents"] == [{"document_id":"spec-000.md","matched":1}]
    assert corpus.search_documents("Shimadzu", document_id="absent.pdf")["matched"] == 0
    assert sum(x["matched"] for x in corpus.search_documents("Shimadzu")["matched_documents"]) == 107

def test_page_batch_preserves_images_and_reports_individual_failure(tmp_path):
    root=tmp_path/"docs";root.mkdir();doc=fitz.open();doc.new_page().insert_text((30,30),"evidence");doc.save(root/"spec.pdf")
    session=KnowledgeTools({"root":str(root),"index":str(tmp_path/"index"),"documents":[{"document_id":"spec.pdf"}]},[],tmp_path/"trace","job")
    blocks=session.call("get_document_pages",{"requests":[{"document_id":"spec.pdf","page":1},{"document_id":"spec.pdf","page":99}]})
    assert sum(x["type"]=="image" for x in blocks)==1
    assert "incomplete" in json.dumps([x for x in blocks if x["type"]=="text"])
    assert len((tmp_path/"trace").read_text().splitlines())==1
    with pytest.raises(ValueError):
        session.call("get_document_pages",{"requests":[{"document_id":"spec.pdf","page":1}]*7})

def test_document_context_reads_neighboring_units(tmp_path):
    from docx import Document
    root=tmp_path/"docs";root.mkdir();doc=Document()
    for text in ["Section start","Shimadzu only","Exact applicability condition","Next topic"]:
        doc.add_paragraph(text)
    doc.save(root/"manual.docx")
    corpus=Corpus(root,tmp_path/"index")
    hit=corpus.search_documents("Shimadzu")["results"][0]
    result=corpus.get_document("manual.docx",section=hit["chunk_id"],context=1)
    assert len(result["results"])==3
    assert "Exact applicability condition" in json.dumps(result,ensure_ascii=False)
    with pytest.raises(ValueError):
        corpus.get_document("manual.docx",section=hit["chunk_id"],context=4)

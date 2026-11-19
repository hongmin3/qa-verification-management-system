"""Validates REQ-VXVUECHATBOT-028."""
from contextlib import nullcontext
import hashlib
from types import SimpleNamespace

import httpx

import pytest

from scripts import qa_guide_compare as guide


@pytest.mark.parametrize("product", ["VXvue", "Bellalun Viewer"])
def test_changed_documents_are_staged_and_match_manifest(tmp_path, product):
    path = tmp_path / "rules.md"
    path.write_bytes(b"candidate")
    asset = {"kind": "qa_rules", "file_name": "rules.md", "sha256": hashlib.sha256(b"candidate").hexdigest()}
    baseline = {("qa_rules", "rules.md"): hashlib.sha256(b"baseline").hexdigest()}
    assert guide.stage_changed_rules(product, [asset], baseline, True, lambda _: path) == {
        ("qa_rules", "rules.md"): b"candidate",
    }
    path.write_bytes(b"changed since collection")
    with pytest.raises(ValueError, match="해시"):
        guide.stage_changed_rules(product, [asset], baseline, True, lambda _: path)


def test_manifest_removal_cannot_bypass_comparison():
    with pytest.raises(ValueError):
        guide.stage_changed_rules("VXvue", [], {("qa_rules", "rules.md"): "before"}, True, None)


def test_other_document_changes_do_not_start_model():
    assert not guide.stage_changed_rules("VXvue", [{"kind": "manual", "file_name": "m.pdf"}],
                                          {}, True, None)


@pytest.mark.parametrize("product", ["VXvue", "Bellalun Viewer"])
@pytest.mark.parametrize("outcome", ["REGRESSION", "INCOMPARABLE", "PASS", "DRIFT", "UNCOMMITTED"])
def test_upload_first_write_uses_only_approved_bytes(tmp_path, monkeypatch, product, outcome):
    from app.core import knowledge_push as push

    path = tmp_path / "qa.md"
    tested = b"the exact guide under evaluation"
    path.write_bytes(tested)
    sha = guide.digest(tested)
    asset = {"kind": "qa_rules", "file_name": path.name, "sha256": sha}
    before = {("qa_rules", path.name): sha if outcome == "UNCOMMITTED" else guide.digest(b"old")}
    posts = []
    state_reads = []

    def state(*_):
        state_reads.append(1)
        return {} if outcome == "DRIFT" and len(state_reads) > 1 else before

    def evaluate(name, server, staged, desired, prune, *, operating):
        assert outcome != "UNCOMMITTED"
        assert name == product
        assert staged == {("qa_rules", path.name): tested}
        assert desired == {("qa_rules", path.name): sha}
        assert prune
        assert operating == {("qa_rules", path.name): b"old"}
        path.write_bytes(b"changed during model execution")
        return {"status": "PASS" if outcome == "DRIFT" else outcome}, tmp_path / "report.json"

    def send(url, **kwargs):
        name = url.rsplit("/", 1)[-1]
        posts.append((name, kwargs["files"]["file"][1].read() if name == "asset" else None))
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"status": "SUCCESS", "sha256": sha})

    client = SimpleNamespace(post=send)
    monkeypatch.setattr(push, "_client", lambda _: nullcontext(client))
    monkeypatch.setattr(push, "resolve_config", lambda _: SimpleNamespace(product=product, knowledge_source=SimpleNamespace()))
    monkeypatch.setattr(push, "source_available", lambda _: True)
    monkeypatch.setattr(push, "pull_person_documents", lambda *_, **__: None)
    monkeypatch.setattr(push, "sync_product", lambda *_, **__: SimpleNamespace(status="SUCCESS", detail="수집"))
    monkeypatch.setattr(push, "load_manifest", lambda _: {"assets": [asset]})
    monkeypatch.setattr(push, "collected_path", lambda *_: path)
    monkeypatch.setattr(push, "fetch_server_state", state)
    monkeypatch.setattr(guide, "read_operating_inputs", lambda *_: {("qa_rules", path.name): b"old"})
    monkeypatch.setattr(guide, "compare_upload", evaluate)
    result = push.push_product(product, "http://server.invalid")
    if outcome == "PASS":
        assert result["status"] == "SUCCESS"
        assert posts == [("asset", tested), ("commit", None)]
    else:
        assert result["status"] == "FAILED"
        assert not posts


def test_bridge_renders_candidate_only_and_pins_document_queries(monkeypatch):
    seen = []

    def upstream(client, config, message):
        seen.append(message)
        return {"jsonrpc": "2.0", "id": message["id"], "result": guide.workflow_result({"text": "운영"})}

    def render(candidate, args):
        return {"text": candidate["rules"], "received": args}

    monkeypatch.setattr(guide, "rpc", upstream)
    with guide.preview_bridge(None, {}, "get_workflow", render, {"id": "fixed", "rules": "후보"},
                              {"vxvue-ai": "fixed"}, preview_id="distinct") as bridge, httpx.Client(trust_env=False) as client:
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "get_workflow", "arguments": {"snapshot_id": "fixed", "section": "spec"},
        }}
        baseline = client.post(f"{bridge}/baseline", json=message).json()
        candidate = client.post(f"{bridge}/candidate", json=message).json()
        assert list(guide.evals.payloads(baseline))[0]["text"] == "운영"
        assert list(guide.evals.payloads(candidate))[0]["text"] == "후보"
        assert list(guide.evals.payloads(candidate))[0]["qa_guide_preview_id"] == "distinct"
        assert len(seen) == 1
        message["params"]["name"] = "get_document"
        client.post(f"{bridge}/candidate", json=message).raise_for_status()
        assert len(seen) == 2
        message["params"]["arguments"]["snapshot_id"] = "different"
        assert "error" in client.post(f"{bridge}/candidate", json=message).json()
        assert len(seen) == 2


def test_document_response_cannot_leak_previous_rules():
    document = next(iter(guide.EVIDENCE_DOCUMENTS))
    response = {"result": guide.workflow_result({"results": [
        {"document_id": document, "evidence": "필수 근거"},
        {"document_id": "removed-guide.md", "evidence": "이전 지침"},
    ]})}
    restricted = guide.restrict_document_reply(response)
    assert list(guide.evals.payloads(restricted))[0]["results"] == [
        {"document_id": document, "evidence": "필수 근거"},
    ]

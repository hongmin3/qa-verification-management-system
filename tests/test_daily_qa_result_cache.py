"""Validates: REQ-QAINTEL-037 / TEST-QAINTEL-022. Synthetic input, SQLite, fake AI only."""
from __future__ import annotations

import copy
import json
from dataclasses import replace

import pytest

from app.modules.daily_qa import result_cache
from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome
from app.modules.daily_qa.cancellation import RunCancelled
from app.modules.daily_qa.intelligence import KnownIds
from app.modules.daily_qa.packages import Task
from app.modules.daily_qa.pipeline import Inputs, _Run
from app.modules.daily_qa.schema import parse_result
from app.modules.daily_qa.store import DailyQaStore
from app.modules.daily_qa.workspace import RunWorkspace, write_task_input
from tests.daily_qa_fixtures import make_settings, rules_ok
from tests.qa_intel_harness import DAY1

SKILL = "qa-new-issue-analysis"


def payload(event_id=1):
    return {"analysis_type": "NEW_ISSUE", "items": [{"target": "VP-100", "event_ids": [event_id],
            "events": [{"id": event_id, "event_type": "ISSUE_CONTENT_UPDATED", "before": {"text": "old"},
                        "after": {"text": "new", "comments": [{"id": "c1", "text": "observed"}]}}],
            "issue": {"id": "VP-100", "description": "new"},
            "candidates": {"srs": [{"id": "VP-10", "text": "current", "status": "draft"}]}}],
            "answered_questions": [], "unreadable_documents": []}


def raw_result(task):
    return {"skill": task.skill, "task_id": task.task_id, "gate_status": {"G1": "PASS"},
            "open_questions": [], "findings": [{"subject": "VP-100", "verdict": "SPEC_VIOLATION",
            "summary": "Synthetic observation", "evidence": [{"source_type": "srs", "location": "VP-10 본문",
            "validity": "Current"}], "sections": {"duplicate": {"verdict": "NO_DUPLICATE_FOUND", "candidates": []}}}]}


class Case:
    def __init__(self, tmp):
        self.cfg = make_settings(tmp / "repo", tmp / "workspace", claude_model="claude-opus-4-6", claude_effort="medium")
        self.store = DailyQaStore(self.cfg.db_path)
        self.calls = []
        self.transform = lambda raw: raw
        self.runner_ok = True
        root = self.cfg.product_workspace_dir
        for folder in (root / "rules", root / ".claude" / "skills" / SKILL):
            folder.mkdir(parents=True, exist_ok=True)
        (root / "rules" / "qa-guide.md").write_text("Synthetic rules §9", encoding="utf-8")
        (root / ".claude" / "skills" / SKILL / "SKILL.md").write_text("Synthetic skill v1", encoding="utf-8")
        contract = root / ".claude" / "skills" / "qa-common-rules" / "references" / "output-contract.md"
        contract.parent.mkdir(parents=True, exist_ok=True)
        contract.write_text("Synthetic output v1", encoding="utf-8")
        self.known = KnownIds(issue_ids={"VP-100"}, srs_ids={"VP-10"})

    def produce(self, task, ws):
        self.calls.append(task.task_id)
        (ws.out_dir / f"{task.task_id}.json").write_text(json.dumps(self.transform(raw_result(task))), encoding="utf-8")
        return RunnerOutcome(self.runner_ok, 0 if self.runner_ok else 1, 0, meta={"usage": {"input_tokens": 100,
                             "output_tokens": 10}, "total_cost_usd": .05})

    def run(self, run_id, task_id="NEW-001", data=None, **kwargs):
        self.store.create_run(run_id, False, self.cfg.slug)
        run = _Run(self.cfg, self.store, run_id, DAY1, False, False, None, FakeRunner(self.produce), Inputs(),
                   rules_ok(self.cfg.root.parent), None, "manual", **kwargs)
        ws = run.workspace = RunWorkspace(self.cfg.product_workspace_dir, run_id)
        for folder in (ws.in_dir, ws.out_dir, ws.context_dir, run.out_dir):
            folder.mkdir(parents=True, exist_ok=True)
        (ws.context_dir / "srs_current.jsonl").write_text('{"id":"VP-10","text":"current"}\n', encoding="utf-8")
        task = Task(task_id, SKILL, data or payload())
        return run, task


def execute(run, task, known):
    return run._run_task(task, known, "NEW_ISSUE")


def test_same_batch_reuses_without_cli_tokens_and_rebinds_ids(tmp_path):
    c = Case(tmp_path)
    first, task = c.run("origin")
    original = execute(first, task, c.known)
    source_ids = original.finding_ids["VP-100"]
    c.store.review_finding(source_ids[0], "APPROVED", "synthetic reviewer", "synthetic approval")
    second, task2 = c.run("current", "NEW-999", payload(99))
    hit = execute(second, task2, c.known)
    assert hit.ok and len(c.calls) == 1
    assert second.claude_calls == 0 and second.token_usage["total_tokens"] == 0 and second.token_usage["cost_usd"] == 0
    finding = c.store.get_finding(hit.finding_ids["VP-100"][0])
    assert finding["run_id"] == "current" and finding["task_id"] == "NEW-999" and finding["event_ids"] == [99]
    assert finding["review_status"] == "PENDING"
    assert finding["sections"]["result_cache"] == {"run_id": "origin", "task_id": "NEW-001", "finding_ids": source_ids}
    assert second.result_cache["hits"] == 1 and second.result_cache["reused_findings"] == 1
    stage = second._stage_result("NEW_ISSUE", [task2], [hit], 0)
    assert stage["counts"]["cache_hits"] == 1 and stage["counts"]["done"] == 1
    assert parse_result(second.workspace.out_dir / "NEW-999.json", SKILL, "NEW-999").accepted


@pytest.mark.parametrize("change", ["body", "comment", "before", "candidate", "validity", "answer", "index", "rules", "skill", "contract", "model", "effort", "mode", "product"])
def test_semantic_changes_miss(tmp_path, change):
    c = Case(tmp_path)
    run, task = c.run("a")
    execute(run, task, c.known)
    data = payload(2)
    args = {}
    if change == "body": data["items"][0]["issue"]["description"] = "different"
    if change == "comment": data["items"][0]["events"][0]["after"]["comments"][0]["text"] = "different"
    if change == "before": data["items"][0]["events"][0]["before"]["text"] = "different"
    if change == "candidate": data["items"][0]["candidates"]["srs"][0]["text"] = "different"
    if change == "validity": data["items"][0]["candidates"]["srs"][0]["status"] = "deleted"
    if change == "answer": data["answered_questions"] = [{"question": "value", "answer": "changed"}]
    if change == "model": c.cfg = replace(c.cfg, claude_model="another-model")
    if change == "effort": c.cfg = replace(c.cfg, claude_effort="high")
    if change == "mode": args["on_demand"] = "tc-draft"
    if change == "product": c.cfg = replace(c.cfg, product="Another", profile=None)
    second, task2 = c.run("b", data=data, **args)
    if change in ("rules", "skill"):
        path = second.workspace.root / ("rules/qa-guide.md" if change == "rules" else f".claude/skills/{SKILL}/SKILL.md")
        path.write_text("changed", encoding="utf-8")
    if change == "index": (second.workspace.context_dir / "srs_current.jsonl").write_text("different index", encoding="utf-8")
    if change == "contract":
        (second.workspace.root / ".claude/skills/qa-common-rules/references/output-contract.md").write_text("v2", encoding="utf-8")
    execute(second, task2, c.known)
    assert len(c.calls) == 2 and second.result_cache["hits"] == 0


@pytest.mark.parametrize("bad", ["question", "lab_question", "removed", "insufficient", "unknown_validity", "unreadable", "failed", "gate", "draft_gate", "empty"])
def test_incomplete_results_are_not_cached(tmp_path, bad):
    c = Case(tmp_path)
    def transform(raw):
        if bad == "question": raw["open_questions"] = [{"question": "Need evidence"}]
        if bad == "lab_question": raw["findings"][0]["sections"]["specification"] = {"questions_for_lab": ["Need specification confirmation"]}
        if bad == "removed": raw["findings"][0]["evidence"].append({"source_type": "srs", "location": "VP-999", "validity": "Current"})
        if bad == "insufficient": raw["findings"][0]["verdict"] = "INSUFFICIENT_EVIDENCE"
        if bad == "unknown_validity": raw["findings"][0]["evidence"][0]["validity"] = "Unknown"
        if bad == "gate": raw["gate_status"]["G1"] = "FAIL"
        if bad == "draft_gate": raw["gate_status"]["G1"] = "DRAFT"
        if bad == "empty": raw["findings"] = []
        return raw
    c.transform = transform
    c.runner_ok = bad != "failed"
    data = payload()
    if bad == "unreadable": data["unreadable_documents"] = ["synthetic.xlsx"]
    one, task = c.run("one", data=data)
    execute(one, task, c.known)
    two, task2 = c.run("two", data=data)
    execute(two, task2, c.known)
    assert len(c.calls) >= 2 and two.result_cache["hits"] == 0


def test_cache_current_evidence_revalidation_and_corrupt_fallback(tmp_path):
    c = Case(tmp_path)
    first, task = c.run("one")
    execute(first, task, c.known)
    second, task2 = c.run("two")
    execute(second, task2, KnownIds(issue_ids={"VP-100"}))
    assert len(c.calls) == 2 and second.result_cache["hits"] == 0
    with c.store.storage.connect() as db:
        db.execute("UPDATE ai_cache SET response_json='broken' WHERE cache_key LIKE 'daily_qa:%'")
    third, task3 = c.run("three")
    execute(third, task3, c.known)
    assert len(c.calls) == 3


def test_disabled_and_cancellation_skip_cache(tmp_path):
    c = Case(tmp_path)
    first, task = c.run("one")
    execute(first, task, c.known)
    c.cfg = replace(c.cfg, result_cache_enabled=False)
    second, task2 = c.run("two")
    execute(second, task2, c.known)
    assert len(c.calls) == 2
    c.cfg = replace(c.cfg, result_cache_enabled=True)
    third, task3 = c.run("three")
    c.store.request_cancel("three")
    with pytest.raises(RunCancelled): execute(third, task3, c.known)
    assert len(c.calls) == 2 and third.result_cache["hits"] == 0


def test_fingerprint_excludes_only_operational_ids(tmp_path):
    c = Case(tmp_path)
    run, task = c.run("one")
    write_task_input(run.workspace, task.task_id, task.payload)
    def key(task):
        write_task_input(run.workspace, task.task_id, task.payload)
        return result_cache.fingerprint(task, run.workspace, "vxvue", "NEW_ISSUE", "", "opus", "medium", {})
    original = key(task)
    same = Task("NEW-002", SKILL, payload(5))
    assert key(same) == original
    changed = copy.deepcopy(same)
    changed.payload["items"][0]["events"][0]["after"]["comments"][0]["id"] = "c2"
    assert key(changed) != original


def test_legacy_row_without_fingerprint_is_not_adopted(tmp_path):
    c = Case(tmp_path)
    first, task = c.run("one")
    execute(first, task, c.known)
    with c.store.storage.connect() as db:
        cache_key = db.execute("SELECT cache_key FROM ai_cache").fetchone()[0]
    c.store.storage.cache_set(cache_key, raw_result(task))
    second, task2 = c.run("two")
    execute(second, task2, c.known)
    assert len(c.calls) == 2 and second.result_cache["hits"] == 0


@pytest.mark.parametrize("cancelled", [False, True])
def test_limit_and_cancelled_cli_results_never_enter_cache(tmp_path, cancelled):
    from app.modules.daily_qa.claude_limits import LimitInfo, KIND_SESSION
    c = Case(tmp_path)
    normal = c.produce
    def limited(task, ws):
        c.calls.append(task.task_id)
        return RunnerOutcome(False, 1, 0, limit=None if cancelled else LimitInfo(KIND_SESSION, "", "synthetic limit"), cancelled=cancelled)
    c.produce = limited
    first, task = c.run("limited")
    if cancelled:
        with pytest.raises(RunCancelled): execute(first, task, c.known)
    else:
        execute(first, task, c.known)
    with c.store.storage.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM ai_cache").fetchone()[0] == 0
    c.produce = normal
    second, task2 = c.run("normal")
    execute(second, task2, c.known)
    assert second.claude_calls == 1


def test_summary_reports_reuse_separately(tmp_path):
    c = Case(tmp_path)
    first, task = c.run("one")
    execute(first, task, c.known)
    second, task2 = c.run("two")
    execute(second, task2, c.known)
    summary = second._finish()["summary"]
    assert summary["result_cache_hits"] == 1 and summary["result_cache"]["sources"][0]["run_id"] == "one"
    assert summary["claude_calls"] == summary["token_usage"]["total_tokens"] == 0


def test_comment_access_failure_disables_reuse_and_storage(tmp_path):
    c = Case(tmp_path)
    first, task = c.run("one")
    first.audit["result_cache_unreadable"] = True
    execute(first, task, c.known)
    second, task2 = c.run("two")
    execute(second, task2, c.known)
    assert len(c.calls) == 2
    third, task3 = c.run("three")
    third.audit["result_cache_unreadable"] = True
    execute(third, task3, c.known)
    assert len(c.calls) == 3 and third.result_cache["hits"] == 0


def test_default_setting_is_enabled_and_config_false_is_respected(tmp_path):
    from app.core.config import Settings, Secrets
    from app.modules.daily_qa.settings import load
    default = Settings(root=tmp_path, raw={}, secrets=Secrets())
    assert load(default).result_cache_enabled
    configured = Settings(root=tmp_path, raw={"daily_qa": {"result_cache": {"enabled": False}}}, secrets=Secrets())
    assert not load(configured).result_cache_enabled


@pytest.mark.parametrize("operation", ["cache_get", "cache_set"])
def test_cache_database_failure_keeps_successful_analysis(tmp_path, monkeypatch, operation):
    import sqlite3
    c = Case(tmp_path)
    def unavailable(*args): raise sqlite3.OperationalError("synthetic unavailable")
    monkeypatch.setattr(c.store.storage, operation, unavailable)
    run, task = c.run("one")
    outcome = execute(run, task, c.known)
    assert outcome.ok and outcome.finding_ids and run.claude_calls == 1
    assert "OperationalError" in run.audit["tasks"][0]["cache_error"]


def test_full_spec_body_change_beyond_candidate_excerpt_invalidates(tmp_path):
    from app.core.document_schemas import SpecificationChunk
    from app.modules.daily_qa.intelligence import Corpus
    c = Case(tmp_path)
    common = "same candidate excerpt " * 100
    chunk = SpecificationChunk(chunk_id="doc-1", document_id="doc", text=common + "old tail", page=1, heading="Synthetic")
    corpus = Corpus(c.cfg.product_profile, [], [], [], [chunk])
    first, task = c.run("one")
    first.audit["result_cache_corpus"] = result_cache.corpus_digest(corpus)
    execute(first, task, c.known)
    chunk.text = common + "new tail"
    second, task2 = c.run("two")
    second.audit["result_cache_corpus"] = result_cache.corpus_digest(corpus)
    execute(second, task2, c.known)
    assert len(c.calls) == 2 and second.result_cache["hits"] == 0


def test_unstructured_validation_metadata_cannot_fail_successful_task(tmp_path):
    c = Case(tmp_path)
    def transform(raw):
        raw["findings"][0]["sections"]["validation"] = "unstructured metadata"
        return raw
    c.transform = transform
    one, task = c.run("one")
    assert execute(one, task, c.known).ok
    two, task2 = c.run("two")
    assert execute(two, task2, c.known).ok
    assert len(c.calls) == 2


@pytest.mark.parametrize("bad", ["format", "evidence"])
@pytest.mark.parametrize("existing", [False, True])
def test_cache_miss_preserves_result_file_and_leaves_no_cached_output(tmp_path, bad, existing):
    c = Case(tmp_path)
    first, task = c.run("one")
    execute(first, task, c.known)
    with c.store.storage.connect() as db:
        key = db.execute("SELECT cache_key FROM ai_cache").fetchone()[0]
    known = c.known
    if bad == "format":
        cached = c.store.storage.cache_get(key)
        cached["result"]["findings"][0].pop("summary")
        c.store.storage.cache_set(key, cached)
    else:
        known = KnownIds(issue_ids={"VP-100"})
    second, task2 = c.run("two", "NEW-999", payload(99))
    result_path = second.workspace.out_dir / "NEW-999.json"
    previous = b"synthetic existing result must be preserved"
    if existing:
        result_path.write_bytes(previous)
    assert result_cache.load(c.store.storage, key, result_path, task2, "NEW_ISSUE", known,
                             tuple(second.profile.regression_axes)) is None
    assert result_path.read_bytes() == previous if existing else not result_path.exists()
    assert list(second.workspace.out_dir.iterdir()) == ([result_path] if existing else [])


def test_cache_miss_then_cli_success_without_output_cannot_adopt_cache(tmp_path):
    c = Case(tmp_path)
    first, task = c.run("one")
    execute(first, task, c.known)
    def no_output(task, ws):
        c.calls.append(task.task_id)
        return RunnerOutcome(True, 0, 0)
    c.produce = no_output
    second, task2 = c.run("two", "NEW-999")
    outcome = execute(second, task2, KnownIds(issue_ids={"VP-100"}))
    assert not outcome.ok and not outcome.finding_ids and second.result_cache["hits"] == 0
    assert c.calls == ["NEW-001", "NEW-999", "NEW-999"]
    assert not (second.workspace.out_dir / "NEW-999.json").exists()

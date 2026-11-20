"""Validates REQ-VXVUECHATBOT-026 without credentials or model quota."""
from copy import deepcopy
import json

import pytest

from scripts import qa_skill_eval as evals

CORE_CASES = tuple(case for case in evals.CASES if case.core)


def test_default_cases_include_six_for_each_product_and_original_core():
    assert len({case.id for case in evals.CASES}) == 12
    assert sum(case.prefix == "bellalun_" for case in evals.CASES) == 6
    assert {case.id for case in evals.CASES if case.core} == {"bellalun-id-only", "vxvue-image-page"}


def test_consistency_counts_failures_and_snapshot_drift():
    baseline = {"case": "a", "status": "PASS", "answer": {"value": True}, "snapshot_id": "s",
                "skill_sha256": "sha", "model": "m"}
    assert evals.summarize([baseline] * 3) == [
        {"case": "a", "trials": 3, "passed": 3, "consistent": True},
    ]
    assert evals.summarize([baseline, {**baseline, "status": "FAIL"}])[0]["passed"] == 1
    assert not evals.summarize([baseline, {**baseline, "snapshot_id": "new"}])[0]["consistent"]


def transcript(case):
    sid = "fixed"

    def call(name, arguments, *content, prefix=case.prefix):
        return {
            "type": "mcp_tool_call", "status": "completed", "server": "vxvue-ai",
            "tool": prefix + name, "arguments": arguments,
            "result": {"content": list(content)},
        }

    def block(value):
        return {"type": "text", "text": json.dumps(value)}

    docs = [{"document_id": case.document, "sha12": case.source_hash}] if case.document else []
    status = call("get_status", {}, block({"snapshot_id": sid, "scope": {
        "documents": [] if case.kind == "xipl" else docs,
    }}))
    workflow = [
        call("get_workflow", {"snapshot_id": sid, "section": case.section, **args},
             block({"snapshot_id": sid, "section": case.section, "text": "지침",
                    "truncated": bool(next_), "next_offset": next_}))
        for args, next_ in [({}, 2), ({"offset": 2}, None)]
    ]
    if case.kind == "id_range":
        evidence = call("search_documents", {"snapshot_id": sid, "query": "id-only"}, block({
            "snapshot_id": sid, "results": [{
                "document_id": case.document, "sha256": case.source_hash + "0" * 52,
                "location": {"sheet": case.expected["sheet"], "first_row": 114, "last_row": 400},
                "evidence_status": "id-only-range-not-test-definition",
                "evidence_truncated": False,
            }],
        }))
    elif case.kind == "image":
        evidence = call("get_document_page", {
            "snapshot_id": sid, "document_id": case.document, "page": case.expected["page"],
        }, block({"document_id": case.document, "location": {"page": case.expected["page"]}}),
            block({"page": case.expected["page"], "text_status": "no-extractable-text"}),
            {"type": "image", "mimeType": "image/png", "data": "fixture"})
    elif case.kind == "redmine":
        evidence = call("search_redmine_issues", {"keywords": "내보내기", "page_size": 3, "full_text": True},
                        block({"project_ids": [75], "status": "*", "search_mode": "full_text", "complete": False,
                               "page": 1, "page_size": 3, "errors": []}))
    elif case.kind == "polarion":
        evidence = call("get_polarion_item", {"vp_id": "VP-584", "section": "links"}, block({
            "truncated": False, "text": json.dumps({"response": {
                "data": [{"id": "VXvue/VP-584/parent/VXvue/VP-579"}],
            }}),
        }))
    elif case.kind == "generator":
        evidence = call("get_generator_evidence", {"snapshot_id": sid, "model": "EGEN", "limit": 1}, block({
            "model": "EGEN", "authority": "VSS specification",
            "sources": [{"file": case.document, "sha256": case.source_hash + "0" * 52}],
            "scope": {"tc": {"truncated": True}},
        }))
    else:
        if case.kind == "xipl":
            sid = "xipl-fixed"
            workflow.append(call("get_status", {}, block({"snapshot_id": sid, "scope": {"documents": docs}}),
                                 prefix="xipl_"))
        def chunk(location, value):
            return {
                "document_id": case.document, "sha256": case.source_hash + "0" * 52,
                "location": location, "evidence_truncated": False, "evidence": value,
            }

        rows = [chunk(dict(case.location), "\n".join(case.markers))]
        if case.name_paragraph is not None:
            rows.append(chunk({"paragraph": case.name_paragraph}, case.expected["sop_class"]))
        evidence = call("get_document", {"snapshot_id": sid, "document_id": case.document}, block({
            "snapshot_id": sid, "results": rows,
        }), prefix="xipl_" if case.kind == "xipl" else case.prefix)
    return [status, *workflow, evidence]


@pytest.mark.parametrize("case", evals.CASES, ids=lambda case: case.id)
def test_scores_complete_trace_and_final_answer(case):
    assert evals.grade(case, transcript(case), case.expected)["status"] == "PASS"


@pytest.mark.parametrize("case", CORE_CASES, ids=lambda case: case.id)
def test_flips_wrong_answer_even_with_correct_trace(case):
    answer = deepcopy(case.expected)
    answer["coverage_supported" if case.prefix else "default_oid_confirmed"] = True
    assert evals.grade(case, transcript(case), answer)["status"] == "FAIL"


@pytest.mark.parametrize("case", CORE_CASES, ids=lambda case: case.id)
def test_rejects_missing_continuation_or_mixed_snapshot(case):
    trace = transcript(case)
    assert evals.grade(case, [*trace[:2], trace[-1]], case.expected)["status"] == "FAIL"
    trace[-1]["arguments"]["snapshot_id"] = "new"
    assert evals.grade(case, trace, case.expected)["status"] == "FAIL"


def test_requires_image_and_id_only_status():
    bellalun, vxvue = CORE_CASES
    trace = transcript(vxvue)
    trace[-1]["result"]["content"].pop()
    assert evals.grade(vxvue, trace, vxvue.expected)["status"] == "FAIL"
    trace = transcript(bellalun)
    entry = trace[-1]["result"]["content"][0]
    entry["text"] = entry["text"].replace("id-only-range-not-test-definition", "test-definition")
    assert evals.grade(bellalun, trace, bellalun.expected)["status"] == "FAIL"


@pytest.mark.parametrize("field, value", [("oid", 170), ("matching_buttons", ["Pattern Matching", "Physical Matching"])])
def test_reads_values_from_image(field, value):
    case = evals.CASES[1]
    result = evals.grade(case, transcript(case), {**case.expected, field: value})
    assert result["status"] == "FAIL"
    assert result["checks"]["evidence"]
    assert not result["checks"]["answer." + field]


def test_does_not_accept_identical_id_range_on_wrong_sheet():
    case = evals.CASES[0]
    trace = transcript(case)
    entry = trace[-1]["result"]["content"][0]
    value = json.loads(entry["text"])
    value["results"][0]["location"]["sheet"] = "다른 시트"
    entry["text"] = json.dumps(value)
    assert evals.grade(case, trace, case.expected)["status"] == "FAIL"


def test_changed_source_needs_rebaseline():
    case = evals.CASES[0]
    trace = transcript(case)
    entry = trace[0]["result"]["content"][0]
    entry["text"] = entry["text"].replace(case.source_hash, "new-hash")
    assert evals.grade(case, trace, case.expected)["status"] == "SOURCE_CHANGED"


@pytest.mark.parametrize("case", evals.CASES, ids=lambda case: case.id)
def test_requires_case_specific_evidence(case):
    trace = transcript(case)
    trace[-1]["result"]["content"].clear()
    result = evals.grade(case, trace, case.expected)
    assert result["status"] == "FAIL"
    assert not result["checks"]["evidence"]


def test_xipl_cannot_use_bellalun_snapshot_or_read_before_xipl_status():
    case = next(case for case in evals.CASES if case.kind == "xipl")
    trace = transcript(case)
    trace[-1]["arguments"]["snapshot_id"] = "fixed"
    assert not evals.grade(case, trace, case.expected)["checks"]["pinned_snapshot"]
    trace[-1]["arguments"]["snapshot_id"] = "xipl-fixed"
    trace[-1], trace[-2] = trace[-2], trace[-1]
    assert not evals.grade(case, trace, case.expected)["checks"]["pinned_snapshot"]


def replace_response(trace, **changes):
    last = trace[-1]["result"]["content"][0]
    value = json.loads(last["text"])
    last["text"] = json.dumps({**value, **changes})


def test_redmine_scope_requires_a_successful_project_read():
    case = next(case for case in evals.CASES if case.kind == "redmine")
    trace = transcript(case)
    replace_response(trace, errors=[{"project_id": 75, "error": "timeout"}])
    assert not evals.grade(case, trace, case.expected)["checks"]["evidence"]


def test_redmine_evidence_fingerprint_ignores_only_collection_time():
    case = next(case for case in evals.CASES if case.kind == "redmine")
    trace = transcript(case)
    replace_response(trace, items=[{"id": 1, "subject": "before"}], collected_at="first")
    first = evals.grade(case, trace, case.expected)["live_evidence_sha256"]
    replace_response(trace, collected_at="second")
    assert evals.grade(case, trace, case.expected)["live_evidence_sha256"] == first
    replace_response(trace, items=[{"id": 1, "subject": "after"}])
    assert evals.grade(case, trace, case.expected)["live_evidence_sha256"] != first


def test_polarion_invalid_link_data_cannot_pass():
    case = next(case for case in evals.CASES if case.kind == "polarion")
    trace = transcript(case)
    replace_response(trace, text=json.dumps({"response": {"data": None}}))
    assert not evals.grade(case, trace, case.expected)["checks"]["evidence"]


@pytest.mark.parametrize("case_id", ["bellalun-windows-update", "vxvue-dicom-locator"])
def test_document_location_without_every_required_value_is_not_evidence(case_id):
    case = next(case for case in evals.CASES if case.id == case_id)
    trace = transcript(case)
    values = json.loads(trace[-1]["result"]["content"][0]["text"])
    for row in values["results"]:
        row["evidence"] = "다른 원문"
    replace_response(trace, results=values["results"])
    assert not evals.grade(case, trace, case.expected)["checks"]["evidence"]


def test_dicom_uid_read_alone_is_not_evidence_for_sop_class_name():
    case = next(case for case in evals.CASES if case.id == "vxvue-dicom-locator")
    trace = transcript(case)
    rows = json.loads(trace[-1]["result"]["content"][0]["text"])["results"]
    replace_response(trace, results=rows[:1])
    result = evals.grade(case, trace, case.expected)
    assert result["checks"]["evidence"]
    assert not result["checks"]["name_paragraph"]
    assert result["status"] == "FAIL"


def test_core_repeat_starts_three_trials_for_each_product(monkeypatch, capsys):
    def fake(case, args, codex_home):
        return evals.grade(case, transcript(case), case.expected)

    monkeypatch.setattr(evals, "run_case", fake)
    monkeypatch.setattr("sys.argv", ["qa_skill_eval.py", "--core", "--repeat", "3", "--jobs", "2"])
    assert evals.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert {item["case"] for item in result["summary"]} == {case.id for case in CORE_CASES}
    assert all(item["trials"] == item["passed"] == 3 and item["consistent"] for item in result["summary"])
    for case in CORE_CASES:
        assert [item["trial"] for item in result["results"] if item["case"] == case.id] == [1, 2, 3]


@pytest.mark.parametrize("snapshot, early", [("fixed", True), ("other", False)])
def test_failed_attempts_count_for_order_and_snapshot(snapshot, early):
    case = evals.CASES[0]
    trace = transcript(case)
    failed = deepcopy(trace[-1])
    failed["status"] = "failed"
    failed["arguments"]["snapshot_id"] = snapshot
    trace.insert(1 if early else len(trace) - 1, failed)
    assert evals.grade(case, trace, case.expected)["status"] == "FAIL"


def test_incomplete_cli_event_is_a_run_error():
    case = evals.CASES[0]
    stream = '{"type":"item.completed"}\n{"type":"turn.completed"}'
    assert evals.score_stream(case, stream, 0)["status"] == "RUN_ERROR"


def test_codex_stream_requires_completion_and_uses_last_agent_answer():
    case = evals.CASES[0]
    items = transcript(case)
    events = [{"type": "item.completed", "item": item} for item in items]
    events += [
        {"type": "item.completed", "item": {"type": "agent_message", "text": "진행 중"}},
        {"type": "item.completed", "item": {
            "type": "agent_message", "text": json.dumps(case.expected),
        }},
    ]
    stream = lambda: "\n".join(map(json.dumps, events))
    assert evals.score_stream(case, stream(), 0)["status"] == "RUN_ERROR"
    events.append({"type": "turn.completed", "usage": {"output_tokens": 100}})
    assert evals.score_stream(case, stream(), 0)["status"] == "PASS"
    assert evals.score_stream(case, stream(), 1)["status"] == "RUN_ERROR"


def test_preview_evaluation_uses_pinned_settings_across_desktop_config_changes(tmp_path, monkeypatch):
    from hashlib import sha256
    from types import SimpleNamespace

    config = tmp_path / "config.toml"
    config.write_text('model = "live-model"\n[mcp_servers.vxvue-ai]\nurl = "http://example.test/mcp"\n',
                      encoding="utf-8")
    args = SimpleNamespace(codex="codex", model="fixed-model", timeout=30,
                           config_sha256=sha256(config.read_bytes()).hexdigest(),
                           preview_settings={"model_reasoning_effort": "high"})
    invocations = []

    def execute(command, **kwargs):
        invocations.append((command, kwargs.get("env", {})))
        config.write_text('model = "changed-again"\n', encoding="utf-8")
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(evals.subprocess, "run", execute)
    monkeypatch.setattr(evals, "score_stream", lambda *_: {"status": "PASS"})
    for _ in range(2):
        result = evals.run_case(evals.CASES[0], args, tmp_path, skill=b"test",
                                preview_url="http://127.0.0.1:6000/candidate")
        assert result["status"] == "PASS"
        assert result["model"] == "fixed-model"

    for command, environment in invocations:
        assert environment["CODEX_HOME"] == str(tmp_path)
        assert "--ignore-user-config" in command
        assert 'model_reasoning_effort="high"' in command
        assert "fixed-model" in command
        assert 'mcp_servers.vxvue-ai.url="http://127.0.0.1:6000/candidate"' in command

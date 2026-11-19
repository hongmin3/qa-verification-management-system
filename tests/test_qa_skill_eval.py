"""Validates REQ-VXVUECHATBOT-026 without credentials or model quota."""
from copy import deepcopy
import json

import pytest

from scripts import qa_skill_eval as evals


def transcript(case):
    sid = "fixed"

    def call(name, arguments, *content):
        return {
            "type": "mcp_tool_call", "status": "completed", "server": "vxvue-ai",
            "tool": case.prefix + name, "arguments": arguments,
            "result": {"content": list(content)},
        }

    def block(value):
        return {"type": "text", "text": json.dumps(value)}

    status = call("get_status", {}, block({"snapshot_id": sid, "scope": {"documents": [
        {"document_id": case.document, "sha12": case.source_hash},
    ]}}))
    workflow = [
        call("get_workflow", {"snapshot_id": sid, "section": case.section, **args},
             block({"snapshot_id": sid, "section": case.section, "text": "지침",
                    "truncated": bool(next_), "next_offset": next_}))
        for args, next_ in [({}, 2), ({"offset": 2}, None)]
    ]
    if case.prefix:
        evidence = call("search_documents", {"snapshot_id": sid, "query": "id-only"}, block({
            "snapshot_id": sid, "results": [{
                "document_id": case.document, "sha256": case.source_hash + "0" * 52,
                "location": {"sheet": case.expected["sheet"], "first_row": 114, "last_row": 400},
                "evidence_status": "id-only-range-not-test-definition",
                "evidence_truncated": False,
            }],
        }))
    else:
        evidence = call("get_document_page", {
            "snapshot_id": sid, "document_id": case.document, "page": case.expected["page"],
        }, block({"document_id": case.document, "location": {"page": case.expected["page"]}}),
            block({"page": case.expected["page"], "text_status": "no-extractable-text"}),
            {"type": "image", "mimeType": "image/png", "data": "fixture"})
    return [status, *workflow, evidence]


@pytest.mark.parametrize("case", evals.CASES, ids=lambda case: case.id)
def test_scores_complete_trace_and_final_answer(case):
    assert evals.grade(case, transcript(case), case.expected)["status"] == "PASS"


@pytest.mark.parametrize("case", evals.CASES, ids=lambda case: case.id)
def test_flips_wrong_answer_even_with_correct_trace(case):
    answer = deepcopy(case.expected)
    answer["coverage_supported" if case.prefix else "default_oid_confirmed"] = True
    assert evals.grade(case, transcript(case), answer)["status"] == "FAIL"


@pytest.mark.parametrize("case", evals.CASES, ids=lambda case: case.id)
def test_rejects_missing_continuation_or_mixed_snapshot(case):
    trace = transcript(case)
    assert evals.grade(case, [*trace[:2], trace[-1]], case.expected)["status"] == "FAIL"
    trace[-1]["arguments"]["snapshot_id"] = "new"
    assert evals.grade(case, trace, case.expected)["status"] == "FAIL"


def test_requires_image_and_id_only_status():
    bellalun, vxvue = evals.CASES
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

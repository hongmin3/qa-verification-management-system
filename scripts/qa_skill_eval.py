"""Run and grade read-only QA questions through Codex and the shared MCP."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import tomllib


SERVER = "vxvue-ai"
LIVE_TOOLS = {"bellalun_search_redmine_issues", "get_polarion_item"}


@dataclass(frozen=True)
class Case:
    id: str
    skill: str
    prefix: str
    section: str
    document: str | None
    source_hash: str | None
    question: str
    expected: dict
    evidence_tools: tuple[str, ...]
    kind: str = "document"
    core: bool = False
    location: tuple[tuple[str, str | int], ...] = ()
    markers: tuple[str, ...] = ()
    name_paragraph: int | None = None
    extra_tools: tuple[str, ...] = ()

    @property
    def tools(self):
        return tuple(self.prefix + name for name in ("get_status", "get_workflow", *self.evidence_tools)) + self.extra_tools


BELLALUN_DOC = "(TC) Mammo System 연동 TestCase (개정).xlsx"
VXVUE_DOC = "(사양서) VXvue 사양서2(261119).pdf"
BELLALUN_DICOM = "(매뉴얼) Bellalun Viewer DICOM Conformance Statement.V1.3W1_EN_수정_확인완료.docx"
VXVUE_DICOM = "(매뉴얼) VXvue DICOM Conformance Statement.V4.4W5.docx"
BELLALUN_WU = "(TC) R-25-782_Bellalun Viewer_Windows Update 호환성 검증 Checklist.xlsx"
VXVUE_WU = "(TC) Windows Update 호환성 검증 Checklist_VXvue_R-25-774.xlsx"
XIPL_DOC = "XIPL User Manual.V3.6.0_KO_리뷰완료_수정사항없음.docx"
VSS_DOC = "(사양서) VSS Specification (261023).xlsx"
MAMMO_SHEET = "뷰어_시스템 연동(2D)_1000S_1100B"
DOCUMENT_TOOLS = ("search_documents", "get_document")
TC_TOOLS = (*DOCUMENT_TOOLS, "find_testcases")
CASES = (
    Case(
        "bellalun-id-only", "bellalun-server-ai", "bellalun_", "tc",
        BELLALUN_DOC, "5b08fa2d4c2d",
        f"벨라룬 {BELLALUN_DOC}의 뷰어_시스템 연동(2D)_1000S_1100B 시트에서 "
        "TC_System_Integration_2D_250이 있는 행을 알려줘. 개별 번호가 검색되지 않으면 연속 번호 구간도 확인해 줘. "
        "그 행에 Step과 Expected가 각각 있고, 그 행을 연동 검증 Coverage 근거로 인정할 수 있어?",
        {
            "document": BELLALUN_DOC, "sheet": "뷰어_시스템 연동(2D)_1000S_1100B",
            "row": 251, "tc_id": "TC_System_Integration_2D_250",
            "has_steps": False, "has_expected_results": False, "coverage_supported": False,
        },
        TC_TOOLS, kind="id_range", core=True,
    ),
    Case(
        "vxvue-image-page", "vxvue-ai", "", "spec", VXVUE_DOC, "3313f639733c",
        f"VXvue {VXVUE_DOC}의 PDF 물리 87쪽 화면에서 OID 표시값과 이름에 Matching이 들어간 버튼 라벨을 "
        "위에서 아래 순서대로 읽어줘. 이 화면만으로 OID 기본값까지 확정할 수 있는지도 알려줘.",
        {
            "document": VXVUE_DOC, "page": 87, "oid": 171,
            "matching_buttons": ["Physical Matching", "Pattern Matching"],
            "default_oid_confirmed": False,
        },
        (*DOCUMENT_TOOLS, "get_document_page"), kind="image", core=True,
    ),
    Case(
        "bellalun-defined-tc", "bellalun-server-ai", "bellalun_", "tc", BELLALUN_DOC, "5b08fa2d4c2d",
        f"벨라룬 {BELLALUN_DOC}의 {MAMMO_SHEET} 시트에서 TC_System_Integration_2D_003을 검토해 줘. "
        "시트와 행, Step·Expected 유무, Expected에서 디텍터를 찾지 못했을 때 표시하는 오류 코드(문자와 숫자)를 알려줘.",
        {"document": BELLALUN_DOC, "sheet": MAMMO_SHEET, "row": 4, "has_steps": True,
         "has_expected_results": True, "detector_error_code": "E2200"},
        TC_TOOLS, location=(("sheet", MAMMO_SHEET), ("row", 4)), markers=("O4:", "P4:", "E2200"),
    ),
    Case(
        "bellalun-dicom-locator", "bellalun-server-ai", "bellalun_", "spec", BELLALUN_DICOM, "bd8c6b70a2d4",
        f"벨라룬 사양 문의야. {BELLALUN_DICOM}의 원본 DOCX 문단 439~440에서 "
        "SOP Class 이름과 UID를 읽어줘. UID의 문단 번호와 파일명도 답에 포함해 줘.",
        {"document": BELLALUN_DICOM, "paragraph": 440, "sop_class": "Verification SOP Class",
         "uid": "1.2.840.10008.1.1"},
        DOCUMENT_TOOLS, location=(("paragraph", 440),), markers=("1.2.840.10008.1.1",), name_paragraph=439,
    ),
    Case(
        "bellalun-windows-update", "bellalun-server-ai", "bellalun_", "tc", BELLALUN_WU, "2d859f008c0f",
        f"벨라룬 {BELLALUN_WU}의 TC_WindowsUpdate_01을 검토해 줘. "
        "원본 시트·행과 K열 기록을 알려줘. 오늘 설치한 새 빌드의 실행 로그나 산출물은 없는 상태인데 "
        "이 기록으로 오늘 실행이 통과했다고 확정해도 돼?",
        {"document": BELLALUN_WU, "sheet": "Checklist", "row": 6, "recorded_result": "Pass",
         "current_build_pass_confirmed": False},
        TC_TOOLS, location=(("sheet", "Checklist"), ("row", 6)), markers=("K6: Pass",),
    ),
    Case(
        "bellalun-xipl-boundary", "bellalun-server-ai", "bellalun_", "spec", XIPL_DOC, "e8f877acae9e",
        f"벨라룬 사양 문의 절차를 먼저 적용한 뒤, XIPL 공용 자료 {XIPL_DOC}에서 Smart Autocrop 제목의 "
        "문단 번호를 찾아줘. 원문 조회는 XIPL 공용 문서에 한정해 줘. 이 문서만 근거로 벨라룬에서의 기능 지원과 "
        "라이선스 적용을 각각 확정할 수 있어?",
        {"document": XIPL_DOC, "paragraph": 647, "bellalun_support_confirmed": False,
         "bellalun_license_confirmed": False},
        (), kind="xipl", location=(("paragraph", 647),), markers=("Smart Autocrop",),
        extra_tools=("xipl_get_status", "xipl_search_documents", "xipl_get_document"),
    ),
    Case(
        "bellalun-redmine-scope", "bellalun-server-ai", "bellalun_", "issue", None, None,
        "벨라룬 VDMS Series QA Defect(75)에서 '내보내기'를 전문 검색해 줘. "
        "상태 제한 없이 종료 이슈도 검색 범위에 넣고, 1페이지만 3건 크기로 확인해 줘. "
        "검색한 프로젝트 번호, 종료 이슈를 범위에 넣었는지, 이 조회로 전수 조사 완료를 확정할 수 있는지 답해 줘.",
        {"project_id": 75, "includes_closed": True, "complete_review_confirmed": False},
        ("search_redmine_issues",), kind="redmine",
    ),
    Case(
        "vxvue-stitch-conditions", "vxvue-ai", "", "spec", VXVUE_DOC, "3313f639733c",
        f"VXvue {VXVUE_DOC}의 PDF 물리 82쪽 Stitch Image 조건을 읽어줘. "
        "한 번에 이어 붙일 수 있는 최대 영상 수와 이미 이어 붙인 영상의 재스티칭 허용 여부를 알려줘.",
        {"document": VXVUE_DOC, "page": 82, "max_images": 6, "restitch_allowed": False},
        (*DOCUMENT_TOOLS, "get_document_page"), location=(("page", 82),),
        markers=("최대 6장", "다시 이어 붙일 수 없다"),
    ),
    Case(
        "vxvue-dicom-locator", "vxvue-ai", "", "spec", VXVUE_DICOM, "541f4d521e9d",
        f"VXvue 사양 문의야. {VXVUE_DICOM}의 원본 DOCX 문단 382~383에서 "
        "SOP Class 이름과 UID를 읽어줘. UID의 문단 번호와 파일명도 답에 포함해 줘.",
        {"document": VXVUE_DICOM, "paragraph": 383, "sop_class": "Modality Worklist Information Model - FIND",
         "uid": "1.2.840.10008.5.1.4.31"},
        DOCUMENT_TOOLS, location=(("paragraph", 383),), markers=("1.2.840.10008.5.1.4.31",), name_paragraph=382,
    ),
    Case(
        "vxvue-windows-update", "vxvue-ai", "", "tc", VXVUE_WU, "a52b42596873",
        f"VXvue {VXVUE_WU}의 TC_WindowsUpdate_01을 검토해 줘. "
        "시마주 패키지 행의 원본 시트·행과 K열 기록을 알려줘. 이 기록만 있고 새 빌드 실행 로그가 없을 때 "
        "오늘 새 빌드의 설치·기동 검증이 통과했다고 확정해도 돼?",
        {"document": VXVUE_WU, "sheet": "Checklist", "row": 7, "recorded_result": "Pass",
         "current_build_pass_confirmed": False},
        TC_TOOLS, location=(("sheet", "Checklist"), ("row", 7)), markers=("K7: Pass",),
    ),
    Case(
        "vxvue-generator-scope", "vxvue-ai", "", "integ", VSS_DOC, "ef804acac152",
        "VXvue Generator 연동 검토야. EGEN 근거를 결과당 1건(limit=1)으로 조회해 줘. "
        "모델 이름, 기본 사양 기준이 되는 파일명, 그 결과만으로 EGEN TC를 전부 검토한 것으로 볼 수 있는지 답해 줘.",
        {"model": "EGEN", "authority_document": VSS_DOC, "all_tc_reviewed": False},
        ("get_generator_evidence",), kind="generator",
    ),
    Case(
        "vxvue-polarion-parent", "vxvue-ai", "", "spec", None, None,
        "VXvue 사양 추적이야. Polarion VP-584의 연결 항목 목록(links)에서 직접 parent 관계의 VP 번호를 알려줘. "
        "답변에 조회한 원래 항목 번호, 관계 이름, 연결된 번호를 적어줘.",
        {"vp_id": "VP-584", "relation": "parent", "parent_id": "VP-579"},
        ("get_polarion_item",), kind="polarion",
    ),
)


def json_value(text):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def payloads(call):
    result = call.get("result")
    content = result.get("content") if isinstance(result, dict) else None
    for block in content if isinstance(content, list) else ():
        if isinstance(block, dict) and block.get("type") == "text":
            value = json_value(block.get("text"))
            if isinstance(value, dict):
                yield value


def successful(call):
    result = call.get("result")
    return (
        call.get("status") == "completed"
        and isinstance(result, dict)
        and isinstance(result.get("content"), list)
        and not result.get("is_error", result.get("isError"))
    )


def objects(value):
    return (item for item in value if isinstance(item, dict)) if isinstance(value, list) else ()


def workflow_end(case, calls, sid):
    offset = 0
    for index, call in enumerate(calls):
        args = call["arguments"]
        if not successful(call) or call["tool"] != case.prefix + "get_workflow" or (
            args.get("section"), args.get("offset", 0), args.get("exclude_core", False)
        ) != (case.section, offset, False):
            continue
        for value in payloads(call):
            if value.get("snapshot_id") != sid or value.get("section") != case.section or not value.get("text"):
                continue
            next_offset = value.get("next_offset")
            if value.get("truncated") is False and not next_offset:
                return index
            if value.get("truncated") is True and type(next_offset) is int and next_offset > offset:
                offset = next_offset
                break
    return None


def source_chunks(call, sid, *, require_snapshot=False):
    return (
        row for value in payloads(call)
        if value.get("snapshot_id") in ((sid,) if require_snapshot else (None, sid))
        for row in objects(value.get("results"))
    )


def source_text(case, call, sid, location, markers):
    return any(
        row.get("document_id") == case.document
        and row.get("sha256", "").startswith(case.source_hash)
        and row.get("evidence_truncated") is False and isinstance(row.get("evidence"), str)
        and all(marker in row["evidence"] for marker in markers)
        and isinstance(row.get("location"), dict)
        and all(row["location"].get(key) == expected for key, expected in location)
        for row in source_chunks(call, sid)
    )


def document_evidence(case, call, sid):
    expected = case.expected
    if case.kind == "id_range":
        return any(
            row.get("document_id") == case.document
            and row.get("sha256", "").startswith(case.source_hash)
            and row.get("evidence_status") == "id-only-range-not-test-definition"
            and row.get("evidence_truncated") is False
            and row["location"].get("sheet") == expected["sheet"]
            and row["location"]["first_row"] <= expected["row"] <= row["location"]["last_row"]
            for row in source_chunks(call, sid, require_snapshot=True) if isinstance(row.get("location"), dict)
            and type(row["location"].get("first_row")) is int and type(row["location"].get("last_row")) is int
        )
    if call["tool"] == "get_document_page":
        if (call["arguments"].get("document_id"), call["arguments"].get("page")) != (case.document, expected["page"]):
            return False
        blocks = list(payloads(call))
        return (
            any(value.get("document_id") == case.document and value.get("location", {}).get("page") == expected["page"]
                for value in blocks)
            and any(value.get("page") == expected["page"]
                    and value.get("text_status") == ("no-extractable-text" if case.kind == "image" else "text-available")
                    for value in blocks)
            and any(isinstance(block, dict) and block.get("type") == "image" and block.get("data")
                    for block in call["result"]["content"])
        )
    if case.kind == "image":
        return False
    return source_text(case, call, sid, case.location, case.markers)


def evidence_found(case, call, sid):
    args = call["arguments"]
    if case.kind == "redmine":
        return call["tool"] == "bellalun_search_redmine_issues" and (
            args.get("keywords"), args.get("full_text"), args.get("page", 1), args.get("page_size")
        ) == ("내보내기", True, 1, 3) and any(
            value.get("search_mode") == "full_text" and value.get("project_ids") == [75]
            and value.get("status") == "*" and value.get("complete") is False
            and value.get("errors") == [] and (value.get("page"), value.get("page_size")) == (1, 3)
            for value in payloads(call)
        )
    if case.kind == "polarion":
        if call["tool"] != "get_polarion_item" or (args.get("vp_id"), args.get("section")) != ("VP-584", "links"):
            return False
        for value in payloads(call):
            links = json_value(value.get("text"))
            response = links.get("response") if isinstance(links, dict) else None
            if value.get("truncated") is False and isinstance(response, dict) and any(
                item.get("id") == "VXvue/VP-584/parent/VXvue/VP-579"
                for item in objects(response.get("data"))
            ):
                return True
        return False
    if case.kind == "generator":
        return call["tool"] == "get_generator_evidence" and (args.get("model"), args.get("limit")) == ("EGEN", 1) and any(
            value.get("model") == "EGEN" and value.get("authority", "").startswith("VSS specification")
            and value.get("scope", {}).get("tc", {}).get("truncated") is True
            and any(source.get("file") == case.document and source.get("sha256", "").startswith(case.source_hash)
                    for source in objects(value.get("sources")))
            for value in payloads(call)
        )
    return document_evidence(case, call, sid)


def first_status(calls, tool):
    return next((value for call in calls if successful(call) and call["tool"] == tool
                 for value in payloads(call) if value.get("snapshot_id")), None)


def grade(case, calls, answer):
    report = {"case": case.id, "answer": answer, "tool_calls": len(calls), "checks": {}}
    report["tool_sequence"] = [call.get("tool") for call in calls]
    report["workflow_sections"] = [
        call["arguments"].get("section") for call in calls
        if call.get("tool") == case.prefix + "get_workflow" and isinstance(call.get("arguments"), dict)
    ]
    checks = report["checks"]
    allowed = [call for call in calls if call.get("server") == SERVER
               and call.get("tool") in case.tools and isinstance(call.get("arguments"), dict)]
    checks["product_tools"] = len(allowed) == len(calls)
    status_tool = case.prefix + "get_status"
    status = first_status(allowed, status_tool)
    if status is None:
        report["status"] = "RUN_ERROR" if any(call.get("tool") == status_tool for call in calls) else "FAIL"
        report["reason"] = "제품 상태 조회 결과 없음"
        return report

    sid = status["snapshot_id"]
    report["snapshot_id"] = sid
    xipl_status = first_status(allowed, "xipl_get_status") if case.kind == "xipl" else None
    evidence_status = xipl_status if case.kind == "xipl" else status
    if xipl_status:
        report["xipl_snapshot_id"] = xipl_status["snapshot_id"]
    document = next((doc for doc in (evidence_status or {}).get("scope", {}).get("documents", [])
                     if doc.get("document_id") == case.document), {})
    source_hash = document.get("sha12") or document.get("sha256", "")[:12]
    if case.document and evidence_status is not None and source_hash != case.source_hash:
        report["status"] = "SOURCE_CHANGED"
        report["reason"] = "기준 문서가 목록에 없거나 해시가 달라 정답을 다시 확인해야 함"
        report["source_sha12"] = source_hash or None
        return report

    queries = [call for call in allowed if call["tool"] != status_tool]
    status_ids = {status_tool: sid}
    if xipl_status:
        status_ids["xipl_get_status"] = xipl_status["snapshot_id"]
    current_ids = {}
    pinned = True
    for call in allowed:
        tool = call["tool"]
        if tool in status_ids and successful(call):
            for value in payloads(call):
                if value.get("snapshot_id"):
                    current_ids[tool] = value["snapshot_id"]
                    pinned &= current_ids[tool] == status_ids[tool]
        elif tool not in LIVE_TOOLS and tool not in (status_tool, "xipl_get_status"):
            source = "xipl_get_status" if tool.startswith("xipl_") else status_tool
            pinned &= source in current_ids and call["arguments"].get("snapshot_id") == current_ids[source]
    checks["pinned_snapshot"] = (
        bool(queries) and bool(pinned) and evidence_status is not None
    )
    checks["status_first"] = calls[0].get("tool") == status_tool
    end = workflow_end(case, queries, sid)
    checks["workflow_complete_before_evidence"] = end is not None and all(
        call["tool"] == case.prefix + "get_workflow" for call in queries[:end + 1]
    )
    evidence_calls = [call for call in queries[end + 1:] if successful(call)] if end is not None else []
    source_sid = (evidence_status or status)["snapshot_id"]
    checks["evidence"] = any(evidence_found(case, call, source_sid) for call in evidence_calls)
    if case.name_paragraph is not None:
        checks["name_paragraph"] = any(
            source_text(case, call, source_sid, (("paragraph", case.name_paragraph),), (case.expected["sop_class"],))
            for call in evidence_calls
        )
    if case.kind == "redmine":
        checks["requested_page_only"] = all(
            (call["arguments"].get("page", 1), call["arguments"].get("page_size")) == (1, 3)
            for call in queries if call["tool"] == "bellalun_search_redmine_issues"
        )
    for key, expected in case.expected.items():
        actual = answer.get(key) if isinstance(answer, dict) else None
        checks["answer." + key] = type(actual) is type(expected) and actual == expected
    checks["answer_fields"] = isinstance(answer, dict) and answer.keys() == case.expected.keys()
    report["status"] = "PASS" if all(checks.values()) else "FAIL"
    return report


def score_stream(case, stdout, returncode):
    events = [value for line in stdout.splitlines() if isinstance(value := json_value(line), dict)]
    completed = [event for event in events if event.get("type") in ("turn.completed", "turn.failed")]
    if returncode or not completed or completed[-1]["type"] != "turn.completed":
        return {"case": case.id, "status": "RUN_ERROR", "reason": "Codex 실행 실패 또는 완료 이벤트 누락",
                "exit_code": returncode}
    items = [event.get("item") for event in events if event.get("type") == "item.completed"]
    if any(not isinstance(item, dict) or not isinstance(item.get("type"), str) for item in items):
        return {"case": case.id, "status": "RUN_ERROR", "reason": "Codex 완료 항목의 형식을 읽을 수 없음"}
    calls = [item for item in items if item["type"] == "mcp_tool_call"]
    final = next((item.get("text") for item in reversed(items) if item["type"] == "agent_message"), None)
    try:
        report = grade(case, calls, json_value(final))
    except (AttributeError, KeyError, TypeError):
        return {"case": case.id, "status": "RUN_ERROR", "reason": "MCP 응답에서 필요한 필드 형식을 읽을 수 없음"}
    report["usage"] = completed[-1].get("usage", {})
    return report


def answer_schema(case):
    types = {str: {"type": "string"}, bool: {"type": "boolean"}, int: {"type": "integer"},
             list: {"type": "array", "items": {"type": "string"}}}
    return {"type": "object", "properties": {key: types[type(value)] for key, value in case.expected.items()},
            "required": list(case.expected), "additionalProperties": False}


def run_case(case, args, codex_home):
    started = time.monotonic()
    try:
        skill = (codex_home / "skills" / case.skill / "SKILL.md").read_bytes()
        with (codex_home / "config.toml").open("rb") as file:
            config = tomllib.load(file)
        servers = config.get("mcp_servers", {})
        if SERVER not in servers:
            raise ValueError("Codex 설정에 vxvue-ai MCP가 없습니다.")
        with tempfile.TemporaryDirectory(prefix="qa-skill-eval-") as directory:
            schema = Path(directory) / "answer-schema.json"
            schema.write_text(json.dumps(answer_schema(case)), encoding="utf-8")
            cmd = [
                args.codex, "exec", "--json", "--ephemeral", "--skip-git-repo-check", "--color", "never",
                "--sandbox", "read-only", "--disable", "shell_tool", "--disable", "plugins",
                "--disable", "apps", "--disable", "hooks", "--output-schema", str(schema),
                "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
                "-c", f"mcp_servers.{SERVER}.enabled_tools=" + json.dumps(case.tools),
            ]
            for name in servers.keys() - {SERVER}:
                cmd.extend(["-c", f"mcp_servers.{name}.enabled=false"])
            if args.model:
                cmd.extend(["--model", args.model])
            prompt = (
                "아래는 설치된 QA Skill의 업무 지침이다. 연결된 공용 MCP로 업무 질문에 답해 줘. "
                "최종 답변은 지정된 JSON 필드로 작성해 줘. 도구나 자료가 없어 확인하지 못한 항목을 추정하지 마.\n\n"
                + skill.decode("utf-8-sig") + "\n\n업무 질문\n" + case.question
            )
            child = subprocess.run(
                [*cmd, "-"], cwd=directory, input=prompt, capture_output=True,
                encoding="utf-8", errors="replace", timeout=args.timeout,
            )
        report = score_stream(case, child.stdout, child.returncode)
        report["skill_sha256"] = hashlib.sha256(skill).hexdigest()
        report["model"] = args.model or config.get("model")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        report = {"case": case.id, "status": "RUN_ERROR", "reason": f"실행 준비 또는 연결 오류 ({type(error).__name__})"}
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return report


def summarize(results):
    groups = {}
    for result in results:
        groups.setdefault(result["case"], []).append(result)
    summary = []
    comparable = ("answer", "snapshot_id", "xipl_snapshot_id", "skill_sha256", "model")
    for case_id, trials in groups.items():
        passed = sum(trial["status"] == "PASS" for trial in trials)
        consistent = passed == len(trials) and all(
            trial.get(key) == trials[0].get(key) for trial in trials for key in comparable
        )
        summary.append({"case": case_id, "trials": len(trials), "passed": passed, "consistent": consistent})
    return summary


def main():
    parser = argparse.ArgumentParser(description="벨라룬·VXvue 공용 스킬 답변 회귀시험")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--case", choices=[case.id for case in CASES], help="사례 하나 실행. 기본은 전체 12건")
    selection.add_argument("--core", action="store_true", help="제품별 핵심 사례만 실행")
    parser.add_argument("--repeat", type=int, default=1, help="사례마다 열 새 대화 수")
    parser.add_argument("--jobs", type=int, default=1, help="동시에 실행할 대화 수")
    executable = "codex.exe" if os.name == "nt" else "codex"
    parser.add_argument("--codex", default=shutil.which(executable) or executable, help="Codex 실행 파일 경로")
    parser.add_argument("--model", help="생략하면 사용자 설정의 모델 사용")
    parser.add_argument("--timeout", type=int, default=240, help="질문당 시간 제한(초)")
    parser.add_argument("--report", type=Path, help="JSON 보고서를 저장할 새 파일")
    args = parser.parse_args()
    if os.name == "nt" and Path(args.codex).suffix.lower() != ".exe":
        parser.error("Windows에서는 시간 초과 때 실행을 제어할 수 있도록 codex.exe 경로를 지정하세요.")
    if min(args.timeout, args.repeat, args.jobs) < 1:
        parser.error("시간 제한, 반복 횟수, 동시 실행 수는 1 이상이어야 합니다.")
    if args.report and args.report.exists():
        parser.error("보고서를 저장할 새 파일 경로를 지정하세요.")
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    selected = [case for case in CASES if args.case in (None, case.id) and (not args.core or case.core)]

    def run_trial(job):
        case, trial = job
        return {**run_case(case, args, codex_home), "trial": trial}

    jobs = [(case, trial) for trial in range(1, args.repeat + 1) for case in selected]
    with ThreadPoolExecutor(max_workers=min(args.jobs, len(jobs))) as executor:
        results = list(executor.map(run_trial, jobs))
    summary = summarize(results)
    report = json.dumps({"model_override": args.model, "summary": summary, "results": results},
                        ensure_ascii=False, indent=2)
    if args.report:
        with args.report.open("x", encoding="utf-8") as file:
            file.write(report + "\n")
    print(report)
    if any(result["status"] in ("RUN_ERROR", "SOURCE_CHANGED") for result in results):
        return 2
    return int(any(not case["consistent"] for case in summary))


if __name__ == "__main__":
    raise SystemExit(main())

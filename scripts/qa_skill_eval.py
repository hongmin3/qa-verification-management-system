"""Run and grade two read-only QA questions through Codex and the shared MCP."""

import argparse
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


@dataclass(frozen=True)
class Case:
    id: str
    skill: str
    prefix: str
    section: str
    document: str
    source_hash: str
    question: str
    expected: dict
    evidence_tools: tuple[str, ...]

    @property
    def tools(self):
        return tuple(self.prefix + name for name in ("get_status", "get_workflow", *self.evidence_tools))


BELLALUN_DOC = "(TC) Mammo System 연동 TestCase (개정).xlsx"
VXVUE_DOC = "(사양서) VXvue 사양서2(261119).pdf"
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
        ("search_documents", "find_testcases", "get_document"),
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
        ("search_documents", "get_document", "get_document_page"),
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


def evidence_found(case, call, sid):
    expected = case.expected
    if case.prefix:
        return any(
            row.get("document_id") == case.document
            and row.get("sha256", "").startswith(case.source_hash)
            and row.get("evidence_status") == "id-only-range-not-test-definition"
            and row.get("evidence_truncated") is False
            and row["location"].get("sheet") == expected["sheet"]
            and row["location"]["first_row"] <= expected["row"] <= row["location"]["last_row"]
            for value in payloads(call) if value.get("snapshot_id") == sid
            for row in value.get("results", []) if isinstance(row.get("location"), dict)
            and type(row["location"].get("first_row")) is int and type(row["location"].get("last_row")) is int
        )
    if call["tool"] != "get_document_page" or (
        call["arguments"].get("document_id"), call["arguments"].get("page")
    ) != (case.document, expected["page"]):
        return False
    return (
        any(value.get("document_id") == case.document and value.get("location", {}).get("page") == expected["page"]
            for value in payloads(call))
        and any(value.get("page") == expected["page"] and value.get("text_status") == "no-extractable-text"
                for value in payloads(call))
        and any(isinstance(block, dict) and block.get("type") == "image" and block.get("data")
                for block in call["result"]["content"])
    )


def grade(case, calls, answer):
    report = {"case": case.id, "answer": answer, "tool_calls": len(calls), "checks": {}}
    checks = report["checks"]
    allowed = [call for call in calls if call.get("server") == SERVER
               and call.get("tool") in case.tools and isinstance(call.get("arguments"), dict)]
    checks["product_tools"] = len(allowed) == len(calls)
    status_tool = case.prefix + "get_status"
    status = next((value for call in allowed if successful(call) and call["tool"] == status_tool
                   for value in payloads(call) if value.get("snapshot_id")), None)
    if status is None:
        report["status"] = "RUN_ERROR" if any(call.get("tool") == status_tool for call in calls) else "FAIL"
        report["reason"] = "제품 상태 조회 결과 없음"
        return report

    sid = status["snapshot_id"]
    report["snapshot_id"] = sid
    document = next((doc for doc in status.get("scope", {}).get("documents", [])
                     if doc.get("document_id") == case.document), {})
    if document.get("sha12") != case.source_hash:
        report["status"] = "SOURCE_CHANGED"
        report["reason"] = "기준 문서가 목록에 없거나 해시가 달라 정답을 다시 확인해야 함"
        report["source_sha12"] = document.get("sha12")
        return report

    queries = [call for call in allowed if call["tool"] != status_tool]
    checks["pinned_snapshot"] = (
        bool(queries) and all(call["arguments"].get("snapshot_id") == sid for call in queries)
        and all(value.get("snapshot_id") == sid for call in allowed if successful(call) and call["tool"] == status_tool
                for value in payloads(call) if value.get("snapshot_id"))
    )
    checks["status_first"] = calls[0].get("tool") == status_tool
    end = workflow_end(case, queries, sid)
    checks["workflow_complete_before_evidence"] = end is not None and all(
        call["tool"] == case.prefix + "get_workflow" for call in queries[:end + 1]
    )
    checks["evidence"] = end is not None and any(
        successful(call) and evidence_found(case, call, sid) for call in queries[end + 1:]
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
    report = grade(case, calls, json_value(final))
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
            servers = tomllib.load(file).get("mcp_servers", {})
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
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        report = {"case": case.id, "status": "RUN_ERROR", "reason": f"실행 준비 또는 연결 오류 ({type(error).__name__})"}
    report["elapsed_seconds"] = round(time.monotonic() - started, 2)
    return report


def main():
    parser = argparse.ArgumentParser(description="벨라룬·VXvue 공용 스킬 답변 회귀시험")
    parser.add_argument("--case", choices=[case.id for case in CASES], help="생략하면 두 질문 모두 실행")
    executable = "codex.exe" if os.name == "nt" else "codex"
    parser.add_argument("--codex", default=shutil.which(executable) or executable, help="Codex 실행 파일 경로")
    parser.add_argument("--model", help="생략하면 사용자 설정의 모델 사용")
    parser.add_argument("--timeout", type=int, default=240, help="질문당 시간 제한(초)")
    parser.add_argument("--report", type=Path, help="JSON 보고서를 저장할 새 파일")
    args = parser.parse_args()
    if os.name == "nt" and Path(args.codex).suffix.lower() != ".exe":
        parser.error("Windows에서는 시간 초과 때 실행을 제어할 수 있도록 codex.exe 경로를 지정하세요.")
    if args.timeout < 1:
        parser.error("시간 제한은 1초 이상이어야 합니다.")
    if args.report and args.report.exists():
        parser.error("보고서를 저장할 새 파일 경로를 지정하세요.")
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    selected = [case for case in CASES if args.case in (None, case.id)]
    results = [run_case(case, args, codex_home) for case in selected]
    report = json.dumps({"model_override": args.model, "results": results}, ensure_ascii=False, indent=2)
    if args.report:
        with args.report.open("x", encoding="utf-8") as file:
            file.write(report + "\n")
    print(report)
    if any(result["status"] in ("RUN_ERROR", "SOURCE_CHANGED") for result in results):
        return 2
    return int(any(result["status"] != "PASS" for result in results))


if __name__ == "__main__":
    raise SystemExit(main())

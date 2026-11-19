"""Read the active QA workflow and evaluate unpublished guide bytes on its data."""

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import tempfile
from threading import Lock, Thread
import tomllib
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import qa_skill_compare as gate, qa_skill_eval as evals


LIBRARIES = {"VXvue": "vxvue-ai", "Bellalun Viewer": "bellalun-server-ai"}
RULE_KINDS = {"qa_rules", "instruction_prompt", "skill_instruction"}
PREVIEW_TOOLS = {tool for case in evals.CASES for tool in case.tools}
EVIDENCE_DOCUMENTS = {case.document for case in evals.CASES if case.document}
DOCUMENT_QUERIES = {"get_document", "get_document_page", "search_documents", "find_testcases"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def rule_asset(asset):
    return asset.get("kind") in RULE_KINDS


def stage_changed_rules(product, assets, existing, allow_prune, path_for):
    if product not in LIBRARIES:
        return {}
    desired = {(row["kind"], row["file_name"]): row for row in assets if rule_asset(row)}
    if allow_prune and any(key[0] == "qa_rules" for key in existing) and not any(
        key[0] == "qa_rules" for key in desired
    ):
        raise ValueError("운영 통합 QA 지침을 수집 목록에서 제거할 수 없습니다.")
    changed = {}
    for key, asset in desired.items():
        if asset.get("sha256") and existing.get(key) == asset["sha256"]:
            continue
        data = path_for(asset).read_bytes()
        if asset.get("error") or digest(data) != asset.get("sha256"):
            raise ValueError("수집된 지침 원문의 상태 또는 해시가 다릅니다.")
        changed[key] = data
    if not changed and allow_prune and set(desired) < {key for key in existing if key[0] in RULE_KINDS}:
        raise ValueError("운영 지침 삭제만 있는 변경은 검증할 후보 지침이 필요합니다.")
    return changed


def read_operating_inputs(server, product, snapshot=None):
    """Read the committed manifest or a frozen snapshot and verify each input against it."""
    cfg = dict(line.split("=", 1) for line in (ROOT / ".deploy.env").read_text(encoding="utf-8-sig").splitlines()
               if re.match(r"^[A-Z_]+=", line))
    target = cfg["DEPLOY_SSH_HOST"]
    expected_host = urlsplit(cfg.get("DEPLOY_QA_BASE_URL") or server).hostname
    if urlsplit(server).hostname != expected_host or (
        not cfg.get("DEPLOY_QA_BASE_URL") and expected_host != target
    ):
        raise ValueError("운영 지침을 읽을 SSH 대상과 MCP 주소가 다릅니다.")
    if snapshot is not None and not re.fullmatch(r"(?:bellalun-)?[0-9a-f]{24}", snapshot):
        raise ValueError("운영 스냅샷 ID 형식을 확인하세요.")
    remote = r'''
import base64
import hashlib
import json
from pathlib import Path
if sid is None:
    root = Path("data/product_knowledge") / ("vxvue" if product == "VXvue" else "bellalun-viewer")
    records = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))["assets"]
else:
    root = Path("data") / ("vxvue_desktop/snapshots" if product == "VXvue" else "shared_knowledge/bellalun/snapshots") / sid
    manifest = root / ("manifest.json" if product == "VXvue" else "index-manifest.json")
    records = json.loads(manifest.read_text(encoding="utf-8"))["documents"]
results = []
for record in records:
    if record["kind"] not in ("qa_rules", "skill_instruction", "instruction_prompt"):
        continue
    original = (root / "original").resolve()
    name = record["file_name"] if sid is None else record["name"]
    file = (original / record["kind"] / name) if sid is None else (original / record["document_id"])
    if record.get("error") or not file.resolve().is_relative_to(original):
        raise ValueError("snapshot path")
    data = file.read_bytes()
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("snapshot digest")
    results.append([record["kind"], name, base64.b64encode(data).decode("ascii")])
print(json.dumps(results))
'''
    process = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", f"{cfg['DEPLOY_SSH_USER']}@{target}",
         "cd " + shlex.quote(cfg["DEPLOY_TARGET_DIRECTORY"]) + " && .venv/bin/python -"],
        input=(f"product = {product!r}\nsid = {snapshot!r}\n" + remote).encode("ascii"),
        capture_output=True, timeout=30,
    )
    if process.returncode:
        raise ValueError("운영 서버의 동결된 QA 지침 원본을 읽지 못했습니다.")
    return {(kind, name): base64.b64decode(data, validate=True) for kind, name, data in json.loads(process.stdout)}


def workflow_inputs(product, files, snapshot):
    from app.core.qa_rule_source import select_rule_documents
    from app.core.qa_workflow import validate_guide
    from app.core.skill_knowledge import parse_bundle

    def text(data):
        value = data.decode("utf-8-sig")
        if not value.strip():
            raise ValueError("QA 지침 원본이 비어 있습니다.")
        return value

    instructions = [text(data) for (kind, _), data in files.items() if kind == "skill_instruction"]
    if len(instructions) != 1:
        raise ValueError("운영 Skill 지침 동기화본이 정확히 하나여야 합니다.")
    rules = [(kind, name, data) for (kind, name), data in files.items() if kind != "skill_instruction"]
    metadata = None
    if product == "VXvue":
        with tempfile.TemporaryDirectory(prefix="qa-rules-select-") as folder:
            docs = []
            for index, (kind, name, data) in enumerate(rules):
                path = Path(folder) / str(index)
                path.write_bytes(data)
                docs.append({"kind": kind, "name": name, "path": path})
            selected = select_rule_documents(docs, require_prompt=True)
            rules_text = "".join(f"\n\n## {doc['name']}\n{doc['path'].read_text(encoding='utf-8-sig')}"
                                 for doc in selected)
        instructions[0] = instructions[0].replace("\r\n", "\n").replace("\r", "\n")
        metadata = parse_bundle(instructions[0])
    else:
        selected = [text(data) for kind, _, data in rules if kind == "qa_rules"]
        if len(selected) != 1:
            raise ValueError("Bellalun 통합 QA 지침이 정확히 하나여야 합니다.")
        rules_text = selected[0]
    errors = validate_guide(rules_text, product)["errors"]
    if errors:
        raise ValueError("후보 또는 운영 QA 섹션 구성을 확인하세요.")
    return {"id": snapshot, "rules": rules_text, "skill_text": instructions[0], "skill_metadata": metadata}


def rpc(client, config, message):
    response = client.post(config["url"], json=message, headers={
        **config.get("http_headers", {}), "Accept": "application/json, text/event-stream",
    })
    response.raise_for_status()
    return response.json() if response.content else None


def workflow_result(value):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": False}


def restrict_document_reply(reply):
    """Keep QA policy input on the workflow tool in both arms."""
    if not isinstance(reply, dict) or not isinstance(reply.get("result"), dict):
        return reply
    for block in reply["result"].get("content", []):
        if block.get("type") != "text":
            continue
        value = evals.json_value(block.get("text"))
        if not isinstance(value, dict):
            continue
        for field in ("results", "documents", "matched_documents"):
            if isinstance(value.get(field), list):
                value[field] = [item for item in value[field] if isinstance(item, dict)
                                and item.get("document_id") in EVIDENCE_DOCUMENTS]
        block["text"] = json.dumps(value, ensure_ascii=False)
    return reply


def renderer(client, config, product, before):
    """Check the shared local renderer against real pages before making any candidate pages."""
    from app.modules.bellalun_shared.service import workflow as bellalun_workflow
    from app.modules.vxvue_chatbot.desktop import workflow as vxvue_workflow
    from app.modules.vxvue_usage.service import OWNER_INSTRUCTIONS

    library = LIBRARIES[product]
    tool = gate.STATUS_TOOLS[library].replace("status", "workflow")
    renders = ([bellalun_workflow] if product != "VXvue" else
               [vxvue_workflow, lambda current, args: vxvue_workflow(current, args, OWNER_INSTRUCTIONS)])
    for section in sorted({case.section for case in evals.CASES if case.skill == library}):
        offset = 0
        while offset is not None:
            args = {"snapshot_id": before["id"], "section": section, "offset": offset, "limit": 24000}
            result = rpc(client, config, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                         "params": {"name": tool, "arguments": args}})["result"]
            original = next(evals.payloads({"result": result}), {})
            keys = ("text", "snapshot_id", "section", "truncated", "next_offset", "total_chars", "selected_sections")
            local_args = {key: value for key, value in args.items() if key != "snapshot_id"}
            renders = [render for render in renders for page in [render(before, local_args)]
                       if all(original.get(key) == page.get(key) for key in keys)]
            if not renders:
                raise ValueError("로컬 지침 조합이 현재 운영 응답과 일치하지 않습니다.")
            nxt = original["next_offset"]
            if nxt is not None and (type(nxt) is not int or nxt <= offset):
                raise ValueError("운영 지침의 이어 읽기 위치가 유효하지 않습니다.")
            offset = nxt
    return tool, renders[0]


@contextmanager
def preview_bridge(client, config, tool, render, candidate, snapshots, *, preview_id=None):
    token = secrets.token_urlsafe(24)
    library_status = {sid: status for library, status in gate.STATUS_TOOLS.items() if (sid := snapshots.get(library))}
    cached = {}
    cache_lock = Lock()

    def read_evidence(name, message):
        # The same frozen status, image or live RPC is shared by the two arms.
        stable_reads = (*gate.STATUS_TOOLS.values(), *evals.LIVE_TOOLS, "get_document_page")
        if name not in stable_reads:
            return rpc(client, config, message)
        key = json.dumps(message["params"], sort_keys=True)
        with cache_lock:
            if key not in cached:
                reply = rpc(client, config, message)
                if not isinstance(reply, dict) or not isinstance(reply.get("result"), dict) or reply["result"].get("isError"):
                    return reply
                cached[key] = reply
            return {**cached[key], "id": message["id"]}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_error(405)

        do_DELETE = do_GET

        def do_POST(self):
            if self.path not in (f"/{token}/baseline", f"/{token}/candidate"):
                self.send_error(404)
                return
            message = {}
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 262144:
                    raise ValueError("MCP 요청 크기를 확인하세요.")
                message = json.loads(self.rfile.read(size))
                method, params = message["method"], message.get("params", {})
                if method == "tools/call":
                    name, args = params["name"], params.get("arguments", {})
                    if name not in PREVIEW_TOOLS:
                        raise ValueError("평가에 허용한 읽기 도구만 사용할 수 있습니다.")
                    document_query = name.removeprefix("bellalun_").removeprefix("xipl_") in DOCUMENT_QUERIES
                    if document_query and args.get("document_id") and args["document_id"] not in EVIDENCE_DOCUMENTS:
                        raise ValueError("평가의 지정 근거 문서만 조회할 수 있습니다.")
                    if "snapshot_id" in args:
                        status = ("bellalun_get_status" if name.startswith("bellalun_") else
                                  "xipl_get_status" if name.startswith("xipl_") else "get_status")
                        if library_status.get(args["snapshot_id"]) != status:
                            raise ValueError("평가 시작 때 확인한 자료 판을 사용하세요.")
                    if name == tool and self.path.endswith("/candidate"):
                        if args.get("snapshot_id") != candidate["id"]:
                            raise ValueError("후보 지침에 고정된 자료 판을 사용하세요.")
                        raw = render(candidate, {key: value for key, value in args.items() if key != "snapshot_id"})
                        if preview_id:
                            raw["qa_guide_preview_id"] = preview_id
                        reply = {"jsonrpc": "2.0", "id": message["id"], "result": workflow_result(raw)}
                    else:
                        reply = read_evidence(name, message)
                        if document_query:
                            reply = restrict_document_reply(reply)
                elif method in ("initialize", "ping", "notifications/initialized", "notifications/cancelled", "tools/list"):
                    reply = rpc(client, config, message)
                    if method == "tools/list":
                        reply["result"]["tools"] = [item for item in reply["result"]["tools"] if item["name"] in PREVIEW_TOOLS]
                else:
                    raise ValueError("지원하지 않는 미리보기 요청입니다.")
            except (ValueError, KeyError, AttributeError, TypeError, OSError, httpx.HTTPError):
                reply = {"jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                         "error": {"code": -32000, "message": "미리보기 자료 판 또는 읽기 연결을 확인하세요."}}
            data = json.dumps(reply, ensure_ascii=False).encode("utf-8") if reply is not None else b""
            self.send_response(200 if data else 202)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/{token}"
        finally:
            server.shutdown()
            thread.join()


def compare_upload(product, server, staged, desired, allow_prune, *, jobs=2, codex=None, operating=None):
    """Return a persisted verdict for the actual upload bytes; raise on unreadable setup."""
    server = gate.server_root(server)
    home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    config_source = (home / "config.toml").read_bytes()
    config = tomllib.loads(config_source.decode("utf-8"))
    mcp = config["mcp_servers"][evals.SERVER]
    if mcp.get("url", "").rstrip("/") != server + "/vxvue-desktop/mcp" or not config.get("model"):
        raise ValueError("업로드와 평가가 같은 운영 MCP에 연결되는지 확인하세요.")
    binary = "codex.exe" if os.name == "nt" else "codex"
    executable = codex or shutil.which(binary)
    if not executable and os.name == "nt":
        default = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/OpenAI/Codex/bin/codex.exe"
        executable = str(default) if default.is_file() else None
    if not executable:
        raise ValueError("지침 비교를 실행할 Codex 실행 파일을 찾지 못했습니다.")
    args = SimpleNamespace(codex=executable, model=config["model"], config_sha256=digest(config_source), timeout=420)
    library = LIBRARIES[product]
    with httpx.Client(timeout=40, follow_redirects=False, trust_env=False) as client:
        snapshots = gate.capture_snapshots(client, mcp)
        sid = snapshots[library]
        baseline = read_operating_inputs(server, product, sid)
        committed = read_operating_inputs(server, product) if operating is None else operating
        if any(committed.get(key) != data for key, data in baseline.items()):
            raise ValueError("동결 스냅샷과 운영 커밋의 지침이 다릅니다.")
        candidate = {key: data for key, data in committed.items() if not allow_prune or key in desired}
        candidate.update(staged)
        if {key: digest(data) for key, data in candidate.items() if key in desired} != desired:
            raise ValueError("운영 스냅샷과 수집 목록 사이의 지침 해시가 다릅니다.")
        before = workflow_inputs(product, baseline, sid)
        after = workflow_inputs(product, candidate, sid)
        tool, render = renderer(client, mcp, product, before)
        skills = gate.download_baseline(client, server)
        preview_id = secrets.token_hex(16)
        with preview_bridge(client, mcp, tool, render, after, snapshots, preview_id=preview_id) as bridge:
            tasks = [(case, phase, trial, variant) for case, phase, trial in gate.matrix()
                     for variant in ("baseline", "candidate")]

            def evaluate(job):
                case, phase, trial, variant = job
                result = evals.run_case(case, args, home, skill=skills[case.skill], preview_url=f"{bridge}/{variant}")
                return variant, {**result, "phase": phase, "trial": trial}

            results = {"baseline": [], "candidate": []}
            with ThreadPoolExecutor(max_workers=jobs) as pool:
                for variant, result in pool.map(evaluate, tasks):
                    results[variant].append(result)
        report = gate.compare(results["baseline"], results["candidate"], snapshots)
        preview_cases = {case.id for case in evals.CASES if case.skill == library}
        for row in results["candidate"]:
            if row["case"] in preview_cases and row.get("qa_guide_preview_ids") != [preview_id]:
                report["problems"].append(f"{row['case']} {row['phase']}/{row['trial']} 후보 지침 응답 확인 실패")
        try:
            if (gate.capture_snapshots(client, mcp) != snapshots or gate.download_baseline(client, server) != skills
                    or read_operating_inputs(server, product) != committed):
                report["problems"].append("비교 도중 운영 자료 판 또는 스킬이 변경됨")
        except gate.READ_ERRORS:
            report["problems"].append("운영 자료 판 재확인 실패")
    if report["problems"]:
        report["status"] = "INCOMPARABLE"
    path = gate.local_report_dir() / (datetime.now().strftime("%Y-%m-%d_%H%M%S_%f") + "-guide.json")
    report.update(product=product, snapshots=snapshots, preview_id=preview_id, **results, guide_sha256={
        side: [{"kind": kind, "file_name": name, "sha256": digest(data)} for (kind, name), data in inputs.items()]
        for side, inputs in (("baseline", baseline), ("candidate", candidate))
    })
    gate.save(report, path)
    return report, path


def main():
    parser = argparse.ArgumentParser(description="운영 지침과 로컬 후보 QA MD를 게시 없이 비교")
    parser.add_argument("--server", required=True)
    parser.add_argument("--product", choices=LIBRARIES, required=True)
    parser.add_argument("--asset", nargs=3, action="append", metavar=("KIND", "NAME", "PATH"), required=True)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    if args.jobs < 1 or any(kind not in RULE_KINDS for kind, _, _ in args.asset):
        parser.error("지침 종류와 동시 실행 수를 확인하세요.")
    staged = {(kind, name): Path(path).read_bytes() for kind, name, path in args.asset}
    try:
        result, path = compare_upload(args.product, args.server, staged,
                                      {key: digest(data) for key, data in staged.items()}, False, jobs=args.jobs)
    except (*gate.READ_ERRORS, subprocess.SubprocessError) as error:
        print(f"QA 지침 비교 실행 실패 ({type(error).__name__})")
        return 2
    print(f"QA 지침 비교 {result['status']}. 보고서 {path}")
    return {"PASS": 0, "REGRESSION": 1, "INCOMPARABLE": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())

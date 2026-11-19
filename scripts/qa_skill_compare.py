"""Compare the deployed QA skills with candidates before sending release files."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import tomllib
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import qa_skill_eval as evals, sync_server_skills as skills


SKILL_PATHS = {
    "vxvue-ai": "claude-desktop/skill/vxvue-ai/SKILL.md",
    "bellalun-server-ai": "bellalun-skill/bellalun-server-ai/SKILL.md",
}
STATUS_TOOLS = {"vxvue-ai": "get_status", "bellalun-server-ai": "bellalun_get_status", "xipl": "xipl_get_status"}
READ_ERRORS = (OSError, ValueError, KeyError, TypeError, httpx.HTTPError, skills.SyncError)


def matrix():
    return [(case, "full", 1) for case in evals.CASES] + [
        (case, "core", trial) for trial in range(1, 4) for case in evals.CASES if case.core
    ]


def compare(baseline, candidate, snapshots=None):
    """Compare like inputs. Report old failures separately from new ones."""
    tasks = matrix()
    expected = {(case.id, phase, trial) for case, phase, trial in tasks}
    indexed = [
        {(row["case"], row["phase"], row["trial"]): row for row in stage}
        for stage in (baseline, candidate)
    ]
    report = {"status": "INCOMPARABLE", "regressions": [], "cases": [], "problems": []}
    problems = report["problems"]
    if any(len(stage) != len(expected) or set(rows) != expected
           for stage, rows in zip((baseline, candidate), indexed)):
        problems.append("전체 12건과 핵심 3회 조합의 결과가 누락되었거나 중복됨")
        return report

    seen_snapshots = {}
    models = set()
    grouped = {}
    for case, phase, trial in tasks:
        rows = [stage[case.id, phase, trial] for stage in indexed]
        for side, row in zip(("baseline", "candidate"), rows):
            models.add(row.get("model"))
            if row.get("status") not in ("PASS", "FAIL"):
                problems.append(f"{side} {case.id} {phase}/{trial} 실행 결과 {row.get('status')}")
            seen_snapshots.setdefault(case.skill, set()).add(row.get("snapshot_id"))
            if case.kind == "xipl":
                seen_snapshots.setdefault("xipl", set()).add(row.get("xipl_snapshot_id"))
        if case.kind in ("redmine", "polarion") and (
            not rows[0].get("live_evidence_sha256")
            or rows[0]["live_evidence_sha256"] != rows[1].get("live_evidence_sha256")
        ):
            problems.append(f"{case.id} 실시간 근거가 다르거나 누락됨")
        grouped.setdefault((case.id, phase), []).append(rows)

    if len(models) != 1 or None in models:
        problems.append("동일 모델을 확인할 수 없음")
    for library, ids in seen_snapshots.items():
        if len(ids) != 1 or None in ids or (snapshots is not None and ids != {snapshots.get(library)}):
            problems.append(f"{library} 자료 판이 다르거나 누락됨")

    for (case_id, phase), trials in grouped.items():
        before, after = (sum(rows[index]["status"] == "PASS" for rows in trials) for index in range(2))
        verdict = "REGRESSION" if after < before else "IMPROVED" if after > before else (
            "PASS" if after == len(trials) else "EXISTING_FAILURE"
        )
        item = {"case": case_id, "phase": phase, "trials": len(trials),
                "baseline_passed": before, "candidate_passed": after, "status": verdict}
        report["cases"].append(item)
        if verdict == "REGRESSION":
            report["regressions"].append(item)
    if not problems:
        report["status"] = "REGRESSION" if report["regressions"] else "PASS"
    return report


def server_root(address):
    endpoint = urlsplit(address)
    if (endpoint.scheme not in ("http", "https") or not endpoint.hostname or endpoint.username is not None
            or endpoint.query or endpoint.fragment):
        raise ValueError("운영 QA 서버 주소를 확인하세요.")
    return address.rstrip("/")


def download_baseline(client, server):
    def fetch(url):
        response = client.get(url)
        response.raise_for_status()
        return response.content

    return {name: skills.unpack(name, skills.download(server, name, fetch))["SKILL.md"] for name in SKILL_PATHS}


def capture_snapshots(client, mcp_config):
    snapshots = {}
    for library, tool in STATUS_TOOLS.items():
        response = client.post(mcp_config["url"], json={
            "jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": {}},
        }, headers={**mcp_config.get("http_headers", {}), "Accept": "application/json, text/event-stream"})
        response.raise_for_status()
        reply = response.json()["result"]
        if reply.get("isError"):
            raise ValueError(f"{library} 운영 자료 판을 읽지 못했습니다.")
        values = list(evals.payloads({"result": reply}))
        sid = next((value["snapshot_id"] for value in values if value.get("snapshot_id")), None)
        if not sid:
            raise ValueError(f"{library} 운영 자료 판이 없습니다.")
        snapshots[library] = sid
    return snapshots


def inspect(client, server, candidate, args, codex_home):
    baseline = download_baseline(client, server)
    hashes = {label: {name: hashlib.sha256(text).hexdigest() for name, text in variant.items()}
              for label, variant in (("baseline", baseline), ("candidate", candidate))}
    remote_hashes = getattr(args, "baseline_hashes", None)
    if remote_hashes is not None and remote_hashes != hashes["baseline"]:
        raise ValueError("SSH 배포 대상과 운영 서버 ZIP의 스킬 해시가 다릅니다.")
    changed = sorted(name for name in candidate if candidate[name] != baseline[name])
    if not changed and not args.force:
        return {"status": "UNCHANGED", "skill_sha256": hashes, "changed": changed}

    config_path = codex_home / "config.toml"
    config_data = config_path.read_bytes()
    args.config_sha256 = hashlib.sha256(config_data).hexdigest()
    config = tomllib.loads(config_data.decode("utf-8"))
    mcp_config = config["mcp_servers"][evals.SERVER]
    if mcp_config.get("url", "").rstrip("/") != server + "/vxvue-desktop/mcp":
        raise ValueError("배포 대상과 Codex가 사용하는 QA MCP 주소가 다릅니다.")
    args.model = args.model or config.get("model")
    if not args.model:
        raise ValueError("비교에 사용할 모델을 지정하세요.")
    snapshots = capture_snapshots(client, mcp_config)
    jobs = [
        (side, source, case, phase, trial) for case, phase, trial in matrix()
        for side, source in (("baseline", baseline), ("candidate", candidate))
    ]

    def evaluate(job):
        side, source, case, phase, trial = job
        return side, {**evals.run_case(case, args, codex_home, skill=source[case.skill]), "phase": phase, "trial": trial}

    results = {"baseline": [], "candidate": []}
    with ThreadPoolExecutor(max_workers=min(args.jobs, len(jobs))) as executor:
        for side, result in executor.map(evaluate, jobs):
            results[side].append(result)
    report = compare(results["baseline"], results["candidate"], snapshots)
    report.update(skill_sha256=hashes, changed=changed, snapshots=snapshots, model=args.model, **results)
    try:
        if config_path.read_bytes() != config_data:
            report["problems"].append("비교 도중 Codex 설정이 변경됨")
        if download_baseline(client, server) != baseline or capture_snapshots(client, mcp_config) != snapshots:
            report["problems"].append("비교 도중 운영 스킬 또는 자료 판이 변경됨")
    except READ_ERRORS as error:
        report["problems"].append(f"운영 상태 재확인 실패 ({type(error).__name__})")
    if report["problems"]:
        report["status"] = "INCOMPARABLE"
    return report


def local_report_dir():
    state = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "state")
    return state / "QA-Skill-Eval" / "comparisons"


def save(report, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".qa-compare-", delete=False) as file:
        temp = Path(file.name)
        file.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="벨라룬·VXvue 운영 스킬 배포 전 비교")
    parser.add_argument("--server", required=True, help="운영 QA 서버 기본 주소")
    parser.add_argument("--candidate-deploy", type=Path, default=ROOT / "deploy", help="후보 deploy 폴더")
    parser.add_argument("--force", action="store_true", help="바이트가 같은 스킬도 비교 검증")
    binary = "codex.exe" if os.name == "nt" else "codex"
    parser.add_argument("--codex", default=shutil.which(binary) or binary)
    parser.add_argument("--model")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--baseline-sha", nargs=2, metavar=("VXVUE", "BELLALUN"),
                        help="SSH 배포 대상에서 읽은 두 스킬의 SHA256")
    args = parser.parse_args()
    if min(args.jobs, args.timeout) < 1 or (os.name == "nt" and Path(args.codex).suffix.lower() != ".exe"):
        parser.error("동시 실행 수·시간 제한과 Codex 실행 파일을 확인하세요.")
    if args.report and args.report.exists():
        parser.error("비교 보고서를 저장할 새 파일 경로를 입력하세요.")
    if args.baseline_sha and not all(re.fullmatch(r"[0-9a-fA-F]{64}", digest) for digest in args.baseline_sha):
        parser.error("운영 스킬 SHA256 형식을 확인하세요.")
    args.baseline_hashes = (
        dict(zip(SKILL_PATHS, (digest.lower() for digest in args.baseline_sha))) if args.baseline_sha else None
    )
    report_path = args.report or local_report_dir() / (datetime.now().strftime("%Y-%m-%d_%H%M%S_%f") + ".json")
    codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        server = server_root(args.server)
        candidate = {name: (args.candidate_deploy / path).read_bytes().replace(b"\r\n", b"\n")
                     for name, path in SKILL_PATHS.items()}
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            report = inspect(client, server, candidate, args, codex_home)
    except READ_ERRORS as error:
        # Do not serialize request objects, addresses or authentication headers.
        message = str(error) if type(error) is ValueError else type(error).__name__
        report = {"status": "INCOMPARABLE", "problems": [message], "regressions": []}
    save(report, report_path)
    print(f"스킬 비교 {report['status']}. 보고서 {report_path}")
    for change in report.get("regressions", []):
        print(f"신규 실패 {change['case']} ({change['phase']}) {change['baseline_passed']} → {change['candidate_passed']}")
    for problem in report.get("problems", []):
        print(problem)
    return {"UNCHANGED": 0, "PASS": 0, "REGRESSION": 1, "INCOMPARABLE": 2}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())

"""B4 Redmine 이슈 등록 (Section 29.10) — 사용자가 해당 초안을 승인한 뒤에만 실행한다.

기본은 시험 실행(--dry-run, 네트워크 쓰기 없음): 초안 검증 → 전송 payload·draft_sha256 생성 → 첨부 확인 → 계획 출력.
실제 등록은 --execute 와 승인한 초안의 --approved-sha 가 모두 있어야 한다(초안이 바뀌면 해시가 달라져 거부).

순서(실행 시): 중복 확인(GET) → 등록(POST, 쓰기 1) → 번호 확인 → 공유폴더 Defect\\#번호 생성·복사·크기 확인
             → 본문 [Log, Data] 경로에 번호 기입 + 댓글(PUT, 쓰기 2, 한 요청으로 묶음) → 재조회로 저장값 확인
쓰기는 최대 2회(승인 1회당 3회 제한 안). 등록 응답이 실패여도 이슈가 생겼는지 다시 조회해 중복 등록을 막는다.

API 키는 환경변수 REDMINE_API_KEY(없으면 Windows 사용자 환경변수)에서 읽고 출력·저장하지 않는다.
검증 규칙은 서버 validate_redmine_draft와 같은 원본(app/modules/vxvue_chatbot/redmine_draft_rules.py)을 경로로 읽는다.

입력 JSON 예(draft.json):
{
  "subject": "(DK INNOGEN) [에뮬레이터연동] ...",
  "description": "[Issue Title]\\n...\\n[Log, Data]\\n\\\\\\\\10.1.1.100\\\\...\\\\Defect\\\\#",
  "project_id": 61, "tracker_id": 7, "priority_id": 2,
  "custom_fields": [{"id": 4, "value": "C"}, {"id": 20, "value": "Always"}, {"id": 54, "value": "2767"},
                    {"id": 84, "value": "VSS.Vieworks(DK)"}],
  "attachments": [{"path": "C:\\\\work\\\\25575\\\\01_현상요약.mp4"}],
  "notes": ""
}

예:
    python register_redmine_issue.py draft.json --out out\\reg            # 시험 실행
    python register_redmine_issue.py draft.json --out out\\reg --execute --approved-sha <sha>
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import copy_shared, say, write_json, write_utf8  # noqa: E402

REDMINE = "http://sw.vieworks.com/redmine"
GRADE_FIELD = 4
RULES_FILE = Path(__file__).resolve().parents[2] / "app" / "modules" / "vxvue_chatbot" / "redmine_draft_rules.py"
# 원시 문자열이 백슬래시로 끝나면 문법 오류가 나므로 경로는 조각으로 조립한다.
SHARE_ROOT = PureWindowsPath("\\\\10.1.1.100\\전사 공유폴더", "2. 인증", "506. Device History Record(Medical)",
                             "8. 제품 검증(V&V)", "4. SW파트", "01.SW_Validation", "VXvue", "2.Test_Case")
NUMBER_SLOT = re.compile(r"(\\Defect\\#)(\d*)[ \t]*$", re.M)


def load_rules():
    spec = importlib.util.spec_from_file_location("redmine_draft_rules", RULES_FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def api_key() -> str:
    key = os.environ.get("REDMINE_API_KEY", "")
    if not key and os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = str(winreg.QueryValueEx(k, "REDMINE_API_KEY")[0])
        except OSError:
            key = ""
    return key.strip()


class Redmine:
    def __init__(self, base: str, key: str):
        self.base, self.key, self.writes = base.rstrip("/"), key, 0

    def call(self, method: str, path: str, body=None, params=None):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"X-Redmine-API-Key": self.key, "Content-Type": "application/json"})
        if method != "GET":
            self.writes += 1
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                text = resp.read().decode("utf-8")
                return resp.status, (json.loads(text) if text.strip() else {})
        except urllib.error.HTTPError as exc:
            return exc.code, {"error": exc.read().decode("utf-8", "replace")[:500]}

    def find_by_subject(self, project_id: int, subject: str):
        status, data = self.call("GET", "/issues.json", params={"project_id": project_id, "status_id": "*",
                                                                 "subject": "~" + subject[:120], "sort": "id:desc", "limit": 10})
        if status != 200:
            return None, status
        return [i for i in data.get("issues", []) if i.get("subject") == subject], status


def fill_number(description: str, number: int) -> str:
    new, n = NUMBER_SLOT.subn(lambda m: m.group(1) + str(number), description)
    if n != 1:
        raise ValueError(f"[Log, Data]의 Defect\\# 자리가 {n}개입니다(1개여야 함).")
    return new


def share_folder(description: str, number: int) -> PureWindowsPath:
    """[Log, Data]의 Defect 번호 자리 줄에서 Defect 아래 #번호 폴더 경로를 만든다."""
    line = next(l.strip() for l in description.splitlines() if NUMBER_SLOT.search(l.strip()))
    base = re.sub(r"#\d*\s*$", "", line).rstrip("\\")  # ...\Defect
    return PureWindowsPath(base, f"#{number}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("draft", type=Path, help="초안 JSON(UTF-8)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--execute", action="store_true", help="실제 등록(쓰기). 없으면 시험 실행")
    ap.add_argument("--approved-sha", help="사용자가 승인한 초안의 draft_sha256")
    ap.add_argument("--base-url", default=REDMINE)
    args = ap.parse_args(argv)

    draft = json.loads(args.draft.read_text(encoding="utf-8-sig"))
    # Grade(cf 4)는 기본 C로 등록한다(Section 29.8). 사용자가 지정한 값은 그대로 쓴다.
    fields = draft.setdefault("custom_fields", [])
    if not any(isinstance(f, dict) and f.get("id") == GRADE_FIELD for f in fields):
        fields.append({"id": GRADE_FIELD, "value": "C"})
    rules = load_rules()
    v = rules.validate(draft.get("subject"), draft.get("description"), draft.get("project_id", 61),
                       draft.get("tracker_id"), draft.get("priority_id"), draft.get("category_id"),
                       draft.get("custom_fields"), issue_number_known=False)
    attachments = []
    for a in draft.get("attachments", []):
        p = Path(a["path"])
        attachments.append({"path": str(p), "exists": p.is_file(), "size": p.stat().st_size if p.is_file() else None,
                            "name": a.get("name") or p.name})
    report = {"mode": "execute" if args.execute else "dry-run", "validation": {k: v[k] for k in ("ok", "errors", "warnings")},
              "draft_sha256": v.get("draft_sha256"), "attachments": attachments, "writes": 0, "steps": []}
    if v["ok"]:
        write_json(args.out / "payload.json", v["payload"])  # 실제 전송용(가리지 않은 원문). PC에만 둔다.
    missing = [a["path"] for a in attachments if not a["exists"]]
    if not NUMBER_SLOT.search(draft.get("description") or ""):
        report["validation"]["errors"].append("[Log, Data] 경로가 Defect\\# 로 끝나야 등록 후 번호를 채울 수 있습니다.")
    if missing:
        report["validation"]["errors"].append("첨부 파일 없음: " + ", ".join(missing))
    plan_ok = v["ok"] and not report["validation"]["errors"]
    report["plan"] = ["GET 중복 확인(같은 제목)", "POST /issues.json (쓰기 1)", "이슈 번호 확인",
                      f"공유폴더 {SHARE_ROOT}\\...\\Defect\\#번호 생성, 첨부 {len(attachments)}개 복사·크기 확인",
                      "PUT /issues/번호.json: [Log, Data] 번호 기입" + (" + 댓글" if draft.get("notes") else "") + " (쓰기 2)",
                      "GET 재조회로 제목·본문 경로·필드 저장값 확인"]

    def finish(code):
        write_json(args.out / "result.json", report)
        lines = [f"# Redmine 등록 {'실행' if args.execute else '시험 실행'} 결과", "",
                 f"- 검증: {'통과' if report['validation']['ok'] and not report['validation']['errors'] else '실패'}",
                 f"- draft_sha256: {report['draft_sha256'] or '-'}", f"- 쓰기 횟수: {report['writes']}"]
        lines += [f"- 오류: {e}" for e in report["validation"]["errors"]] + [f"- 경고: {w}" for w in report["validation"]["warnings"]]
        lines += ["", "## 첨부", ""] + [f"- {a['name']} ({a['size']} bytes){'' if a['exists'] else ' — 없음'}" for a in attachments]
        lines += ["", "## 절차", ""] + [f"{i}. {s}" for i, s in enumerate(report["plan"], 1)]
        if report["steps"]:
            lines += ["", "## 실행 기록", ""] + [f"- {s}" for s in report["steps"]]
        out = write_utf8(args.out / "result.md", "\n".join(lines) + "\n")
        say(f"[{report['mode']}] 검증 {'통과' if plan_ok else '실패'} · sha {str(report['draft_sha256'])[:12]} · 쓰기 {report['writes']}회 -> {out}")
        return code

    if not plan_ok:
        return finish(1)
    if not args.execute:
        report["steps"].append("시험 실행: 네트워크 쓰기 없음. 승인받을 때 draft_sha256을 함께 제시한다.")
        return finish(0)

    # ---- 실제 등록 -------------------------------------------------------------
    if args.approved_sha != v["draft_sha256"]:
        report["validation"]["errors"].append("--approved-sha가 현재 초안 해시와 다릅니다. 바뀐 초안은 다시 승인받아야 합니다.")
        return finish(3)
    key = api_key()
    if not key:
        report["validation"]["errors"].append("REDMINE_API_KEY가 없습니다. 등록하지 않았습니다.")
        return finish(4)
    rm = Redmine(args.base_url, key)
    issue = v["payload"]["issue"]
    dup, st = rm.find_by_subject(issue["project_id"], issue["subject"])
    if dup is None:
        report["steps"].append(f"중복 확인 실패(HTTP {st}) — 등록 중단")
        return finish(5)
    if dup:
        report["steps"].append(f"같은 제목 이슈가 이미 있음: #{dup[0]['id']} — 등록 중단")
        return finish(5)
    status, data = rm.call("POST", "/issues.json", {"issue": issue})
    report["writes"] = rm.writes
    number = (data.get("issue") or {}).get("id") if status in (200, 201) else None
    if not number:
        again, _ = rm.find_by_subject(issue["project_id"], issue["subject"])  # 실패 응답이어도 생성됐는지 확인
        number = again[0]["id"] if again else None
        report["steps"].append(f"POST 응답 HTTP {status}; 재조회 결과 {'#' + str(number) + ' 생성됨' if number else '생성 안 됨'}")
        if not number:
            return finish(6)
    report["steps"].append(f"등록 #{number}")
    folder = Path(str(share_folder(issue["description"], number)))
    folder.mkdir(parents=True, exist_ok=True)
    copies = [copy_shared(Path(a["path"]), folder / a["name"]) for a in attachments]
    report["copies"] = copies
    bad = [c for c in copies if not c["match"]]
    report["steps"].append(f"공유폴더 {folder}: {len(copies)}개 복사, 크기 불일치 {len(bad)}개")
    body = {"issue": {"description": fill_number(issue["description"], number)}}
    if draft.get("notes"):
        body["issue"]["notes"] = draft["notes"]
    status, _ = rm.call("PUT", f"/issues/{number}.json", body)
    report["writes"] = rm.writes
    report["steps"].append(f"본문 경로 번호 기입{' + 댓글' if draft.get('notes') else ''}: HTTP {status}")
    status, data = rm.call("GET", f"/issues/{number}.json", params={"include": "journals"})
    saved = data.get("issue") or {}
    checks = {"subject": saved.get("subject") == issue["subject"],
              "log_path": f"Defect\\#{number}" in (saved.get("description") or ""),
              "custom_fields": all(any(f["id"] == c["id"] and str(f.get("value")) == str(c["value"])
                                       for f in saved.get("custom_fields", [])) for c in issue.get("custom_fields", []))}
    report["verify"] = checks
    report["steps"].append("재조회 확인: " + ", ".join(f"{k}={'OK' if ok else 'FAIL'}" for k, ok in checks.items()))
    return finish(0 if all(checks.values()) and not bad else 7)


if __name__ == "__main__":
    raise SystemExit(main())

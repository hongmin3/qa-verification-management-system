"""B3 VXvue·VSS 연동 환경 정보 (운영 4).

출력:
  1) 설치 버전: Windows 설치 목록(Uninstall 레지스트리)의 VXvue·VSS.* 표시 이름·버전
  2) 현재 선택된 Generator 모델·포트: 최신 VSS 세션 로그의
       `Generator device connector created. [ID: n] [Name: X] (ConnectionType: .., Port: [COMn], ...)` 줄
     — VSS가 실제로 연결을 만든 모델이므로 이것을 '선택 모델'로 쓴다.
  3) VSS.Vieworks.xml `<DeviceList>`: 설정 가능한 모델 목록. 선택 모델과 구분한다.
     XML 안에서 선택값으로 보이는 항목을 찾으면 로그 ID와 대조하고, 대조할 수 없으면 '미확정'으로 표시한다.

Database 폴더는 C: 또는 D: 아래에서 찾는다(--database로 지정 가능).

예:
    python env_info.py --out out\\env
"""
from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import fmt, line_times, say, vss_session_start, write_json, write_utf8, find_database  # noqa: E402

CONNECTOR = re.compile(r"Generator device connector created\. \[ID: *(\d+)\] \[Name: *([^\]]+)\] *\(([^)]*)\)")
VERSION = re.compile(r"Program Name: *([^,]+), Version: *([\d.]+)")
CONNECTED = re.compile(r"Generator state changed: \[([^\]]+)\] -> \[([^\]]+)\]")
PRODUCT = re.compile(r"VXvue|VSS\.|VX\.?LIVE|VXSetup", re.I)


def installed_products():
    rows = []
    try:
        import winreg
    except ImportError:
        return rows
    keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    seen = set()
    for hive, path in keys:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                sub = winreg.OpenKey(root, winreg.EnumKey(root, i))
                name = winreg.QueryValueEx(sub, "DisplayName")[0]
            except OSError:
                continue
            if not PRODUCT.search(str(name)):
                continue
            def val(key):
                try:
                    return str(winreg.QueryValueEx(sub, key)[0])
                except OSError:
                    return ""
            item = (name, val("DisplayVersion"))
            if item in seen:
                continue
            seen.add(item)
            rows.append({"name": name, "version": val("DisplayVersion"), "install_date": val("InstallDate"),
                         "location": val("InstallLocation")})
    return sorted(rows, key=lambda r: r["name"])


def latest_session(vss_dir: Path):
    logs = [p for p in vss_dir.glob("Vieworks_*.log") if vss_session_start(p)] if vss_dir.is_dir() else []
    return max(logs, key=vss_session_start) if logs else None


def session_info(path: Path):
    info = {"log": str(path), "session_start": fmt(vss_session_start(path))}
    lines = line_times(path, "vss")
    for t, line in lines:
        m = VERSION.search(line)
        if m and "vss_version" not in info:
            info["program"], info["vss_version"] = m.group(1).strip(), m.group(2)
        m = CONNECTOR.search(line)
        if m:
            fields = dict(re.findall(r"(\w+): *\[?([^\],]+)\]?", m.group(3)))
            info["selected"] = {"id": m.group(1), "name": m.group(2).strip(), "connection": fields.get("ConnectionType"),
                                "port": fields.get("Port"), "baud": fields.get("BaudRate"), "logged_at": fmt(t),
                                "source": "VSS 로그 connector 줄"}
        m = CONNECTED.search(line)
        if m:
            info["last_state"] = {"from": m.group(1), "to": m.group(2), "at": fmt(t)}
    if lines:
        info["last_line_at"] = fmt(lines[-1][0])
    return info


def xml_devices(xml_path: Path):
    """<DeviceList>의 모델 목록과, 목록 밖에서 Device ID를 가리키는 선택값 후보."""
    out = {"xml": str(xml_path), "devices": [], "selected_candidates": []}
    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, OSError) as exc:
        out["error"] = f"XML 읽기 실패: {exc}"
        return out
    root = tree.getroot()
    lists = [e for e in root.iter() if e.tag.lower() == "devicelist"]
    in_list = set()
    for dl in lists:
        for dev in list(dl):
            in_list.update(id(x) for x in dev.iter())
            attrs = {k.lower(): v for k, v in dev.attrib.items()}
            child = {c.tag.lower(): (c.text or "").strip() for c in dev}
            dev_id = attrs.get("id") or attrs.get("deviceid") or child.get("id") or child.get("deviceid")
            name = attrs.get("name") or child.get("name") or attrs.get("model") or child.get("model")
            out["devices"].append({"tag": dev.tag, "id": dev_id, "name": name})
    ids = {d["id"] for d in out["devices"] if d["id"]}
    for e in root.iter():
        if id(e) in in_list:
            continue
        for key, value in [(e.tag, (e.text or "").strip())] + list(e.attrib.items()):
            if re.search(r"device|generator|select|current|model", key, re.I) and value in ids:
                out["selected_candidates"].append({"element": e.tag, "key": key, "value": value})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database", type=Path, help="Database 폴더(기본: C:/D: 자동 탐색)")
    ap.add_argument("--vss-log", type=Path, help="특정 VSS 세션 로그(기본: 최신 세션)")
    ap.add_argument("--xml", type=Path, help="VSS.Vieworks.xml 경로(기본: Database/Integration/VSS.Vieworks)")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    db = args.database or find_database("log/VSS.Vieworks")
    vss_dir = db / "log" / "VSS.Vieworks"
    xml_path = args.xml or (db / "Integration" / "VSS.Vieworks" / "VSS.Vieworks.xml")

    result = {"database": str(db), "installed": installed_products()}
    log = args.vss_log or latest_session(vss_dir)
    result["vss_session"] = session_info(log) if log and log.is_file() else {"error": f"VSS 로그 없음: {vss_dir}"}
    result["xml"] = xml_devices(xml_path) if xml_path.is_file() else {"error": f"XML 없음: {xml_path}"}

    sel = result["vss_session"].get("selected")
    cands = result["xml"].get("selected_candidates", [])
    if sel and any(c["value"] == sel["id"] for c in cands):
        verdict = f"확정: {sel['name']} (ID {sel['id']}, {sel['port']}) — 로그와 XML 선택값 일치"
    elif sel:
        verdict = (f"로그 기준 {sel['name']} (ID {sel['id']}, {sel['connection']} {sel['port']}). "
                   + ("XML 선택값 후보와 다름 — 확인 필요" if cands else "XML 선택값 위치 미확정"))
    else:
        verdict = "미확정 — VSS 로그에 connector 줄 없음"
    result["selected_model"] = verdict

    md = ["# VXvue 연동 환경 정보", "", f"- Database: {db}", "", "## 설치 버전(Windows 설치 목록)", ""]
    md += [f"- {r['name']} {r['version']} (설치일 {r['install_date'] or '-'})" for r in result["installed"]] or ["- 해당 제품 없음"]
    vs = result["vss_session"]
    md += ["", "## 현재 선택된 Generator", "", f"- 판정: {verdict}"]
    if "error" in vs:
        md.append(f"- {vs['error']}")
    else:
        md += [f"- VSS 로그: {Path(vs['log']).name} (세션 시작 {vs['session_start']}, 마지막 줄 {vs.get('last_line_at', '-')})",
               f"- 로그의 프로그램 버전: {vs.get('program', '-')} {vs.get('vss_version', '-')}",
               f"- 마지막 Generator 상태 변경: {vs['last_state']['from']} -> {vs['last_state']['to']} ({vs['last_state']['at']})"
               if vs.get("last_state") else "- 마지막 Generator 상태 변경: 기록 없음"]
    xm = result["xml"]
    md += ["", "## VSS.Vieworks.xml <DeviceList> (설정 가능한 모델 목록 — 선택 모델 아님)", ""]
    if "error" in xm:
        md.append(f"- {xm['error']}")
    else:
        md += [f"- ID {d['id'] or '?'}: {d['name'] or d['tag']}" for d in xm["devices"]] or ["- DeviceList 항목 없음"]
        md.append(f"- 선택값 후보: {', '.join(c['element'] + '=' + c['value'] for c in cands) or '미확정'}")
    out = write_utf8(args.out / "env_info.md", "\n".join(md) + "\n")
    write_json(args.out / "env_info.json", result)
    say(f"[완료] {verdict} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

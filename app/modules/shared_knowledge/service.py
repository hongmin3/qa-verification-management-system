"""XIPL-only immutable document snapshots, served through authenticated MCP.

Reuses the existing corpus and read tools without loading VXvue QA rules.
Source manifests and documents are never modified here.
"""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Lock

from app.core.snapshot_files import publish_directory

from app.modules.vxvue_chatbot.mcp_server import tools
from app.modules.vxvue_chatbot.skill.scripts.corpus import Corpus

_lock = Lock()
_snapshots = OrderedDict()
_ALLOWED = {"get_status", "search_documents", "get_document", "get_document_page"}
_KINDS = {"manual", "release_note"}
_INSTRUCTIONS = ("VXvue와 Bellalun이 공유하는 XIPL 문서입니다. 같은 snapshot_id로 원문을 조회하세요. "
                 "뷰어별 적용 사양과 QA 지침은 해당 제품 도구로 따로 확인하세요. "
                 "검색 결과는 지원 여부나 실제 제품 실행 결과를 확정하지 않습니다. "
                 "DOCX 문단·표 위치는 렌더링된 Word 쪽수가 아닙니다.")


def reset_cache():
    with _lock:
        _snapshots.clear()


def definitions():
    result = []
    for tool in tools():
        if tool["name"] not in _ALLOWED:
            continue
        tool = json.loads(json.dumps(tool))
        for viewer_input in ('compact', 'columns'):
            tool['inputSchema']['properties'].pop(viewer_input, None)
        name = tool["name"]
        tool["name"] = "xipl_" + name
        tool["description"] = "XIPL 공용 문서(VXvue·Bellalun): " + (
            "먼저 문서 목록·해시·snapshot_id·추출 한계를 확인한다." if name == "get_status"
            else tool["description"].replace("VXvue 사양·매뉴얼·TC 검색", "XIPL 매뉴얼·개정 이력 검색"))
        if name != "get_status":
            tool["inputSchema"]["properties"]["snapshot_id"] = {"type": "string"}
            tool["inputSchema"]["required"].append("snapshot_id")
        result.append(tool)
    return result


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(project_root, snapshot_id=None):
    project_root = Path(project_root).resolve()
    with _lock:
        if snapshot_id is not None:
            found = _snapshots.get((str(project_root), snapshot_id))
            if found is None:
                raise ValueError("XIPL 지식 판을 찾을 수 없습니다. xipl_get_status부터 다시 조회하세요.")
            return found
        base = project_root / "data/product_knowledge/xipl"
        manifest = json.loads((base / "manifest.json").read_text(encoding="utf-8-sig"))
        if manifest.get("product", "XIPL") != "XIPL":
            raise ValueError("XIPL 수집 목록의 제품을 확인하세요.")
        records, payloads = [], []
        for asset in manifest.get("assets", []):
            if asset.get("kind") not in _KINDS:
                continue
            name = asset.get("file_name")
            if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name or ":" in name:
                raise ValueError("XIPL 문서 이름이 올바르지 않습니다.")
            source = base / "original" / asset["kind"] / name
            if asset.get("error") or source.is_symlink() or not source.resolve().is_relative_to(base.resolve()):
                raise ValueError("XIPL 원문 경로 또는 수집 실패 상태를 확인하세요.")
            data = source.read_bytes()
            digest = _digest(data)
            if digest != asset.get("sha256"):
                raise ValueError("XIPL 원문 해시가 수집 목록과 다릅니다. 수집 완료 후 다시 조회하세요.")
            if name in {r["document_id"] for r in records}:
                raise ValueError("XIPL 문서 ID가 중복되었습니다.")
            records.append({"document_id": name, "name": name, "kind": asset["kind"], "sha256": digest})
            payloads.append((name, data))
        if not records:
            raise ValueError("XIPL 수집본이 없습니다. 문서 수집 상태를 확인하세요.")
        if _KINDS - {r["kind"] for r in records}:
            raise ValueError("XIPL 필수 매뉴얼 또는 개정 이력이 없습니다. 수집 상태를 확인하세요.")
        identity = "xipl-" + _digest(json.dumps(records, sort_keys=True, ensure_ascii=False).encode("utf8"))[:24]
        key = (str(project_root), identity)
        if key in _snapshots:
            return _snapshots[key]
        destination = project_root / "data/shared_knowledge/xipl/snapshots"
        destination.mkdir(parents=True, exist_ok=True)
        final = destination / identity
        with tempfile.TemporaryDirectory(prefix="building-", dir=destination) as temp:
            building = Path(temp)
            original = building / "original"
            original.mkdir()
            for name, data in payloads:
                (original / name).write_bytes(data)
            status = Corpus(original, building / "index.sqlite3",
                            document_kinds={r["document_id"]: r["kind"] for r in records}).get_status()
            if status["scope"].get("failed_files") or status["scope"].get("errors"):
                raise ValueError("XIPL 원문 일부를 추출하지 못했습니다. 새 지식 판을 공개하지 않았습니다.")
            if final.exists():
                for record in records:
                    if _digest((final / "original" / record["document_id"]).read_bytes()) != record["sha256"]:
                        raise ValueError("저장된 XIPL 지식 판의 해시를 확인하세요.")
            else:
                publish_directory(building, final)
                # building 경로로 만든 색인의 root를 최종 경로로 맞춘다. 맞추지 않으면 첫 조회가 전체를 다시 파싱한다(REQ-VXVUECHATBOT-022).
                from app.modules.vxvue_chatbot.knowledge import _rebase_index
                _rebase_index(final / "index.sqlite3", final / "original")
        value = {"id": identity, "root": str(final / "original"), "index": str(final / "index.sqlite3"),
                 "documents": records, "failures": [], "rules": "", "skill_text": "", "skill_metadata": None}
        _snapshots[key] = value
        while len(_snapshots) > 8:
            _snapshots.popitem(last=False)
        return value


def call(name, arguments, project_root):
    schema = next((t["inputSchema"] for t in definitions() if t["name"] == name), None)
    if schema is None or not isinstance(arguments, dict) or set(arguments) - set(schema["properties"]):
        raise ValueError("허용된 XIPL 읽기 도구와 입력을 확인하세요.")
    if any(k not in arguments for k in schema["required"]):
        raise ValueError("필수 XIPL 조회 항목이 빠졌습니다. xipl_get_status부터 조회하세요.")
    args = dict(arguments)
    sid = args.pop("snapshot_id", None)
    base_name = name.removeprefix("xipl_")
    if base_name != "get_status" and (not isinstance(sid, str) or not sid):
        raise ValueError("xipl_get_status가 반환한 snapshot_id가 필요합니다.")
    current = snapshot(project_root, sid)
    _verify_snapshot(current)
    if "document_id" in args and args["document_id"] not in {r["document_id"] for r in current["documents"]}:
        raise ValueError("해당 XIPL 지식 판 목록의 문서 ID만 조회할 수 있습니다.")
    if base_name == "get_document_page" and not args["document_id"].lower().endswith(".pdf"):
        raise ValueError("DOCX는 xipl_get_document로 문단·표 위치를 확인하세요. 렌더링된 쪽수는 제공하지 않습니다.")
    from app.modules.vxvue_chatbot.desktop import DesktopTools
    session = DesktopTools(current, [], Path(project_root) / "data/shared_knowledge/unused-trace")
    session.corpus = Corpus(current["root"], current["index"],
                            document_kinds={r["document_id"]: r["kind"] for r in current["documents"]})
    # XIPL keeps its existing row metadata contract; compact views belong to VXvue/Bellalun.
    value = session.call(base_name, {**args, 'compact': False})
    _verify_snapshot(current)
    if isinstance(value, dict):
        value.update(snapshot_id=current["id"], library="XIPL", instructions=_INSTRUCTIONS)
    return value


def _verify_snapshot(current):
    root = Path(current["root"]).resolve()
    if {p.name for p in root.iterdir()} != {r["document_id"] for r in current["documents"]}:
        raise ValueError("XIPL 지식 판의 문서 목록이 변경되었습니다.")
    for record in current["documents"]:
        path = root / record["document_id"]
        if path.is_symlink() or not path.resolve().is_relative_to(root) or _digest(path.read_bytes()) != record["sha256"]:
            raise ValueError("XIPL 지식 판의 원문이 변경되었습니다. 서버 수집 상태를 확인하세요.")

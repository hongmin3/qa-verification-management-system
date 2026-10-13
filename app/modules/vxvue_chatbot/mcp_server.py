"""Restricted stdio MCP server using only stdlib transport and the local Skill corpus."""
import argparse
import base64
import json
import os
from pathlib import Path
import sys
from urllib.parse import quote
import xml.etree.ElementTree as ET

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from app.modules.vxvue_chatbot.skill.scripts.corpus import Corpus
from app.modules.vxvue_chatbot.skill.scripts import knowledge as generator
from app.core.security_filter import mask_payload


def text_content(value):
    masked, _ = mask_payload(value)
    return [{"type": "text", "text": json.dumps(masked, ensure_ascii=False, separators=(",", ":"))}]


def compact_response(value, inventory=False):
    """Remove transport-only repetitions; never shorten evidence or QA metadata."""
    if isinstance(value, list):
        return [compact_response(v, inventory) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "file_path":
            continue
        if key == "scope" and isinstance(item, dict):
            result[key] = {k: compact_response(v, inventory) for k, v in item.items()
                           if k not in ("root", "index_path", "checked_utc") and (inventory or k != "documents")}
        else:
            result[key] = compact_response(item, inventory)
    return result


class KnowledgeTools:
    def __init__(self, snapshot, attachments, trace_path, job_id=""):
        self.snapshot = snapshot
        self.corpus = Corpus(snapshot["root"], snapshot["index"])
        self.attachments = {a["id"]: a for a in attachments}
        self.trace_path = Path(trace_path)
        self.job_id = job_id

    def _decorate(self, value):
        if isinstance(value, list):
            return [self._decorate(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {k: self._decorate(v) for k, v in value.items()}
        if "file" in result and any(k in result for k in ("sheet", "cell", "row")):
            record = next((d for d in self.snapshot["documents"] if Path(result["file"]).name == d["document_id"]), None)
            if record:
                result.update(document_id=record["document_id"], document_name=record["name"], sha256=record["sha256"],
                              location={k: result[k] for k in ("sheet", "cell", "row") if k in result}, text="Generator 원본 위치")
        if "document_id" in result and ("evidence" in result or "text" in result):
            doc = result["document_id"]
            result["reference_url"] = f"/vxvue-chatbot/jobs/{self.job_id}/source?document_id={quote(doc, safe='')}"
            if self.job_id:
                citation = {k: result[k] for k in ("document_id", "document_name", "chunk_id", "location", "sha256", "reference_url") if k in result}
                with self.trace_path.open("a", encoding="utf-8") as log:
                    log.write(json.dumps(citation, ensure_ascii=False) + "\n")
        return result

    def _source(self, document_id):
        root = Path(self.snapshot["root"]).resolve()
        path = (root / document_id).resolve()
        if not path.is_relative_to(root) or document_id not in {d["document_id"] for d in self.snapshot["documents"]}:
            raise ValueError("문서 목록 안의 ID만 조회할 수 있습니다.")
        return path

    def _image(self, path, page=1):
        import fitz
        with fitz.open(path) as doc:
            if not 1 <= page <= len(doc):
                raise ValueError("페이지 범위를 확인해주세요.")
            target = doc[page - 1]
            scale = min(2.0, 1800 / max(target.rect.width, target.rect.height))
            pixmap = target.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return [{"type": "text", "text": f"원본 페이지 {page}. 표·이미지·취소선과 적용 조건을 직접 확인하세요."},
                    {"type": "image", "mimeType": "image/png", "data": base64.b64encode(pixmap.tobytes("png")).decode()}]

    def read_attachment(self, attachment_id, offset=0, limit=10, page=None):
        if attachment_id not in self.attachments:
            raise ValueError("이 대화의 첨부 ID만 사용할 수 있습니다.")
        a = self.attachments[attachment_id]
        path = Path(a["path"])
        suffix = path.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg", ".webp") or page is not None:
            return self._image(path, int(page or 1))
        if suffix in (".xml", ".log"):
            if not isinstance(offset, int) or offset < 0:
                raise ValueError("offset은 0 이상입니다.")
            text = path.read_text(encoding="utf-8-sig")
            return text_content({"name": a["name"], "text": text[offset:offset + 30000], "truncated": offset + 30000 < len(text), "next_offset": offset + 30000, "untrusted_attachment": True})
        chunks, warnings = __import__("app.modules.vxvue_chatbot.skill.scripts.corpus", fromlist=["parse"]).parse(path)
        if not isinstance(offset, int) or offset < 0 or not 1 <= limit <= 30:
            raise ValueError("offset과 limit 범위를 확인해주세요.")
        return text_content({"name": a["name"], "results": chunks[offset:offset + limit], "warnings": warnings, "truncated": offset + limit < len(chunks), "next_offset": offset + limit, "untrusted_attachment": True})

    def call(self, name, arguments):
        arguments = dict(arguments)
        if name in ("search_documents", "find_testcases", "trace_requirements", "compare_packages"):
            arguments.setdefault("limit", 6)
        elif name == "get_document":
            arguments.setdefault("limit", 4)
        if name == "batch_lookup":
            return self.batch_lookup(arguments.get("requests"))
        if name == "get_status":
            status = self.corpus.get_status()
            status.pop("latest_rules", None)  # full latest rules already in system prompt
            return compact_response({**status, "snapshot_id": self.snapshot["id"], "source_failures": self.snapshot.get("failures", [])}, inventory=True)
        if name == "read_attachment":
            return self.read_attachment(**arguments)
        if name == "get_document_page":
            document_id = arguments["document_id"]
            path = self._source(document_id)
            contents = self._image(path, int(arguments.get("page", 1)))
            reference = {"document_id": document_id, "document_name": document_id, "location": {"page": int(arguments.get("page", 1))}, "text": "원본 페이지"}
            contents.insert(0, text_content(self._decorate(reference))[0])
            return contents
        if name == "get_generator_evidence":
            value = generator.bundle(Path(self.snapshot["root"]), arguments.get("model", "Ecoray"), arguments.get("query", ""), arguments.get("limit", 20))
            value.pop("inventory", None)  # authoritative listing is available through get_status
            value.pop("generated_utc", None)
            return compact_response(self._decorate(value))
        allowed = {"search_documents", "get_document", "find_testcases", "compare_packages", "trace_requirements"}
        if name not in allowed:
            raise ValueError("허용되지 않은 도구입니다.")
        return compact_response(self._decorate(getattr(self.corpus, name)(**arguments)))

    def batch_lookup(self, requests):
        allowed = {"search_documents", "get_document", "find_testcases", "compare_packages", "trace_requirements", "get_generator_evidence", "read_attachment"}
        if not isinstance(requests, list) or not 1 <= len(requests) <= 10:
            raise ValueError("묶음 조회는 1~10개 요청입니다.")
        if any(not isinstance(r, dict) or r.get("tool") not in allowed or not isinstance(r.get("arguments", {}), dict) for r in requests):
            raise ValueError("묶음 조회에는 허용된 읽기 도구만 사용할 수 있습니다.")
        results, scope = [], None
        for request in requests:
            try:
                value = self.call(request["tool"], request.get("arguments", {}))
                if isinstance(value, list):
                    if any(block.get("type") != "text" for block in value):
                        raise ValueError("이미지 조회는 개별 read_attachment 도구로 요청해주세요.")
                    value = json.loads(value[0]["text"])
                if isinstance(value, dict) and "scope" in value:
                    if scope is None:
                        scope = value["scope"]
                    if value["scope"] == scope:
                        value.pop("scope")
                results.append({"tool": request["tool"], "value": value, "is_error": False})
            except Exception as exc:
                results.append({"tool": request["tool"], "is_error": True, "error": type(exc).__name__, "evidence_status": "incomplete"})
        return {"results": results, "scope": scope}


def tools():
    string = {"type": "string"}
    integer = {"type": "integer", "minimum": 0}
    definitions = [
        ("batch_lookup", "독립적인 검색·문서·텍스트 첨부 조회 1~10개를 한 번에 처리. 이미지 page는 개별 호출. 각 결과 오류를 확인한다.", {"requests": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"type": "object", "properties": {"tool": {"type": "string", "enum": ["search_documents", "get_document", "find_testcases", "compare_packages", "trace_requirements", "get_generator_evidence", "read_attachment"]}, "arguments": {"type": "object"}}, "required": ["tool", "arguments"], "additionalProperties": False}}}, ["requests"]),
        ("get_status", "현재 지식 목록·읽기 오류·지식 판 상태", {}, []),
        ("search_documents", "VXvue 사양·매뉴얼·TC를 통합 검색. 검색되지 않은 자료는 미확인이다.", {"query": string, "limit": integer, "document_kind": string, "model": string, "package": string}, ["query"]),
        ("get_document", "문서 원문 페이지·시트·셀 조회. truncated이면 next_offset으로 이어 읽기.", {"document_id": string, "section": string, "offset": integer, "limit": integer}, ["document_id"]),
        ("get_document_page", "PDF 또는 이미지 원본 페이지를 이미지로 확인", {"document_id": string, "page": {"type": "integer", "minimum": 1}}, ["document_id"]),
        ("find_testcases", "기존 TC 근거와 원문 위치. 과거 결과는 현재 지원 근거가 아니다.", {"query": string, "model": string, "limit": integer}, ["query"]),
        ("compare_packages", "패키지별 사양 근거 비교", {"query": string, "packages": {"type": "array", "items": string}, "limit": integer}, ["query", "packages"]),
        ("trace_requirements", "사양과 TC 연결 후보 조회. ID 연결만으로 coverage를 확정하지 않음.", {"query": string, "limit": integer}, ["query"]),
        ("get_generator_evidence", "Generator 모델별 사양·TC 셀의 역할을 보존해 조회", {"query": string, "model": string, "limit": integer}, []),
        ("read_attachment", "현재 대화의 첨부 원문·표·이미지를 읽는다. PDF 이미지는 page를 지정.", {"attachment_id": string, "offset": integer, "limit": integer, "page": {"type": "integer", "minimum": 1}}, ["attachment_id"]),
    ]
    return [{"name": name, "description": description, "inputSchema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}, "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}} for name, description, properties, required in definitions]


def serve(session, source=sys.stdin, target=sys.stdout):
    for line in source:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if "id" not in request:
            continue
        method = request.get("method")
        if method == "initialize":
            result = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}}, "serverInfo": {"name": "vxvue_knowledge", "version": "0.1.0"}}
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tools()}
        elif method == "tools/call":
            try:
                value = session.call(request["params"]["name"], request["params"].get("arguments", {}))
                result = {"content": value if isinstance(value, list) else text_content(value), "isError": False}
            except Exception as exc:
                result = {"content": text_content({"error": type(exc).__name__, "detail": str(exc)[:400], "evidence_status": "incomplete"}), "isError": True}
        else:
            target.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}) + "\n")
            target.flush()
            continue
        target.write(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}, ensure_ascii=False) + "\n")
        target.flush()


if __name__ == "__main__":
    os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    parser = argparse.ArgumentParser()
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--trace", required=True, type=Path)
    args = parser.parse_args()
    context = json.loads(args.context.read_text(encoding="utf-8"))
    serve(KnowledgeTools(context["snapshot"], context["attachments"], args.trace, context["job_id"]))

"""Anonymous browser-owned chat APIs and UI (no account/login requirement)."""
import asyncio
import hashlib
import json
import secrets
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from app.core.config import get_settings
from app.core.product_knowledge import load_manifest
from app.core.product_config import load_product_config
from app.core.uploads import save_upload
from app.core.usage import daily_token_status
from app.core.security_filter import mask_text
from . import service
from .knowledge import skill_status

def no_store(response: Response):
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(dependencies=[Depends(no_store)])
templates = Jinja2Templates(directory=[Path(__file__).parent / "templates", Path(__file__).parents[2] / "web" / "templates"])
COOKIE = "vxvue_chat_owner"
EXTENSIONS = {".pdf", ".docx", ".xlsx", ".md", ".txt", ".csv", ".tsv", ".json", ".xml", ".log", ".png", ".jpg", ".jpeg", ".webp"}


def get_storage():
    return service.get_storage()


def file_limit():
    return int(get_settings().get("vxvue_chatbot.max_file_bytes", 50 * 1024 * 1024))


def files_root():
    root = get_settings().root / "data" / "vxvue_chatbot" / "attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def owner(request):
    token = request.cookies.get(COOKIE, "")
    if len(token) < 32 or len(token) > 128:
        raise HTTPException(403, "채팅 화면을 먼저 열어주세요.")
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "다른 사이트에서 요청할 수 없습니다.")
        if request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "다른 사이트에서 요청할 수 없습니다.")
    return hashlib.sha256(token.encode()).hexdigest()


def public_job(job):
    knowledge = json.loads(job.get("knowledge_json") or "{}")
    metadata = knowledge.get("skill_metadata") or {}
    return {k: job[k] for k in ("id", "conversation_id", "assistant_message_id", "status", "stage", "error", "created_at", "updated_at")} | {"knowledge": {"id": knowledge.get("id"), "failures": knowledge.get("failures", []), "documents": len(knowledge.get("documents", [])), "skill_version": metadata.get("version"), "skill_code_review_needed": knowledge.get("skill_code_review_needed", False)}}


def public_chat(chat):
    value = {k: v for k, v in chat.items() if k != "owner"}
    value["jobs"] = [public_job(j) for j in value["jobs"]]
    value["messages"] = [{**m, "attachments": [{k: a[k] for k in ("id", "name", "bytes")} for a in m["attachments"]]} for m in value["messages"]]
    return value


def require_job(request, job_id):
    job = get_storage().vxchat_job(owner(request), job_id)
    if job is None:
        raise HTTPException(404, "작업을 찾을 수 없습니다.")
    return job


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    response = templates.TemplateResponse(request, "chat.html", {"max_file_bytes": file_limit(), "message_char_limit": int(get_settings().get("vxvue_chatbot.message_char_limit", 30000))})
    token = request.cookies.get(COOKIE, "")
    if not 32 <= len(token) <= 128:
        response.set_cookie(COOKIE, secrets.token_urlsafe(48), httponly=True, samesite="strict", secure=request.url.scheme == "https", max_age=365 * 86400, path="/vxvue-chatbot")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    return templates.TemplateResponse(request, "guide.html", {})


@router.get("/conversations")
def conversations(request: Request):
    return get_storage().vxchat_list(owner(request))


@router.post("/conversations")
def create(request: Request):
    value = get_storage().vxchat_create(owner(request))
    return {"id": value["id"], "title": value["title"]}


@router.get("/conversations/{conversation_id}")
def conversation(request: Request, conversation_id: str):
    chat = get_storage().vxchat_get(owner(request), conversation_id)
    if chat is None:
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    return public_chat(chat)


class Rename(BaseModel):
    title: str = Field(min_length=1, max_length=100)


@router.patch("/conversations/{conversation_id}")
def rename(request: Request, conversation_id: str, body: Rename):
    if not get_storage().vxchat_rename(owner(request), conversation_id, body.title.strip()):
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    return {"ok": True}


@router.delete("/conversations/{conversation_id}")
def delete(request: Request, conversation_id: str):
    try:
        deleted = get_storage().vxchat_delete(owner(request), conversation_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if deleted is None:
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    root = files_root().resolve()
    for stored_path in deleted["paths"]:
        path = Path(stored_path).resolve()
        if path.is_relative_to(root):
            path.unlink(missing_ok=True)
    return {"ok": True}


@router.post("/conversations/{conversation_id}/messages", status_code=202)
async def submit(request: Request, conversation_id: str):
    user = owner(request)
    store = get_storage()
    if store.vxchat_get(user, conversation_id) is None:
        raise HTTPException(404, "대화를 찾을 수 없습니다.")
    if daily_token_status(store)["exceeded"]:
        raise HTTPException(429, "오늘 AI 사용량 한도에 도달했습니다.")
    total_limit = int(get_settings().get("vxvue_chatbot.max_request_bytes", 200 * 1024 * 1024))
    size = 0
    original_receive = request._receive
    async def limited_receive():
        nonlocal size
        message = await original_receive()
        size += len(message.get("body", b""))
        if size > total_limit:
            raise HTTPException(413, "첨부 전체 크기가 요청 상한을 넘었습니다.")
        return message
    request._receive = limited_receive
    saved = []
    try:
        async with request.form(max_files=10, max_fields=10, max_part_size=1024 * 1024) as form:
            files = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
            text = str(form.get("text", "")).strip()
            if len(files) > 10:
                raise HTTPException(400, "첨부파일은 최대 10개입니다.")
            if not text and not files:
                raise HTTPException(400, "질문 또는 첨부파일을 입력해주세요.")
            if len(text) > int(get_settings().get("vxvue_chatbot.message_char_limit", 30000)):
                raise HTTPException(400, "질문이 너무 깁니다. 내용을 나눠서 보내주세요.")
            for upload in files:
                if (upload.size or 0) > file_limit():
                    raise HTTPException(413, "첨부파일의 크기 상한을 넘었습니다.")
                path = save_upload(upload, files_root(), EXTENSIONS)
                saved.append({"id": uuid.uuid4().hex, "name": Path(upload.filename.replace("\\", "/")).name[:240], "path": str(path), "bytes": path.stat().st_size})
                if path.stat().st_size > file_limit():
                    raise HTTPException(413, "첨부파일의 크기 상한을 넘었습니다.")
            job = store.vxchat_submit(user, conversation_id, text or "첨부자료를 확인해주세요.", saved,
                                     max_pending=int(get_settings().get("vxvue_chatbot.max_pending_jobs", 20)))
    except Exception as exc:
        for a in saved:
            Path(a["path"]).unlink(missing_ok=True)
        if isinstance(exc, HTTPException):
            raise
        if isinstance(exc, (LookupError, ValueError)):
            raise HTTPException(409 if isinstance(exc, ValueError) else 404, str(exc)) from exc
        raise
    service.wake()
    return public_job(job)


@router.get("/jobs/{job_id}")
def job(request: Request, job_id: str):
    return public_job(require_job(request, job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel(request: Request, job_id: str):
    require_job(request, job_id)
    get_storage().vxchat_cancel(owner(request), job_id)
    service.cancel(job_id)
    return {"ok": True}


@router.get("/jobs/{job_id}/events")
async def events(request: Request, job_id: str):
    require_job(request, job_id)
    async def stream():
        previous = None
        while not await request.is_disconnected():
            stored = await asyncio.to_thread(get_storage().vxchat_job, owner(request), job_id)
            if stored is None:
                return
            chat = await asyncio.to_thread(get_storage().vxchat_get, owner(request), stored["conversation_id"])
            message = next((m for m in chat["messages"] if m["id"] == stored["assistant_message_id"]), None)
            value = {"job": public_job(stored), "message": {"content": message["content"], "citations": message["citations"]}}
            data = json.dumps(value, ensure_ascii=False)
            if data != previous:
                yield "data: " + data + "\n\n"
                previous = data
            else:
                yield ": heartbeat\n\n"
            if stored["status"] not in ("QUEUED", "RUNNING"):
                return
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/attachments/{attachment_id}")
def attachment(request: Request, attachment_id: str):
    a = get_storage().vxchat_attachment(owner(request), attachment_id)
    if a is None:
        raise HTTPException(404, "첨부파일을 찾을 수 없습니다.")
    return FileResponse(a["path"], filename=a["name"], media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/source")
def source(request: Request, job_id: str, document_id: str):
    stored = require_job(request, job_id)
    knowledge = json.loads(stored["knowledge_json"] or "{}")
    document = next((d for d in knowledge.get("documents", []) if d["document_id"] == document_id), None)
    if not document:
        raise HTTPException(404, "조회한 지식 판에 문서가 없습니다.")
    root = Path(knowledge["root"]).resolve()
    path = (root / document_id).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "원본 자료를 찾을 수 없습니다.")
    return FileResponse(path, filename=document["name"], headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})


@router.get("/knowledge-status")
def knowledge_status():
    store = get_storage()
    latest = store.latest_sync("VXvue", "product_knowledge")
    successful = store.vxchat_last_complete_sync()
    retry = None
    if latest and latest["status"] in ("FAILED", "PARTIAL", "NEEDS_CONFIG"):
        match = re.search(r"다음 재시도 ([0-9T:+.\-Z]+)", latest["detail"])
        retry = match[1] if match else (datetime.fromisoformat(latest["synced_at"]) + timedelta(minutes=15)).isoformat()
    stale = not successful or datetime.now(timezone.utc) - datetime.fromisoformat(successful["synced_at"]) > timedelta(hours=26)
    manifest = load_manifest("VXvue", get_settings().root)
    excluded = [{"name": a.get("file_name", ""), "reason": mask_text(a.get("exclude_reason", "") or "")[0], "kind": a.get("kind", "")} for a in manifest.get("excluded", [])]
    failed = [{"name": a.get("file_name", ""), "reason": mask_text(a["error"])[0], "kind": a.get("kind", "")} for a in manifest.get("assets", []) if a.get("error")]
    manuals = []
    for entry, status in [(a, "failed" if a.get("error") else "ready") for a in manifest.get("assets", []) if a.get("kind") == "manual"] + [(a, "excluded") for a in manifest.get("excluded", []) if a.get("kind") == "manual"]:
        reason = entry.get("error") if status == "failed" else entry.get("exclude_reason") if status == "excluded" else ""
        manuals.append({"name": entry.get("file_name", ""), "status": status, "modified_at": entry.get("modified"), "reason": mask_text(reason or "")[0]})
    product = load_product_config("vxvue", get_settings().root)
    sync = product.sync if product else None
    days = {"mon-fri": "평일", "*": "매일"}.get(sync.day_of_week, sync.day_of_week) if sync else ""
    spec_schedule = f"{days} {sync.schedule_time}" if sync else "예약 정보 없음"
    spec_attempt = store.latest_sync("VXvue", "specification")
    spec_success = store.vxchat_last_success_sync("specification")
    skill = skill_status(get_settings().root, get_settings().get("vxvue_chatbot.reviewed_skill_code_hash", ""))
    skill_asset = next((a for a in manifest.get("assets", []) if a.get("kind") == "skill_instruction"), None)
    skill["synced_at"] = (skill_asset.get("collected_at") or skill_asset.get("modified")) if skill_asset else None
    return {"last_attempt": {"synced_at": latest["synced_at"], "status": latest["status"], "detail": mask_text(latest["detail"])[0]} if latest else None,
            "last_success_at": successful["synced_at"] if successful else None,
            "next_retry_at": retry, "stale": stale,
            "excluded": excluded, "failed_documents": failed,
            "skill": skill, "manuals": manuals,
            "specification_sync": {"last_success_at": spec_success["synced_at"] if spec_success else None,
                                   "last_attempt_at": spec_attempt["synced_at"] if spec_attempt else None,
                                   "status": spec_attempt["status"] if spec_attempt else None, "schedule": spec_schedule},
            "schedule": "매일 08:00 KST · 실패 시 15분마다 재시도",
            "documents": sum(len(store.active_documents(kind, "VXvue")) for kind in ("specification", "manual", "testcase")) + len([a for a in manifest.get("assets", []) if a.get("kind") in ("qa_rules", "instruction_prompt", "skill_instruction")])}

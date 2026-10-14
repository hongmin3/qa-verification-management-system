"""Bounded persistent queue with one background worker, independent of HTTP connections."""
from pathlib import Path
import logging
import threading
import json
from functools import lru_cache

from app.core.config import get_settings
from app.core.storage import Storage
from app.core.usage import daily_token_status
from . import agent
from .knowledge import prepare_knowledge

_wake = threading.Event()
_shutdown = threading.Event()
_lock = threading.Lock()
_thread = None
_cancel_events = {}


@lru_cache(maxsize=4)
def _storage(path):
    return Storage(path)


def get_storage():
    return _storage(get_settings().root / "data" / "app.db")


def wake():
    _wake.set()
    start()


def cancel(job_id):
    with _lock:
        event = _cancel_events.get(job_id)
        if event:
            event.set()


def run_job(store, settings, job, cancel_event):
    if not store.vxchat_start(job["id"]):
        return
    try:
        chat = store.vxchat_get(job["owner"], job["conversation_id"])
        if not chat or cancel_event.is_set():
            raise InterruptedError()
        if daily_token_status(store)["exceeded"]:
            raise ValueError("오늘 AI 사용량 한도에 도달했습니다. 한도가 풀린 뒤 다시 요청해주세요.")
        snapshot = prepare_knowledge(store, settings.root / "data" / "vxvue_chatbot" / "snapshots", project_root=settings.root)
        # Private location is only persisted; the router returns the ID and status, not paths.
        knowledge = {k: v for k, v in snapshot.items() if k not in ("rules", "skill_text")}
        if snapshot.get("skill_metadata"):
            knowledge["skill_code_review_needed"] = snapshot["skill_metadata"]["code_hash"] != settings.get("vxvue_chatbot.reviewed_skill_code_hash", "")
        store.vxchat_progress(job["id"], "자료 검색", knowledge=knowledge)
        attachments = [a for message in chat["messages"] for a in message["attachments"]]
        if cancel_event.is_set():
            raise InterruptedError()
        snapshot = {**snapshot, "conversation_meta": {"jobs": chat["jobs"], "title_source": chat.get("title_source", "auto")}}
        result = agent.run(chat["messages"], snapshot, attachments, job["id"], settings, cancel_event,
                           lambda stage, content: store.vxchat_progress(job["id"], stage, content))
        store.vxchat_finish(job["id"], "DONE", result["text"], citations=result["citations"], usage=result["usage"], model=result["model"], masking=result.get("masking"), metrics=result.get("metrics"))
        if result.get("title"):
            try:
                store.vxchat_set_ai_title(job["conversation_id"], result["title"])
            except Exception:
                # Best effort: the answer is already saved, so a title failure must not fail the job.
                logging.getLogger(__name__).warning("AI 대화 제목 저장 실패", exc_info=True)
    except InterruptedError:
        store.vxchat_finish(job["id"], "CANCELLED", "답변 생성을 중지했습니다.")
    except Exception as exc:
        # Only messages defined by this feature are public. Arbitrary IO/CLI data is not.
        detail = str(exc) if isinstance(exc, (ValueError, TimeoutError)) or (type(exc) is RuntimeError) else f"처리 중 {type(exc).__name__} 오류가 발생했습니다. 관리자에게 서버 상태 확인을 요청해주세요."
        store.vxchat_finish(job["id"], "FAILED", detail, error=detail, metrics=getattr(exc, "metrics", None), usage=getattr(exc, "usage", None), model=getattr(exc, "model", ""))


def _worker():
    while not _shutdown.is_set():
        _wake.wait(2)
        _wake.clear()
        try:
            store, settings = get_storage(), get_settings()
            if store.vxchat_global_busy(int(settings.get("analysis.max_concurrent_jobs", 2))):
                continue
            pending = store.vxchat_pending()
            if not pending:
                continue
            job = pending[0]
            event = threading.Event()
            with _lock:
                _cancel_events[job["id"]] = event
            try:
                run_job(store, settings, job, event)
            finally:
                with _lock:
                    _cancel_events.pop(job["id"], None)
            if store.vxchat_pending():
                _wake.set()
        except Exception:
            # An unavailable DB must not kill the worker permanently.
            continue


def start():
    global _thread
    with _lock:
        if _thread is None or not _thread.is_alive():
            _shutdown.clear()
            _thread = threading.Thread(target=_worker, name="vxvue-chatbot", daemon=True)
            _thread.start()


def resume():
    get_storage().vxchat_recover()
    wake()


def stop():
    _shutdown.set()
    _wake.set()
    with _lock:
        for event in _cancel_events.values():
            event.set()
    if _thread:
        _thread.join(timeout=10)

"""Validates: REQ-SYNC-002. Native trigger jitter must not skip a 15-minute retry."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import importlib
import sys


def test_early_native_trigger_waits_until_retry_then_reports_real_result(tmp_path, monkeypatch):
    script = importlib.import_module("scripts.retry_product_knowledge")
    engine = importlib.import_module("app.core.knowledge_retry")
    reporter = importlib.import_module("app.modules.knowledge.vxvue_spec_sync")
    due = None
    calls = []
    def run(*args, **kwargs):
        nonlocal due
        calls.append(datetime.now(timezone.utc))
        if len(calls) == 1:
            due = (calls[0] + timedelta(seconds=.5)).isoformat()
        return {"status": "NOT_DUE", "detail": "retry wait", "next_retry_at": due} if len(calls) == 1 else {"status": "SUCCESS", "detail": "completed"}
    monkeypatch.setattr(engine, "run_due", run)
    monkeypatch.setattr(script, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    reports = []
    monkeypatch.setattr(reporter, "report_sync_log", lambda *args: reports.append(args))
    monkeypatch.setattr(sys, "argv", ["retry_product_knowledge.py", "--upload-to", "http://test.invalid", "--wait-retry"])
    assert script.main() == 0
    assert len(calls) == 2 and calls[1] >= datetime.fromisoformat(due)
    assert len(reports) == 1 and reports[0][4] == "SUCCESS"


def test_idle_minute_run_leaves_no_http_request_line_in_log(tmp_path, monkeypatch, caplog):
    """Validates: REQ-SYNC-002. 1분마다 도는 할 일 없는 실행은 httpx 요청 로그도 남기지 않는다."""
    import logging
    script = importlib.import_module("scripts.retry_product_knowledge")
    engine = importlib.import_module("app.core.knowledge_retry")
    def run(*args, **kwargs):
        # 실제 실행에서는 수집 요청 조회(fetch_collect_request)가 httpx 로 INFO 한 줄을 남긴다.
        logging.getLogger("httpx").info('HTTP Request: GET http://test.invalid/knowledge/product-knowledge/request "HTTP/1.1 200 OK"')
        return {"status": "NOT_DUE", "detail": "none"}
    monkeypatch.setattr(engine, "run_due", run)
    monkeypatch.setattr(script, "get_settings", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(sys, "argv", ["retry_product_knowledge.py", "--upload-to", "http://test.invalid"])
    httpx_logger = logging.getLogger("httpx")
    monkeypatch.setattr(httpx_logger, "level", httpx_logger.level)
    caplog.set_level(logging.INFO)
    assert script.main() == 0
    assert not [r for r in caplog.records if "HTTP Request" in r.getMessage()]

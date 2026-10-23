"""Validates: REQ-SYNC-002. KST schedule, persistent catch-up, target isolation."""
from datetime import datetime, timedelta, timezone
import importlib


def retry_module():
    return importlib.import_module("app.core.knowledge_retry")


def test_eight_am_kst_failure_retries_at_fifteen_minutes_and_stops_on_success(tmp_path):
    retry = retry_module()
    kst = timezone(timedelta(hours=9))
    before = datetime(2026, 10, 13, 7, 59, tzinfo=kst)
    now = before + timedelta(minutes=1)
    calls = []
    def push(*args, **kwargs):
        calls.append(args)
        return {"status": "FAILED" if len(calls) == 1 else "SUCCESS", "detail": "test"}
    run = lambda moment: retry.run_due("VXvue", "http://server-a", state_dir=tmp_path, now=moment, push=push)
    assert run(before)["status"] == "NOT_DUE"
    assert run(now)["status"] == "FAILED"
    assert run(now + timedelta(minutes=14))["status"] == "NOT_DUE"
    assert run(now + timedelta(minutes=15))["status"] == "SUCCESS"
    assert run(now + timedelta(hours=8))["status"] == "NOT_DUE"
    assert len(calls) == 2


def test_pc_off_for_days_catches_up_once_and_targets_do_not_share_success(tmp_path):
    retry = retry_module()
    now = datetime(2026, 10, 18, 4, tzinfo=timezone.utc)
    calls = []
    def push(*args, **kwargs):
        calls.append(args)
        return {"status": "SUCCESS", "detail": "test"}
    for target in ("http://a", "http://a", "http://b"):
        retry.run_due("VXvue", target, state_dir=tmp_path, now=now, push=push)
    assert len(calls) == 2


def test_partial_is_not_success_and_exception_is_recorded(tmp_path):
    retry = retry_module()
    now = datetime(2026, 10, 13, 2, tzinfo=timezone.utc)
    first = retry.run_due("VXvue", "http://a", state_dir=tmp_path, now=now,
                          push=lambda *a, **k: {"status": "PARTIAL", "detail": "unreadable"})
    assert first["status"] == "PARTIAL"
    def broken(*args, **kwargs):
        raise ConnectionError("secret-like-data-must-not-leak")
    second = retry.run_due("VXvue", "http://a", state_dir=tmp_path, now=now + timedelta(minutes=15), push=broken)
    assert second["status"] == "FAILED"
    assert "secret-like" not in second["detail"]
    assert second["next_retry_at"]


def test_request_is_kept_after_a_failed_upload_and_retried_after_fifteen_minutes(tmp_path):
    """Validates: REQ-KNOW-021. 서버에 닿지 못한 실패 뒤에도 같은 화면 요청을 다시 처리한다."""
    retry = retry_module()
    now = datetime(2026, 10, 23, 1, 16, tzinfo=timezone.utc)   # 10:16 KST, 그날 정기 업로드는 이미 끝남
    request = lambda: "2026-10-23T01:15:30+00:00"
    calls = []
    results = iter(["SUCCESS", "FAILED", "SUCCESS"])
    def push(*args, **kwargs):
        calls.append(args)
        return {"status": next(results), "detail": "test"}
    run = lambda moment, check=request: retry.run_due("VXvue", "http://a", state_dir=tmp_path, now=moment, push=push, check_request=check)

    assert run(now - timedelta(hours=2), check=lambda: "")["status"] == "SUCCESS"      # 08:00 정기 업로드
    assert run(now)["status"] == "FAILED"                                               # 요청 처리 실패
    assert run(now + timedelta(minutes=1))["status"] == "NOT_DUE"                       # 15분 재시도 대기
    assert run(now + timedelta(minutes=15))["status"] == "SUCCESS"                      # 같은 요청을 다시 처리
    assert run(now + timedelta(minutes=16))["status"] == "NOT_DUE"                      # 끝난 요청은 다시 하지 않음
    assert len(calls) == 3

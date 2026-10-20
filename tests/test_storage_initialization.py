"""Validates: REQ-STORE-002. 초기화 실패 복구와 기존 기록 보존."""
import ctypes
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.core.storage as module
from app.core.storage import Storage


def cantopen():
    error = sqlite3.OperationalError("unable to open database file")
    error.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
    return error


@pytest.mark.parametrize("phase", ["initialize", "_initialize_daily_qa", "_initialize_qa_execution", "_initialize_vxchat", "_initialize_vxdesk"])
def test_windows_initialization_recovers_without_losing_existing_records(tmp_path, monkeypatch, phase):
    path = tmp_path / "test.db"
    previous = Storage(path)
    document_id = previous.add_document("specification", "VXvue", "1", "", "keep.md", tmp_path / "keep.md")
    original = getattr(Storage, phase)
    attempts, waits = [], []

    def interrupted(self):
        original(self)
        attempts.append(phase)
        if len(attempts) == 1:
            raise cantopen()

    monkeypatch.setattr(Storage, phase, interrupted)
    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"), raising=False)
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=waits.append), raising=False)
    restored = Storage(path)
    assert restored.get_document(document_id)["name"] == "keep.md"
    assert len(restored.list_documents("specification")) == 1
    assert len(attempts) == 2 and waits == [0.05]
    assert restored.vxchat_create("owner")["title"] == "새 대화"


@pytest.mark.parametrize("platform,code,expected", [("nt", sqlite3.SQLITE_CANTOPEN, 5), ("posix", sqlite3.SQLITE_CANTOPEN, 1), ("nt", sqlite3.SQLITE_CORRUPT, 1)])
def test_initialization_failure_is_bounded_and_other_errors_propagate(tmp_path, monkeypatch, platform, code, expected):
    attempts, waits = [], []
    error = sqlite3.OperationalError("controlled initialization failure")
    error.sqlite_errorcode = code

    def unavailable(self):
        attempts.append(1)
        raise error

    monkeypatch.setattr(Storage, "initialize", unavailable)
    monkeypatch.setattr(module, "os", SimpleNamespace(name=platform), raising=False)
    monkeypatch.setattr(module, "time", SimpleNamespace(sleep=waits.append), raising=False)
    with pytest.raises(sqlite3.OperationalError) as caught:
        Storage(tmp_path / "test.db")
    assert caught.value is error
    assert len(attempts) == expected
    assert waits == pytest.approx([0.05, 0.10, 0.15, 0.20] if expected == 5 else [])


@pytest.mark.skipif(os.name != "nt", reason="실제 Windows 파일 접근 제한")
def test_real_windows_journal_access_denial_recovers_on_same_database(tmp_path, monkeypatch):
    from ctypes import wintypes

    path = tmp_path / "test.db"
    journal = Path(str(path) + "-journal")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    denied = []

    class Connection(sqlite3.Connection):
        def executescript(self, sql):
            if "CREATE TABLE IF NOT EXISTS documents" not in sql or denied:
                return super().executescript(sql)
            handle = kernel.CreateFileW(str(journal), 0xC0000000, 0, None, 1, 0x80, None)
            assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
            try:
                return super().executescript(sql)
            except sqlite3.OperationalError as exc:
                denied.append(exc.sqlite_errorcode)
                raise
            finally:
                kernel.CloseHandle(handle)
                assert journal.stat().st_size == 0
                journal.unlink()

    def connect(self):
        db = sqlite3.connect(self.db_path, factory=Connection)
        db.row_factory = sqlite3.Row
        return db

    monkeypatch.setattr(Storage, "connect", connect)
    restored = Storage(path)
    assert denied == [sqlite3.SQLITE_CANTOPEN]
    assert restored.list_products() == ["Bellalun Viewer", "VXvue"]
    with closing(restored.connect()) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

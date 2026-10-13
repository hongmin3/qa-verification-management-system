# Validates: REQ-SYNC-003
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.knowledge.srs_crawler.snapshot_store import find_previous_snapshot_date, load_snapshot, save_snapshot


def test_save_and_load_roundtrip(tmp_path):
    records = [
        {"uid": "VXvue/VP-1", "id": "VP-1", "title": "A"},
        {"uid": "VXvue/VP-2", "id": "VP-2", "title": "B"},
    ]
    save_snapshot(tmp_path, "2026-08-24", "VXvue", records)
    loaded = load_snapshot(tmp_path, "2026-08-24", "VXvue")
    assert set(loaded.keys()) == {"VXvue/VP-1", "VXvue/VP-2"}
    assert loaded["VXvue/VP-1"]["title"] == "A"


def test_find_previous_snapshot_date_picks_latest_before_current(tmp_path):
    for d in ["2026-08-03", "2026-08-10", "2026-08-17"]:
        save_snapshot(tmp_path, d, "VXvue", [{"uid": "VXvue/VP-1", "id": "VP-1"}])
    assert find_previous_snapshot_date(tmp_path, "2026-08-24") == "2026-08-17"
    assert find_previous_snapshot_date(tmp_path, "2026-08-10") == "2026-08-03"


def test_find_previous_snapshot_date_none_on_first_run(tmp_path):
    assert find_previous_snapshot_date(tmp_path, "2026-08-24") is None

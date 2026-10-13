"""Validates: REQ-QAAGENT-016. 배포 후 폐기 파일만 지우고 다른 자료는 보존한다."""
from scripts.remove_retired_qa_agent import cleanup


def test_dry_run_and_apply_preserve_other_files(tmp_path):
    retired = tmp_path / "app/modules/qa_agent/analyzer.py"
    retained = tmp_path / "app/modules/qa_agent/schemas.py"
    data = tmp_path / "data/app.db"
    for path in (retired, retained, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep original", encoding="utf-8")
    dry = cleanup(tmp_path, apply=False)
    assert retired.exists() and "app/modules/qa_agent/analyzer.py" in dry["would_remove"]
    applied = cleanup(tmp_path, apply=True)
    assert not retired.exists() and "app/modules/qa_agent/analyzer.py" in applied["removed"]
    assert retained.read_text() == data.read_text() == "keep original"
    assert cleanup(tmp_path, apply=True)["removed"] == []


def test_directory_at_retired_file_path_is_not_recursively_removed(tmp_path):
    path = tmp_path / "app/modules/qa_agent/analyzer.py"
    path.mkdir(parents=True)
    (path / "user-file").write_text("preserve", encoding="utf-8")
    cleanup(tmp_path, apply=True)
    assert (path / "user-file").read_text() == "preserve"

"""Published snapshot directories tolerate only bounded Windows sharing delays."""
from pathlib import Path
import pytest


def test_publish_retries_windows_access_denied_without_overwriting(tmp_path, monkeypatch):
    from app.core import snapshot_files
    source, final = tmp_path / 'building-one', tmp_path / 'snapshot-one'
    source.mkdir(); (source / 'source.txt').write_text('PRESERVE')
    rename = Path.rename
    attempts = []
    def temporary(self, target):
        attempts.append(1)
        if len(attempts) < 3:
            error = PermissionError('temporary sharing conflict'); error.winerror = 5
            raise error
        return rename(self, target)
    monkeypatch.setattr(Path, 'rename', temporary)
    monkeypatch.setattr(snapshot_files.time, 'sleep', lambda seconds: None)
    snapshot_files.publish_directory(source, final)
    assert len(attempts) == 3 and not source.exists()
    assert (final / 'source.txt').read_text() == 'PRESERVE'


def test_publish_does_not_retry_other_permission_errors(tmp_path, monkeypatch):
    from app.core import snapshot_files
    source, final = tmp_path / 'building-one', tmp_path / 'snapshot-one'; source.mkdir()
    attempts = []
    def denied(self, target):
        attempts.append(1); raise PermissionError('permanent')
    monkeypatch.setattr(Path, 'rename', denied)
    with pytest.raises(PermissionError): snapshot_files.publish_directory(source, final)
    assert len(attempts) == 1 and source.exists() and not final.exists()


def test_publish_never_replaces_existing_snapshot(tmp_path):
    from app.core.snapshot_files import publish_directory
    source, final = tmp_path / 'building-one', tmp_path / 'snapshot-one'
    source.mkdir(); final.mkdir(); (final / 'source.txt').write_text('ORIGINAL')
    with pytest.raises(FileExistsError): publish_directory(source, final)
    assert (final / 'source.txt').read_text() == 'ORIGINAL' and source.exists()

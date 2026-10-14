"""Validates: REQ-QAINTEL-046. Original issue author, not assignee or commenter."""
from tests.qa_intel_harness import Harness
from tests.daily_qa_fixtures import issue_item
from app.modules.daily_qa.product_adapter import normalize_issue
from app.modules.daily_qa.intelligence import _without_authors


def test_reporter_is_original_author_and_excluded_from_ai(tmp_path):
    h = Harness(tmp_path)
    raw = issue_item('VP-1', '2026-10-01', [])
    raw['relationships']['author'] = {'data': {'id': 'creator'}}
    raw['relationships']['assignee'] = {'data': [{'id': 'other'}]}
    raw['_reporter_name'] = '작성자 이름'
    value = normalize_issue(raw, h.cfg.product_profile)
    assert (value['reporter_id'], value['reporter_name']) == ('creator', '작성자 이름')
    assert 'reporter_id' not in _without_authors({'events': [value]})['events'][0]


def test_filters_precede_pagination_and_processing_preserves_result(tmp_path):
    h = Harness(tmp_path); db = h.store.storage
    h.store.create_run('one', False, h.cfg.slug)
    ids = [h.store.add_finding('one', 'qa-new-issue-analysis', str(i),
        {'subject': f'VP-{i}', 'verdict': 'SUPPORTED_BY_SPEC', 'summary': '원래 판정', 'analysis_type': 'NEW_ISSUE'}, h.cfg.slug)
        for i in range(61)]
    db.qa_save_reporters(h.cfg.slug, [{'id': 'VP-0', 'reporter_id': 'a', 'reporter_name': '같은 이름'},
                                    {'id': 'VP-60', 'reporter_id': 'b', 'reporter_name': '같은 이름'}])
    found = db.qa_search_findings(h.cfg.slug, reporter='a', subject='VP-0')
    assert found['total'] == 1 and found['items'][0]['id'] == ids[0]
    db.qa_set_processing(ids[0], h.cfg.slug, 'done', 'QA 담당자', '조치할 내용 없음')
    db.qa_set_processing(ids[0], h.cfg.slug, 'open', 'QA 담당자', '추가 확인')
    assert len(db.qa_processing_history(ids[0])) == 2
    assert h.store.get_finding(ids[0])['summary'] == '원래 판정'
    assert db.qa_search_findings('other')['total'] == 0


def test_updated_order_and_author_order_are_applied_before_paging(tmp_path):
    h = Harness(tmp_path); db = h.store.storage
    h.store.create_run('one', False, h.cfg.slug)
    for n in range(60):
        h.store.add_finding('one', 'qa-new-issue-analysis', str(n),
            {'subject': f'VP-{n}', 'verdict': 'SPEC_VIOLATION', 'summary': '분석', 'analysis_type': 'NEW_ISSUE'}, h.cfg.slug)
    db.qa_save_reporters(h.cfg.slug, [
        {'id': 'VP-0', 'reporter_id': 'b', 'reporter_name': '나', 'updated': '2026-10-14T10:00:00+09:00'},
        {'id': 'VP-1', 'reporter_id': 'a', 'reporter_name': '가', 'updated': '2026-10-14T00:30:00Z'}])
    first = db.qa_search_findings(h.cfg.slug, sort='updated')['items'][0]
    assert first['subject'] == 'VP-0' and first['issue_updated_at'] == '2026-10-14T10:00:00+09:00'
    assert db.qa_search_findings(h.cfg.slug, sort='author')['items'][0]['subject'] == 'VP-1'
    db.qa_save_reporters(h.cfg.slug, [{'id': 'VP-0', 'reporter_id': 'different', 'updated': '2026-10-15'}])
    row = db.qa_search_findings(h.cfg.slug, subject='VP-0')['items'][0]
    assert row['reporter_name'] == ''


def test_historical_collection_does_not_move_update_time_back(tmp_path):
    h = Harness(tmp_path); db = h.store.storage
    db.qa_save_reporters(h.cfg.slug, [{'id': 'VP-1', 'updated': '2026-10-14T10:00:00Z'}])
    db.qa_save_reporters(h.cfg.slug, [{'id': 'VP-1', 'updated': '2026-09-14T10:00:00Z'}])
    with db.connect() as conn:
        assert conn.execute('SELECT issue_updated_at FROM qa_issue_reporters').fetchone()[0] == '2026-10-14T10:00:00Z'


def test_existing_reporter_table_migrates_without_losing_names(tmp_path):
    h = Harness(tmp_path); db = h.store.storage
    with db.connect() as conn:
        conn.execute('DROP TABLE qa_issue_reporters')
        conn.execute('CREATE TABLE qa_issue_reporters(product TEXT,subject TEXT,reporter_id TEXT,reporter_name TEXT,PRIMARY KEY(product,subject))')
        conn.execute("INSERT INTO qa_issue_reporters VALUES('vxvue','VP-1','creator','작성자')")
    db._initialize_qa_execution()
    with db.connect() as conn:
        row = conn.execute('SELECT reporter_name, issue_updated_at FROM qa_issue_reporters').fetchone()
        assert tuple(row) == ('작성자', '')

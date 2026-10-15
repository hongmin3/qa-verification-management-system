from dataclasses import replace
from tests.qa_intel_harness import Harness, DAY1, DAY2
from tests.daily_qa_fixtures import srs_item, issue_item

def test_collect_only_blocks_ai_and_preserves_events(tmp_path):
    h=Harness(tmp_path)
    h.cfg=replace(h.cfg,savings_enabled=True)
    h.srs=[srs_item('VP-10','01','원문')]
    h.issues=[issue_item('VP-100','2026-09-28', ['VP-10'])]
    h.run(DAY1, trigger='prepare')
    h.issues.append(issue_item('VP-200','2026-09-30',['VP-10']))
    out=h.run(DAY2, trigger='prepare')
    assert h.calls == []
    assert out['summary']['collection_only'] is True
    assert any(e['analysis_status']=='pending' for e in h.events())

def test_savings_catchup_is_allowed_after_limit(tmp_path):
    """한도가 풀린 뒤 대기분을 이어 하는 것은 절약 모드에서도 켜져 있다 (REQ-QAINTEL-049)."""
    from app.modules.daily_qa.automation_policy import catchup_allowed
    h=Harness(tmp_path);h.cfg=replace(h.cfg,savings_enabled=True)
    assert catchup_allowed(h.cfg,h.store,{'enabled':True}) is True
    assert catchup_allowed(h.cfg,h.store,{'enabled':False}) is False

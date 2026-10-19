"""Validates REQ-DAILY-010 and REQ-VXVUECHATBOT-005/006: shared source, fail closed."""
import hashlib
import json
from pathlib import Path
import pytest
from app.core.product_knowledge import product_dir
from app.core.qa_rules import load_rule_set
from app.core.storage import Storage
from app.modules.daily_qa.rules import check
from app.modules.vxvue_chatbot.knowledge import prepare_knowledge

UNIFIED = '''<!-- qa-rules-format: unified-v1 -->
# QA 통합 가이드
<!-- qa-rules:operations -->
# 운영 지침
요청 범위와 확인 근거를 구분한다.
<!-- qa-rules:details -->
# 32. Regression 영향 범위
연결된 상태와 저장 결과를 확인한다.
'''

def install(root, files):
    base = product_dir('VXvue', root)
    assets = []
    for kind, name, text in files:
        path = base / 'original' / kind / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        assets.append(dict(kind=kind, file_name=name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (base / 'manifest.json').write_text(json.dumps({'assets':assets, 'synced_at':'fixed'}), encoding='utf-8')
    return base

def test_unified_single_file_is_shared_by_daily_loader_and_chat(tmp_path):
    install(tmp_path, [('qa_rules','[QA 작성 규칙] current.md',UNIFIED)])
    state=check('VXvue',tmp_path)
    assert state.ok, state.reason
    assert state.prompt_path is None
    assert len(load_rule_set('VXvue',tmp_path).documents)==1
    snapshot=prepare_knowledge(Storage(tmp_path/'data/app.db'),tmp_path/'snapshots',tmp_path)
    assert snapshot['rules'].count('요청 범위와 확인 근거')==1

def test_unified_wins_over_old_and_higher_revision_and_excludes_them_from_corpus(tmp_path):
    install(tmp_path,[('qa_rules','[QA 작성 규칙] current.md',UNIFIED),('qa_rules','old_Rev99.0.md','OLD GUIDE'),('instruction_prompt','old_Rev99.0.txt','OLD PROMPT')])
    state=check('VXvue',tmp_path)
    assert state.guide_path.name=='[QA 작성 규칙] current.md'
    assert state.prompt_path is None
    assert len(load_rule_set('VXvue',tmp_path).documents)==1
    snapshot=prepare_knowledge(Storage(tmp_path/'data/app.db'),tmp_path/'snapshots',tmp_path)
    assert 'OLD' not in snapshot['rules']
    assert not any('old_' in d['name'] for d in snapshot['documents'])

@pytest.mark.parametrize('broken',[UNIFIED.replace('<!-- qa-rules:details -->',''),UNIFIED.replace('요청 범위와 확인 근거를 구분한다.','')])
def test_incomplete_unified_blocks_without_legacy_fallback(tmp_path,broken):
    install(tmp_path,[('qa_rules','current.md',broken),('qa_rules','old_Rev1.18.md','# old\nOLD'),('instruction_prompt','old.txt','OLD')])
    assert not check('VXvue',tmp_path).ok
    assert not load_rule_set('VXvue',tmp_path).available
    with pytest.raises(ValueError):
        prepare_knowledge(Storage(tmp_path/'data/app.db'),tmp_path/'snapshots',tmp_path)

def test_hash_mismatch_blocks_all_readers(tmp_path):
    base=install(tmp_path,[('qa_rules','current.md',UNIFIED)])
    (base/'original/qa_rules/current.md').write_text(UNIFIED+'changed',encoding='utf-8')
    assert not check('VXvue',tmp_path).ok
    assert not load_rule_set('VXvue',tmp_path).available
    with pytest.raises(ValueError):
        prepare_knowledge(Storage(tmp_path/'data/app.db'),tmp_path/'snapshots',tmp_path)

def test_conflicting_unified_copies_block(tmp_path):
    install(tmp_path,[('qa_rules','a.md',UNIFIED),('qa_rules','b.md',UNIFIED+'different')])
    assert not check('VXvue',tmp_path).ok

def test_same_filename_edit_creates_new_immutable_snapshot(tmp_path):
    files=[('qa_rules','current.md',UNIFIED)]
    install(tmp_path,files)
    store=Storage(tmp_path/'data/app.db')
    old=prepare_knowledge(store,tmp_path/'snapshots',tmp_path)
    install(tmp_path,[('qa_rules','current.md',UNIFIED+'\nNEW POLICY')])
    new=prepare_knowledge(store,tmp_path/'snapshots',tmp_path)
    assert old['id']!=new['id']
    assert 'NEW POLICY' in new['rules']
    assert 'NEW POLICY' not in (Path(old['root'])/'current.md').read_text(encoding='utf-8')


def test_unified_autoqa_includes_operating_rules_after_heading_renumbering(tmp_path):
    from app.modules.daily_qa.rule_context import guide_context
    from app.modules.daily_qa.workspace import RunWorkspace
    from types import SimpleNamespace
    guide=tmp_path/'rules/qa-guide.md'
    guide.parent.mkdir()
    guide.write_text(UNIFIED.replace('32.', '132.'),encoding='utf-8')
    mapping=tmp_path/'.claude/skills/vxvue-qa-rules/guide-sections.json'
    mapping.parent.mkdir(parents=True)
    mapping.write_text('{"qa-fixed-issue-analysis":["32"]}',encoding='utf-8')
    run=RunWorkspace(tmp_path,'run','vxvue-qa-rules')
    task=SimpleNamespace(skill='qa-fixed-issue-analysis',payload={})
    text=''.join(body for _,body in guide_context(task,run,strict=True))
    assert text.count('요청 범위와 확인 근거')==1
    assert '연결된 상태와 저장 결과' in text

def test_unified_verification_mode_keeps_tc_rules_and_duplicate_child_titles(tmp_path):
    from app.modules.daily_qa.rule_context import guide_context
    from app.modules.daily_qa.workspace import RunWorkspace
    from types import SimpleNamespace
    guide=tmp_path/'rules/qa-guide.md'
    guide.parent.mkdir()
    guide.write_text(UNIFIED+'''\n# 117. TC Coverage
TC RULE REQUIRED
# 133. Regression
## Examples
REGRESSION CHILD
# 139. Generator
## Examples
GENERATOR CHILD
''',encoding='utf-8')
    run=RunWorkspace(tmp_path,'run','vxvue-qa-rules')
    task=SimpleNamespace(skill='qa-fixed-issue-analysis',payload={'items':[{'verification_mode':True}]})
    text=''.join(body for _,body in guide_context(task,run,strict=True))
    assert 'TC RULE REQUIRED' in text
    assert 'REGRESSION CHILD' in text
    assert 'GENERATOR CHILD' in text

def test_unversioned_unified_rules_are_shown_as_collected(tmp_path):
    from app.core.knowledge_status import product_health
    from app.core.product_config import ProductConfig
    install(tmp_path,[('qa_rules','current.md',UNIFIED)])
    health=product_health(ProductConfig(product='VXvue'),Storage(tmp_path/'data/app.db'),root=tmp_path)
    assert health.advanced['rule_revision']

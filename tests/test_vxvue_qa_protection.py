"""Validates: REQ-VXVUECHATBOT-017. Synthetic approved templates only."""
import hashlib
import json
from types import SimpleNamespace
import pytest
from app.modules.vxvue_chatbot import qa_protection

TEMPLATE = r'\\10.9.8.7\qa\logs\#<issue>'
RULES = '<!-- qa-rules-format: unified-v1 -->\n## 29.8 Log\n`'+TEMPLATE+'`\n## 30 Other\n'

@pytest.fixture
def approved(monkeypatch):
    monkeypatch.setattr(qa_protection,'APPROVED_TEMPLATE_SHA256',hashlib.sha256(TEMPLATE.encode()).hexdigest())

def test_exact_template_scoped_to_approved_qa_section(approved):
    value,report=qa_protection.mask_qa_text(RULES+'other '+r'\\10.1.2.3\customer\data', RULES)
    assert TEMPLATE in value and '[NETWORK_PATH]' in value
    assert report.chars_before==len(RULES+'other '+r'\\10.1.2.3\customer\data') and report.chars_after==len(value)
    assert report.counts['unc_path']==1
    assert TEMPLATE not in qa_protection.mask_qa_text(TEMPLATE, '')[0]
    assert TEMPLATE not in qa_protection.mask_qa_text(TEMPLATE, RULES.replace('29.8','29.9'))[0]
    assert TEMPLATE not in qa_protection.mask_qa_text(TEMPLATE, RULES.replace('unified-v1','other'))[0]

def test_near_match_remains_protected(approved):
    for candidate in (TEMPLATE+'suffix', TEMPLATE.replace('10.9.8.7','10.9.8.8'),TEMPLATE.replace('#<issue>','#12345')):
        assert candidate not in qa_protection.mask_qa_text(candidate,RULES)[0]

def test_workflow_protects_before_slicing(approved,monkeypatch):
    from app.modules.vxvue_chatbot import desktop
    snapshot={'id':'s','skill_text':'skill','rules':RULES+'private '+r'\\10.1.2.3\customer\data','skill_metadata':{}}
    monkeypatch.setattr(desktop,'snapshot',lambda sid:snapshot)
    chunks=[];offset=0
    while True:
        reply=desktop.dispatch({'method':'tools/call','params':{'name':'get_workflow','arguments':{'snapshot_id':'s','offset':offset,'limit':11}}})
        assert not reply['isError']
        value=json.loads(reply['content'][0]['text']);chunks.append(value['text'])
        if not value['truncated']:break
        offset=value['next_offset']
    text=''.join(chunks)
    assert TEMPLATE in text and '10.1.2.3' not in text and len(text)==value['total_chars']

def test_qa_document_only_is_preserved(approved,tmp_path):
    from app.modules.vxvue_chatbot.mcp_server import KnowledgeTools,serve
    from app.modules.vxvue_chatbot.skill.scripts.corpus import Corpus
    import io
    source=tmp_path/'original';source.mkdir()
    (source/'qa.md').write_text(RULES,encoding='utf-8');(source/'other.md').write_text(RULES,encoding='utf-8')
    index=tmp_path/'index.sqlite3';Corpus(source,index)
    snapshot={'root':str(source),'index':str(index),'id':'s','documents':[{'document_id':'qa.md','kind':'qa_rules'},{'document_id':'other.md','kind':'specification'}]}
    session=KnowledgeTools(snapshot,[],tmp_path/'trace')
    for doc,preserve in [('qa.md',True),('other.md',False)]:
        request={'id':1,'method':'tools/call','params':{'name':'get_document','arguments':{'document_id':doc,'limit':30}}}
        output=io.StringIO();serve(session,io.StringIO(json.dumps(request)+'\n'),output)
        response=json.loads(output.getvalue());assert not response['result']['isError'],response
        text=json.dumps(json.loads(response['result']['content'][0]['text']),ensure_ascii=False)
        assert (TEMPLATE in json.loads(text)['results'][0]['evidence']) is preserve

def test_batch_preserves_only_qa_document_template(approved,tmp_path):
    from app.modules.vxvue_chatbot.mcp_server import KnowledgeTools,serve
    import io
    source=tmp_path/'original';source.mkdir()
    for doc in ('qa.md','other.md'):(source/doc).write_text(RULES,encoding='utf-8')
    session=KnowledgeTools({'root':str(source),'index':str(tmp_path/'index'),'documents':[{'document_id':'qa.md','kind':'qa_rules'},{'document_id':'other.md','kind':'specification'}]},[],tmp_path/'trace')
    requests=[{'tool':'get_document','arguments':{'document_id':doc}} for doc in ('qa.md','other.md')]
    output=io.StringIO();serve(session,io.StringIO(json.dumps({'id':1,'method':'tools/call','params':{'name':'batch_lookup','arguments':{'requests':requests}}})+'\n'),output)
    data=json.loads(json.loads(output.getvalue())['result']['content'][0]['text'])
    assert TEMPLATE in data['results'][0]['value']['results'][0]['evidence']
    assert TEMPLATE not in data['results'][1]['value']['results'][0]['evidence']

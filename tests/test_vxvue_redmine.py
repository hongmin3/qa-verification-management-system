import json
from types import SimpleNamespace
import httpx
import pytest
from app.modules.vxvue_chatbot import redmine

def cfg(host='https://redmine.example.test', key='test-key'):
    return SimpleNamespace(secrets=SimpleNamespace(redmine_host=host, redmine_api_key=key),get=lambda k,d=None:d)

def test_metadata_paging_and_partial_failure():
    calls=[]
    def transport(request):
        calls.append(request)
        project=int(request.url.params['project_id'])
        if project==29: return httpx.Response(403)
        return httpx.Response(200,json={'issues':[{'id':8,'project':{'id':project,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'ABC','description':'not in list'}], 'total_count':3,'offset':0,'limit':1})
    lookup=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport))
    result=lookup.search('ABC',project_ids=[61,29],page_size=1)
    assert set(result['items'][0])=={'id','project','status','subject'}
    assert result['next_pages']=={'61':2} and result['errors'][0]['project_id']==29
    assert result['complete'] is False
    assert all(r.method=='GET' and r.url.params['status_id']=='*' and r.headers['X-Redmine-API-Key']=='test-key' for r in calls)

@pytest.mark.parametrize('kwargs',[{'issue_id':True},{'issue_id':'https://evil.test/1'},{'issue_id':-1}])
def test_invalid_issue_never_sends(kwargs):
    lookup=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(lambda r: pytest.fail('request sent')))
    with pytest.raises(ValueError):lookup.issue(**kwargs)

def test_host_redirect_and_config_fail_closed():
    with pytest.raises(ValueError):redmine.RedmineLookup(cfg('https://user:secret@evil.test'))
    with pytest.raises(ValueError):redmine.RedmineLookup(cfg(key=''))
    seen=[]
    def transport(r):
        seen.append(r);return httpx.Response(302,headers={'Location':'https://evil.test/'})
    with pytest.raises(ValueError,match='HTTP 302'):
        redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport)).issue(1)
    assert len(seen)==1

def test_detail_keeps_all_sections_and_protects_before_slicing(tmp_path):
    def transport(r):
        assert r.url.path=='/issues/5.json'
        assert r.url.params['include']=='journals,attachments,relations'
        return httpx.Response(200,json={'issue':{'id':5,'project':{'id':61},'description':'IP 10.1.2.3 '+('본문'*10000),'journals':[{'notes':'abc'}],'attachments':[{'id':4}],'relations':[{'issue_to_id':8}]}})
    lookup=redmine.RedmineLookup(cfg(),root=tmp_path,transport=httpx.MockTransport(transport))
    first=lookup.issue(5);chunks=[first['text']];part=first
    while part['truncated']:
        part=lookup.read(first['result_id'],offset=part['next_offset'],limit=101);chunks.append(part['text'])
    text=''.join(chunks);value=json.loads(text)
    assert '10.1.2.3' not in text and '[IP_ADDRESS]' in text
    assert value['issue']['journals'][0]['notes']=='abc' and value['issue']['relations']
    assert len(text)==first['total_chars']

def test_fulltext_uses_project_search_and_verifies_metadata():
    def transport(r):
        if r.url.path=='/projects/107/search.json':return httpx.Response(200,json={'results':[{'id':5,'type':'issue','title':'ignored'}],'total_count':1,'offset':0,'limit':2})
        assert r.url.path=='/issues/5.json'
        return httpx.Response(200,json={'issue':{'id':5,'project':{'id':107,'name':'질의'},'status':{'id':2,'name':'Done'},'subject':'verified'}})
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport)).search('verified',project_ids=[107],full_text=True,page_size=2)
    assert result['items'][0]['subject']=='verified' and result['complete'] is False and result['total_count_by_project']=={'107':None}

def test_malformed_page_is_error_not_empty():
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'issues':[]}))).search('x',project_ids=[61])
    assert not result['complete'] and result['errors']

def test_shared_surface_and_managed_skill_producers():
    from pathlib import Path
    import zipfile,io,base64,re
    from app.modules.vxvue_chatbot import desktop,mcp_server
    from scripts.build_vxvue_desktop import bundle_bytes,skill_bytes,skill_text,installer_text
    required={'get_redmine_issue','search_redmine_issues','read_redmine_result'}
    assert required <= {t['name'] for t in desktop.definitions()}
    assert required <= {t['name'] for t in mcp_server.tools()}
    with zipfile.ZipFile(io.BytesIO(bundle_bytes('https://qa.example.test','t'*43))) as z:
        assert required <= {t['name'] for t in json.loads(z.read('tools.json'))['tools']}
    skill=skill_text().decode()
    assert '총 3회' in skill and '1초' in skill and '사용자 승인' in skill and 'rawfile' in skill
    with zipfile.ZipFile(io.BytesIO(skill_bytes())) as z:assert z.read('vxvue-ai/SKILL.md')==skill_text()
    assert base64.b64encode(skill_text()).decode() in installer_text('https://qa.example.test','t'*43)

@pytest.mark.parametrize('issue',[{'id':5,'project':{'id':1}}, {'id':5,'project':{'id':61},'is_private':True}])
def test_shared_admin_key_never_exposes_unapproved_or_private_issue(issue):
    lookup=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'issue':issue})))
    with pytest.raises(ValueError):lookup.issue(5)

def test_unapproved_project_rejected_before_request():
    lookup=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(lambda r:pytest.fail('request sent')))
    with pytest.raises(ValueError):lookup.search('x',project_ids=[1])

def test_duplicate_page_cannot_claim_complete():
    row={'id':8,'project':{'id':61,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'ABC'}
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'issues':[row,row],'total_count':2,'offset':0,'limit':2}))).search('ABC',project_ids=[61],page_size=2)
    assert not result['complete'] and result['errors'] and len(result['items'])==1

def test_private_row_does_not_hide_later_public_match():
    rows=[{'id':1,'project':{'id':61,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'private','is_private':True}, {'id':2,'project':{'id':61,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'public'}]
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'issues':rows,'total_count':2,'offset':0,'limit':2}))).search('x',project_ids=[61],page_size=2)
    assert [i['id'] for i in result['items']]==[2] and not result['complete'] and not result['errors'] and result['total_count_by_project']=={'61':None}

def test_fulltext_closed_issues_are_included_with_all_statuses():
    def transport(r):
        if r.url.path=='/projects/107/search.json':return httpx.Response(200,json={'results':[{'id':5,'type':'issue closed'},{'id':6,'type':'issue'}],'total_count':2,'offset':0,'limit':2})
        issue_id=int(r.url.path.split('/')[-1].split('.')[0])
        return httpx.Response(200,json={'issue':{'id':issue_id,'project':{'id':107,'name':'QA'},'status':{'id':5,'name':'Closed'},'subject':'verified'}})
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport)).search('verified',project_ids=[107],full_text=True,page_size=2)
    assert not result['complete'] and [i['id'] for i in result['items']]==[5,6] and result['total_count_by_project']=={'107':None}

def test_private_journal_notes_are_not_shared(tmp_path):
    issue={'id':5,'project':{'id':61},'journals':[{'id':1,'notes':'public'},{'id':2,'private_notes':True,'notes':'secret-note'}]}
    lookup=redmine.RedmineLookup(cfg(),root=tmp_path,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'issue':issue})))
    result=lookup.issue(5);value=json.loads(result['text'])
    assert [j['id'] for j in value['issue']['journals']]==[1] and 'excluded_private_journals' not in value and 'secret-note' not in result['text']

@pytest.mark.parametrize('rows,total',[([{'id':1}],0),([{'id':1}],3)])
def test_inconsistent_page_counts_never_advance_over_unread_items(rows,total):
    rows=[{**r,'project':{'id':61,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'public'} for r in rows]
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'issues':rows,'total_count':total,'offset':0,'limit':2}))).search('x',project_ids=[61],page_size=2)
    assert not result['complete'] and result['errors'] and not result['next_pages']

def test_fulltext_private_note_only_match_is_excluded():
    def transport(r):
        if r.url.path=='/projects/61/search.json':return httpx.Response(200,json={'results':[{'id':5,'type':'issue'}],'total_count':1,'offset':0,'limit':1})
        return httpx.Response(200,json={'issue':{'id':5,'project':{'id':61,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'public title','description':'ordinary public text','journals':[{'private_notes':True,'notes':'private-query-sentinel'}]}})
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport)).search('private-query-sentinel',project_ids=[61],full_text=True,page_size=1)
    assert not result['items'] and not result['complete'] and not result['errors'] and result['total_count_by_project']=={'61':None}
    assert 'private-query-sentinel' not in str(result)


def test_shared_subject_search_always_hides_raw_candidate_count():
    row={'id':2,'project':{'id':61,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'public'}
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'issues':[row],'total_count':1,'offset':0,'limit':2}))).search('public',project_ids=[61],page_size=2)
    assert result['items'][0]['id']==2 and result['total_count_by_project']=={'61':None}
    assert result['complete'] is False and result['limitations'] and not result['errors']

def test_web_chat_stdio_executes_same_redmine_guard(tmp_path,monkeypatch):
    import io
    from app.modules.vxvue_chatbot.mcp_server import KnowledgeTools,serve
    issue={'id':5,'project':{'id':61},'description':'public','journals':[{'notes':'private-sentinel','private_notes':True}]}
    lookup=redmine.RedmineLookup(cfg(),root=tmp_path,transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'issue':issue})))
    monkeypatch.setattr(redmine,'lookup',lambda:lookup)
    session=object.__new__(KnowledgeTools)
    request={'id':1,'method':'tools/call','params':{'name':'get_redmine_issue','arguments':{'issue_id':5}}}
    target=io.StringIO();serve(session,io.StringIO(json.dumps(request)+'\n'),target)
    result=json.loads(target.getvalue())['result'];assert not result['isError']
    value=json.loads(json.loads(result['content'][0]['text'])['text'])
    assert value['issue']['id']==5 and value['issue']['journals']==[]
    assert 'private-sentinel' not in target.getvalue()

def test_fulltext_project_url_does_not_override_scope_to_all():
    def transport(request):
        assert request.url.path == '/projects/61/search.json'
        assert 'scope' not in request.url.params
        assert request.url.params['open_issues'] == '0'
        return httpx.Response(200,json={'results':[],'total_count':0,'offset':0,'limit':50})
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport)).search('Focal None',project_ids=[61],full_text=True)
    assert not result['errors']

def test_cross_project_hit_is_filtered_before_deduplication():
    def transport(request):
        if request.url.path.endswith('/search.json'):
            return httpx.Response(200,json={'results':[{'id':5,'type':'issue'}],'total_count':1,'offset':0,'limit':1})
        return httpx.Response(200,json={'issue':{'id':5,'project':{'id':61,'name':'Integration'},'status':{'id':1,'name':'Open'},'subject':'Focal None'}})
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport)).search('Focal None',project_ids=[29,61,107],full_text=True,page_size=1)
    assert [row['id'] for row in result['items']] == [5]
    assert result['items'][0]['project']['id'] == 61 and not result['errors']

def test_legitimate_cross_project_repeat_after_move_is_skipped():
    hydrated=iter((61,29))
    def transport(request):
        if request.url.path.endswith('/search.json'):
            return httpx.Response(200,json={'results':[{'id':5,'type':'issue'}],'total_count':1,'offset':0,'limit':1})
        project=next(hydrated)
        return httpx.Response(200,json={'issue':{'id':5,'project':{'id':project,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'Focal None'}})
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport)).search('Focal None',project_ids=[61,29],full_text=True,page_size=1)
    assert [row['id'] for row in result['items']] == [5] and not result['errors']

def test_duplicate_fulltext_id_in_same_project_page_is_error():
    def transport(request):
        if request.url.path.endswith('/search.json'):
            return httpx.Response(200,json={'results':[{'id':5,'type':'issue'},{'id':5,'type':'issue'}],'total_count':2,'offset':0,'limit':2})
        return httpx.Response(200,json={'issue':{'id':5,'project':{'id':61,'name':'QA'},'status':{'id':1,'name':'Open'},'subject':'Focal None'}})
    result=redmine.RedmineLookup(cfg(),transport=httpx.MockTransport(transport)).search('Focal None',project_ids=[61],full_text=True,page_size=2)
    assert [row['id'] for row in result['items']] == [5] and result['errors'] and not result['complete']

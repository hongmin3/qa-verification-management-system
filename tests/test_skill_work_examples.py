"""Validates: REQ-ALMSKILL-005, REQ-PLMSKILL-004. Generated docs, no external mutation."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]


def renderer():
    spec = importlib.util.spec_from_file_location('render_skill_examples', ROOT/'scripts/render_skill_examples.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_skill_and_web_examples_match_sources():
    module = renderer()
    assert module.render(ROOT, check=True) == []
    data = json.loads((ROOT/'deploy/skill-examples.json').read_text(encoding='utf-8'))
    assert set(data) == {'alm','plm','vxvue','bellalun'}
    for key, group in data.items():
        assert len(group['cases']) >= 6, key
        assert len({case['id'] for case in group['cases']}) == len(group['cases'])
        for case in group['cases']:
            for field in ['title','basis','input','prompt','steps','result','limits']:
                assert case[field], (key,case['id'],field)


def test_app_serves_detailed_examples_on_each_actual_guide():
    from fastapi.testclient import TestClient
    from bs4 import BeautifulSoup
    from app.main import app
    client=TestClient(app)
    paths=['/static/alm-ai/index.html','/static/plm-ai/index.html',
           '/vxvue-desktop','/static/skills/bellalun/index.html']
    for path in paths:
        response=client.get(path)
        assert response.status_code==200,path
        soup=BeautifulSoup(response.text,'html.parser')
        section=soup.select_one('#work-examples')
        assert section is not None,path
        assert len(section.select('details.work-case'))>=6
        for case in section.select('details.work-case'):
            assert len(case.select('pre'))==2
            assert case.select_one('ol li') is not None
        assert soup.select_one('link[href="/static/skills/examples.css"]')
    assert client.get('/static/skills/examples.css').status_code==200


def test_claude_guide_distinguishes_local_skills_upload_and_control_connection():
    from fastapi.testclient import TestClient
    from bs4 import BeautifulSoup
    from app.main import app
    client=TestClient(app)
    for name in ['alm','plm']:
        soup=BeautifulSoup(client.get('/static/'+name+'-ai/index.html').text,'html.parser')
        section=soup.select_one('#claude')
        assert section is not None
        text=section.get_text()
        assert '~/.claude/skills/'+name in text and '/'+name in text
        assert 'Customize' in text and 'Cowork' in text
        assert 'Computer Use' in text and '사내망' in text


def test_renderer_detects_stale_output_and_escapes_html(tmp_path):
    module = renderer()
    source = json.loads((ROOT/'deploy/skill-examples.json').read_text(encoding='utf-8'))
    source['alm']['cases'][0]['prompt'] = '<img src=x onerror=alert(1)>'
    (tmp_path/'deploy').mkdir()
    (tmp_path/'deploy/skill-examples.json').write_text(json.dumps(source),encoding='utf-8')
    for key, path in module.WEB_PATHS.items():
        target=tmp_path/path;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text('<html><body><!-- work-examples:insert --></body></html>',encoding='utf-8')
    assert module.render(tmp_path,check=True)
    assert module.render(tmp_path,check=False) == []
    assert module.render(tmp_path,check=True) == []
    target=tmp_path/module.WEB_PATHS['alm']
    content=target.read_text(encoding='utf-8')
    assert '<img src=x' not in content and '&lt;img' in content
    target.write_text(content.replace('onerror','changed'),encoding='utf-8')
    assert str(module.WEB_PATHS['alm']) in module.render(tmp_path,check=True)


@pytest.mark.parametrize('name',['alm','plm'])
def test_bundles_include_execution_examples_and_explicit_routing(tmp_path,name):
    spec=importlib.util.spec_from_file_location('build_'+name,ROOT/('scripts/build_'+name+'_skill.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    out=tmp_path/(name+'.zip');module.build_skill('https://qa.example.test',out)
    import zipfile
    with zipfile.ZipFile(out) as z:
        text=z.read(name+'/SKILL.md').decode()
        assert 'references/work-examples.md' in text
        assert 'Computer Use' in text
        examples=z.read(name+'/references/work-examples.md').decode()
        assert '결과 보고 형식' in examples and '완료 사실' in examples

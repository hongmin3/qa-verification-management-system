"""Validates: REQ-ALMSKILL-003. No browser or ALM mutation."""
import importlib.util,json,zipfile
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
def builder():
 spec=importlib.util.spec_from_file_location('build_alm_skill',ROOT/'scripts/build_alm_skill.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_bundle_contains_browser_workflow_and_dynamic_server(tmp_path):
 out=tmp_path/'skill.zip';result=builder().build_skill('https://qa.example.test',out)
 with zipfile.ZipFile(out) as z:
  assert set(z.namelist())=={'alm/SKILL.md','alm/agents/openai.yaml','alm/scripts/knowledge.py','alm/references/browser-operations.md','alm/server.json'}
  assert json.loads(z.read('alm/server.json'))['catalog_url']=='https://qa.example.test/static/alm-guide/current.json'
  assert 'references/browser-operations.md' in z.read('alm/SKILL.md').decode()
  assert '전자서명은 사용자가 직접 처리한다' in z.read('alm/references/browser-operations.md').decode()
 assert result['bytes']==out.stat().st_size and len(result['sha256'])==64

@pytest.mark.parametrize('url',['file:///tmp','https://user:secret@qa.example.test','https://qa.example.test/?token=x','https://qa.example.test/path','https://qa.example.test:bad','http://qa.example.test:0','http://qa. example.test'])
def test_bundle_rejects_credentials_and_ambiguous_server_url(tmp_path,url):
 with pytest.raises(ValueError):builder().build_skill(url,tmp_path/'skill.zip')

def test_bundle_does_not_overwrite_existing_file(tmp_path):
 out=tmp_path/'skill.zip';out.write_bytes(b'preserve')
 with pytest.raises(FileExistsError):builder().build_skill('https://qa.example.test',out)
 assert out.read_bytes()==b'preserve'

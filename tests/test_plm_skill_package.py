"""Validates: REQ-PLMSKILL-003. No browser or PLM mutation."""
import importlib.util,json,zipfile
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
def builder():
 spec=importlib.util.spec_from_file_location('build_plm_skill',ROOT/'scripts/build_plm_skill.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_bundle_contains_browser_workflow_and_dynamic_server(tmp_path):
 out=tmp_path/'skill.zip';result=builder().build_skill('https://qa.example.test',out)
 with zipfile.ZipFile(out) as z:
  assert set(z.namelist())=={'plm/SKILL.md','plm/agents/openai.yaml','plm/scripts/knowledge.py','plm/references/browser-operations.md','plm/server.json'}
  assert json.loads(z.read('plm/server.json'))['catalog_url']=='https://qa.example.test/static/plm-guide/current.json'
  assert 'references/browser-operations.md' in z.read('plm/SKILL.md').decode()
  assert '전자서명은 사용자가 직접 처리한다' in z.read('plm/references/browser-operations.md').decode()
 assert result['bytes']==out.stat().st_size and len(result['sha256'])==64

@pytest.mark.parametrize('url',['file:///tmp','https://user:secret@qa.example.test','https://qa.example.test/?token=x','https://qa.example.test/path','https://qa.example.test:bad','http://qa.example.test:0','http://qa. example.test'])
def test_bundle_rejects_credentials_and_ambiguous_server_url(tmp_path,url):
 with pytest.raises(ValueError):builder().build_skill(url,tmp_path/'skill.zip')

def test_bundle_does_not_overwrite_existing_file(tmp_path):
 out=tmp_path/'skill.zip';out.write_bytes(b'preserve')
 with pytest.raises(FileExistsError):builder().build_skill('https://qa.example.test',out)
 assert out.read_bytes()==b'preserve'


@pytest.fixture
def knowledge(tmp_path, monkeypatch):
 import hashlib, io
 spec=importlib.util.spec_from_file_location('plm_knowledge',ROOT/'deploy/plm-skill/plm/scripts/knowledge.py')
 module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
 payloads={
  'chapters.json':json.dumps({'chapters':[{'id':'c9','title':'ALM PDF','text':'DHF '+('x'*350)}]}).encode(),
  'slides.json':json.dumps({'slides':[{'id':'P9','title':'ALM PDF','text':'DHF','image':'slides/P/009.jpg','videos':['V01']}]}).encode(),
  'videos.json':json.dumps([{'id':'V01','file':'V01.mp4','sample_times_seconds':[0,10,267]}]).encode(),
  'slides/P/009.jpg':b'image-test-bytes',
 }
 manifest={'release':'synthetic-001','files':{k:{'sha256':hashlib.sha256(v).hexdigest(),'bytes':len(v)} for k,v in payloads.items()}}
 payloads['manifest.json']=json.dumps(manifest).encode()
 class Response(io.BytesIO):
  status=200
 def fetch(url,**kwargs):
  assert url.startswith('https://qa.example.test/synthetic-001/')
  return Response(payloads[url.removeprefix('https://qa.example.test/synthetic-001/')])
 monkeypatch.setattr(module.urllib.request,'urlopen',fetch)
 return module,payloads,tmp_path


def test_read_commands_pin_release_paginate_and_verify_assets(knowledge):
 module,payloads,tmp=knowledge
 def run(*args):return module.main([*args,'--base-url','https://qa.example.test/synthetic-001'])
 assert run('status')['metadata_verified'] is True
 assert run('search','DHF')['match_count']==2
 first=run('chapter','9','--limit','100')
 assert first['truncated'] and first['next_offset']==100
 assert run('chapter','9','--offset','100','--limit','100')['offset']==100
 assert run('slide','P9')['videos']==['V01']
 assert run('video','V01')['sample_times_seconds']==[0,10,267]
 image=tmp/'slide.jpg'
 run('image','P9','--output',str(image))
 assert image.read_bytes()==payloads['slides/P/009.jpg']
 with pytest.raises(ValueError):run('image','P9','--output',str(image))
 with pytest.raises(ValueError):run('slide','M9')
 payloads['chapters.json']+=b' '
 with pytest.raises(ValueError,match='해시'):run('status')


def test_bad_manifest_digest_and_paths_are_rejected(knowledge):
 module,_,_=knowledge
 with pytest.raises(ValueError,match='해시'):
  module.Client('https://qa.example.test/synthetic-001','0'*64)
 for path in ['../secret','/secret','slides/../secret','https://other.test','a\\b']:
  with pytest.raises(ValueError):module.safe_asset(path)


def test_catalog_resolves_release_and_rejects_manifest_tampering(knowledge,monkeypatch):
 import hashlib,io
 module,payloads,tmp=knowledge
 config=tmp/'server.json'
 config.write_text(json.dumps({'catalog_url':'https://qa.example.test/current.json'}),encoding='utf-8')
 monkeypatch.setattr(module,'__file__',str(tmp/'scripts/knowledge.py'))
 monkeypatch.delenv('PLM_KNOWLEDGE_URL',raising=False)
 asset_fetch=module.urllib.request.urlopen
 catalog={'release_path':'synthetic-001','manifest_sha256':hashlib.sha256(payloads['manifest.json']).hexdigest()}
 def fetch(url,**kwargs):
  if url=='https://qa.example.test/current.json':return io.BytesIO(json.dumps(catalog).encode())
  return asset_fetch(url,**kwargs)
 monkeypatch.setattr(module.urllib.request,'urlopen',fetch)
 assert module.main(['status'])['base_url']=='https://qa.example.test/synthetic-001/'
 catalog['manifest_sha256']='0'*64
 with pytest.raises(ValueError,match='해시'):module.main(['status'])
 catalog['release_path']='../outside'
 with pytest.raises(ValueError,match='release'):module.main(['status'])

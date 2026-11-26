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
  assert set(z.namelist())=={'alm/SKILL.md','alm/agents/openai.yaml','alm/scripts/knowledge.py','alm/references/browser-operations.md','alm/references/work-examples.md','alm/references/checklist-registration.md','alm/references/local-issue-api.md','alm/references/existing-tc-update.md','alm/server.json','alm/scripts/qa_document_guard.py','alm/references/legacy-validation.md'}
  assert json.loads(z.read('alm/server.json'))['catalog_url']=='https://qa.example.test/static/alm-guide/current.json'
  assert 'references/browser-operations.md' in z.read('alm/SKILL.md').decode()
  assert 'references/local-issue-api.md' in z.read('alm/SKILL.md').decode()
  assert b'POLARION_TOKEN' in z.read('alm/references/local-issue-api.md')
  assert 'references/existing-tc-update.md' in z.read('alm/SKILL.md').decode()
  update=z.read('alm/references/existing-tc-update.md').decode()
  assert 'workItemURI' in update and 'full_function' in update and 'Description' in update
  assert 'existing-tc-update.md' in z.read('alm/references/checklist-registration.md').decode()
  assert '전자서명은 사용자가 직접 처리한다' in z.read('alm/references/browser-operations.md').decode()
 assert result['bytes']==out.stat().st_size and len(result['sha256'])==64

@pytest.mark.parametrize('url',['file:///tmp','https://user:secret@qa.example.test','https://qa.example.test/?token=x','https://qa.example.test/path','https://qa.example.test:bad','http://qa.example.test:0','http://qa. example.test'])
def test_bundle_rejects_credentials_and_ambiguous_server_url(tmp_path,url):
 with pytest.raises(ValueError):builder().build_skill(url,tmp_path/'skill.zip')

def test_bundle_does_not_overwrite_existing_file(tmp_path):
 out=tmp_path/'skill.zip';out.write_bytes(b'preserve')
 with pytest.raises(FileExistsError):builder().build_skill('https://qa.example.test',out)
 assert out.read_bytes()==b'preserve'


def test_new_standalone_tc_workflow_requires_id_before_document_move(tmp_path):
    out = tmp_path / "skill.zip"
    builder().build_skill("https://qa.example.test", out)
    with zipfile.ZipFile(out) as archive:
        core = archive.read("alm/SKILL.md").decode("utf-8")
        browser = archive.read("alm/references/browser-operations.md").decode("utf-8").replace("\r\n", "\n")

    assert "신규 TC 등록과 기존 문서 편입" in core
    section = browser.split("## 신규 TC 등록과 기존 문서 편입\n", 1)[1].split("\n## ", 1)[0]
    steps = section.split("\n1. ", 1)[1]
    milestones = ["Work Items", "Software Test Case", "발급 ID", "Actions", "→ Into Document", "같은 ID"]
    assert [steps.index(milestone) for milestone in milestones] == sorted(steps.index(milestone) for milestone in milestones)
    assert "발급 ID를 재조회하기 전에는 LiveDoc 편집을 시작하지 않는다" in section
    assert "문서 추가 필요성만 확인" in section
    assert "브라우저 탭 제어를 우선한다" in browser
    assert "탭 제어가 불가능할 때만" in browser


def test_bundle_documents_safe_resume_for_relationships_and_new_tc(tmp_path):
    out = tmp_path / "skill.zip"
    builder().build_skill("https://qa.example.test", out)
    with zipfile.ZipFile(out) as archive:
        core = archive.read("alm/SKILL.md").decode()
        browser = archive.read("alm/references/browser-operations.md").decode()
        checklist = archive.read("alm/references/checklist-registration.md").decode()
    assert "첫 쓰기 전에" in core and "기존 서비스 API의 지원·권한" in core
    assert "API가 지정됐지만 지원·권한을 확인하지 못하면 그 제한을 보고한다" in checklist
    assert "TC위치" in browser and "일치 항목이 여러 개" in browser
    assert "새 TC를 워크아이템으로 등록하는 요청은" in core
    first_save = browser.index("프로젝트 필수값을 모두 채웠는지 확인하고 `Save`")
    assert "확인한 TC위치도 입력한다" in browser[browser.index("\n1. 신규 한 건은"):first_save]
    assert "이미 한 번 편입되어 있으면 확인에서 끝낸다" in browser
    assert "관계 목록의 전체 페이지를 다시 읽고" in checklist
    assert "이전 저장 요청이 끝났음을 확인한 뒤" in checklist
    assert "승인된 누락분만 다시 보낸다" in checklist

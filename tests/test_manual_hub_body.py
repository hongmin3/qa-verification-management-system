"""Isolated Manual Hub file extraction tests; never imports its PostgreSQL conftest."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import subprocess
import zipfile
import pytest

PATH=Path(__file__).parents[1]/'services/qa-manual-hub/backend/app/body_extract.py'

def extractor():
    spec=importlib.util.spec_from_file_location('hub_body_extract',PATH)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

# Validates: REQ-HUB-008

def test_text_hash_boundaries_and_unsupported(tmp_path):
    module=extractor();p=tmp_path/'manual.md';p.write_text('DICOM 한글 source revision',encoding='utf-8')
    digest=hashlib.sha256(p.read_bytes()).hexdigest();result=module.extract_file(p,'.md',digest)
    assert result['status']=='ready' and '한글' in result['text']
    assert module.extract_file(p,'.md','0'*64)['status']=='hash_mismatch'
    assert module.extract_file(p,'.exe',digest)['status']=='unsupported'
    assert module.extract_file(tmp_path/'missing','.txt',digest)['status']=='missing'
    before=p.read_bytes();module.extract_file(p,'.md',digest);assert p.read_bytes()==before


def test_ooxml_bounded_xml_and_encryption(tmp_path):
    module=extractor();p=tmp_path/'manual.docx'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('word/document.xml','<w:document xmlns:w="urn:test"><w:p><w:t>Detector calibration</w:t></w:p></w:document>')
        z.writestr('word/header1.xml','<w:hdr xmlns:w="urn:test"><w:p><w:t>Revision A</w:t></w:p></w:hdr>')
    r=module.extract_file(p,'.docx');assert r['status']=='ready' and 'Detector' in r['text'] and 'Revision A' in r['text']
    with zipfile.ZipFile(p,'w',compression=zipfile.ZIP_DEFLATED) as z: z.writestr('word/document.xml','x'*(module.MAX_XML_BYTES+1))
    assert module.extract_file(p,'.docx')['status']=='too_large'
    p.write_bytes(bytes.fromhex('d0cf11e0a1b11ae1')+b'encrypted office')
    assert module.extract_file(p,'.docx')['status']=='encrypted'


def test_xlsx_cells_pdf_text_no_text_and_encrypted(tmp_path):
    module=extractor();p=tmp_path/'data.xlsx'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Exposure settings</t></si></sst>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>123</v></c></row></sheetData></worksheet>')
    r=module.extract_file(p,'.xlsx');assert r['status']=='ready' and 'Exposure settings' in r['text'] and '123' in r['text']
    import fitz
    p=tmp_path/'text.pdf';doc=fitz.open();page=doc.new_page();page.insert_text((30,30),'DICOM transfer source');doc.save(p);doc.close()
    r=module.extract_file(p,'.pdf');assert r['status']=='ready' and 'DICOM transfer' in r['text']
    p=tmp_path/'scan.pdf';doc=fitz.open();doc.new_page();doc.save(p);doc.close()
    assert module.extract_file(p,'.pdf')['status']=='no_text'
    p=tmp_path/'encrypted.pdf';doc=fitz.open();doc.new_page();doc.save(p,encryption=fitz.PDF_ENCRYPT_AES_256,owner_pw='owner',user_pw='secret');doc.close()
    assert module.extract_file(p,'.pdf')['status']=='encrypted'


def test_subprocess_output_limit_and_malformed_xml(tmp_path):
    module=extractor();p=tmp_path/'large.txt';p.write_text('x'*(module.MAX_TEXT_CHARS+100),encoding='utf-8')
    r=module.extract_file(p,'.txt');assert r['status']=='truncated' and len(r['text'])<=module.MAX_TEXT_CHARS
    p=tmp_path/'bad.docx'
    with zipfile.ZipFile(p,'w') as z: z.writestr('word/document.xml','<!DOCTYPE a [<!ENTITY x "bad">]><a>&x;</a>')
    assert module.extract_file(p,'.docx')['status']=='failed'
    assert module.snippet('before DICOM after','DICOM')=='before DICOM after'


def test_size_limit_and_parser_timeout_kills_child(tmp_path,monkeypatch):
    module=extractor();p=tmp_path/'sparse.pdf'
    with p.open('wb') as f:f.truncate(module.MAX_FILE_BYTES+1)
    assert module.extract_file(p,'pdf')['status']=='too_large'
    p=tmp_path/'source.txt';p.write_text('original')
    worker=tmp_path/'slow_parser.py';worker.write_text('import time; time.sleep(5)')
    monkeypatch.setattr(module,'__file__',str(worker));monkeypatch.setattr(module,'TIMEOUT_SECONDS',0.2)
    assert module.extract_file(p,'txt')['status']=='timeout'
    assert p.read_text()=='original'


def test_docx_split_runs_preserve_phrase(tmp_path):
    module=extractor();p=tmp_path/'run-split.docx'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t xml:space="preserve">Detector </w:t></w:r><w:r><w:t>calibration</w:t></w:r></w:p></w:body></w:document>')
    r=module.extract_file(p,'docx');assert r['status']=='ready' and 'Detector calibration' in r['text']


def test_xlsx_only_referenced_displayed_values(tmp_path):
    module=extractor()
    p=tmp_path/'shared-values.xlsx'
    with zipfile.ZipFile(p,'w') as z:
        z.writestr('xl/sharedStrings.xml','<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Visible value</t></si><si><t>DELETED_SECRET</t></si></sst>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="inlineStr"><is><t>Inline value</t></is></c><c r="C1"><v>42</v></c></row></sheetData></worksheet>')
    r=module.extract_file(p,'xlsx');assert r['status']=='ready'
    assert 'Visible value' in r['text'] and 'Inline value' in r['text'] and '42' in r['text']
    assert 'DELETED_SECRET' not in r['text'] and '\n0\n' not in '\n'+r['text']+'\n'


@pytest.mark.parametrize('encoding',['utf-8','utf-16','cp949'])
def test_text_encoding_is_not_guessed_as_bomless_utf16(tmp_path,encoding):
    module=extractor();p=tmp_path/'encoding.txt';p.write_bytes('검증'.encode(encoding))
    result=module.extract_file(p,'txt')
    assert result['status']=='ready' and result['text']=='검증'


SERVICE_PROBE = r'''
import os,sys,uuid,hashlib,importlib.util
from pathlib import Path
backend,root=Path(sys.argv[1]),Path(sys.argv[2])
os.environ['DATABASE_URL']='sqlite:///'+str(root/'hub.db')
os.environ['STORAGE_ROOT']=str(root)
sys.path.insert(0,str(backend))
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,text,inspect
from sqlalchemy.orm import Session
from app import db,models
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger
@compiles(JSONB,'sqlite')
def sqlite_json(type_,compiler,**kw):return 'JSON'
@compiles(BigInteger,'sqlite')
def sqlite_integer(type_,compiler,**kw):return 'INTEGER'
from app import deps
# Some deployed Hub installations deliberately lack the newer shared helper.
# The new search API must import and enforce its own narrow maintenance gate.
deps.__dict__.pop('require_admin_password_current',None)
from app.routers import search,documents
from app.deps import get_current_user
tables=[models.User.__table__,models.Product.__table__,models.DocumentCategory.__table__,models.StoredFile.__table__,models.Document.__table__,models.DocumentVersion.__table__,models.AuditLog.__table__]
models.Base.metadata.create_all(db.engine,tables=tables)
with Session(db.engine) as s:
    user=models.User(id=uuid.uuid4(),login_id='tester',display_name='Tester',password_hash='unused',role='admin',is_active=True,must_change_password=False)
    product=models.Product(id=uuid.uuid4(),name='Product')
    category=models.DocumentCategory(id=uuid.uuid4(),name='Manual')
    s.add_all([user,product,category]);s.flush()
    document=models.Document(id=uuid.uuid4(),name='Operation',product_id=product.id,category_id=category.id)
    s.add(document);s.flush()
    document_id=str(document.id)
    for number,content in ((1,'DICOM secret calibration'),(2,'Historical detector legacy')):
        path=root/('file'+str(number)+'.txt');path.write_text(content,encoding='utf-8')
        stored=models.StoredFile(id=uuid.UUID('a0000000-0000-0000-0000-'+f'{number:012d}'),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),byte_size=path.stat().st_size,original_file_name=path.name,file_extension='txt',storage_key=path.name)
        version=models.DocumentVersion(id=uuid.uuid4(),document_id=document.id,revision='Rev'+str(number),uploaded_by_login_id='tester',uploaded_by_display_name='Tester',stored_file_id=stored.id)
        s.add_all([stored,version]);s.flush()
        if number==1: document.current_version_id=version.id
    s.commit();s.refresh(user);s.expunge(user)
app=FastAPI();app.include_router(search.router);app.include_router(documents.router);client=TestClient(app)
assert client.get('/api/search?q=secret').status_code==401
holder={'user':user}
def current():return holder['user']
app.dependency_overrides[get_current_user]=current
assert client.get('/api/search?q=secret').json()==[]
user.role='user';assert client.post('/api/search/reindex',json={}).status_code==403
user.role='admin'
user.must_change_password=True
assert client.post('/api/search/reindex',json={}).status_code==428
user.must_change_password=False
assert client.post('/api/search/reindex',json={},headers={'Origin':'https://evil.example'}).status_code==403
r=client.post('/api/search/reindex',json={'current_only':True,'limit':1});assert r.status_code==200,r.text
assert r.json()['done'] and r.json()['processed']==1 and r.json()['results'][0]['status']=='ready',r.text
# A transient parser/IO error must be retryable by the next explicit batch.
with Session(db.engine) as s:
    stored=s.get(models.StoredFile,uuid.UUID('a0000000-0000-0000-0000-000000000001'))
    stored.body_status='failed';stored.body_text=None;s.commit()
retry=client.post('/api/search/reindex',json={'current_only':True,'limit':1})
assert retry.json()['results'][0]['status']=='ready',retry.text
r=client.get('/api/search?q=secret');assert r.status_code==200,r.text
hits=r.json();assert isinstance(hits,list) and len(hits)==1 and 'secret' in hits[0]['snippet'] and hits[0]['body_status']=='ready'
assert hits[0]['revision']=='Rev1' and hits[0]['original_file_name']=='file1.txt'
assert client.get('/api/search?q=secret&file_name=absent').json()==[]
assert client.get('/api/search?q=Rev2').json()[0]['body_status']=='pending'
r=client.post('/api/search/reindex',json={'current_only':False,'limit':1}).json();assert not r['done'] and r['remaining']==1
r=client.post('/api/search/reindex',json={'current_only':False,'limit':1,'cursor':r['next_cursor']}).json();assert r['done'] and r['results'][0]['status']=='ready'
assert len(client.get('/api/search?q=Historical').json())==1
assert (root/'file1.txt').read_text()=='DICOM secret calibration'
# Exercise the real successful upload route and its post-commit indexing task.
upload=client.post('/api/documents/'+document_id+'/versions',data={'revision':'Rev3'},files={'file':('new.txt',b'Fresh upload searchable source','text/plain')})
assert upload.status_code==201,upload.text
fresh=client.get('/api/search?q=Fresh').json();assert len(fresh)==1 and fresh[0]['body_status']=='ready' and fresh[0]['revision']=='Rev3'
# Stale derived text must neither match nor be presented as a current snippet.
from app.body_extract import INDEX_VERSION
for stale_field in ('version','hash'):
    with Session(db.engine) as s:
        stored=s.get(models.StoredFile,uuid.UUID('a0000000-0000-0000-0000-000000000001'))
        stored.body_index_version=INDEX_VERSION-1 if stale_field=='version' else INDEX_VERSION
        stored.body_sha256=stored.sha256 if stale_field=='version' else '0'*64
        stored.body_text='OBSOLETE_MARKER';stored.body_status='ready';s.commit()
    assert client.get('/api/search?q=OBSOLETE_MARKER').json()==[],stale_field
    metadata=client.get('/api/search?q=Rev1').json()
    assert len(metadata)==1 and metadata[0]['body_status']=='pending' and metadata[0]['snippet'] is None and metadata[0]['body_detail'],metadata
# Run only additive migration0002 against a legacy SQLite row. No production connection.
from alembic.migration import MigrationContext
from alembic.operations import Operations
engine=create_engine('sqlite:///'+str(root/'migration.db'))
with engine.begin() as connection:
    connection.execute(text('CREATE TABLE stored_files(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL)'))
    connection.execute(text("INSERT INTO stored_files VALUES('original','hash')"))
    spec=importlib.util.spec_from_file_location('migration0002',backend/'alembic/versions/0002_file_body_search.py')
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    migration.op=Operations(MigrationContext.configure(connection));migration.upgrade()
    row=connection.execute(text('SELECT id,sha256,body_status,body_index_version FROM stored_files')).one()
    assert tuple(row)==('original','hash','pending',0)
    assert 'ix_stored_files_body_sha256' in {i['name'] for i in inspect(connection).get_indexes('stored_files')}
print('SQLite HTTP search/reindex/auth/filters/snippets/cursor and additive migration passed')
'''


def test_service_sqlite_search_auth_cursor_and_additive_migration(tmp_path):
    backend=PATH.parent.parent
    interpreter=backend/'.venv313/Scripts/python.exe'
    if not interpreter.is_file():
        pytest.skip('isolated Manual Hub dependency environment is not installed')
    process=subprocess.run([str(interpreter),'-c',SERVICE_PROBE,str(backend),str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=90,cwd=tmp_path)
    assert process.returncode==0,process.stdout+'\n'+process.stderr
    assert 'additive migration passed' in process.stdout

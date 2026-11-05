"""Read PLM reference assets; never connects to or mutates Teamcenter."""
import argparse,hashlib,json,os,re,sys,urllib.request
from pathlib import Path,PurePosixPath

def verify(data,expected):
 if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('서버 자료 해시가 일치하지 않습니다. 다시 상태를 확인하세요.')
 return data

def safe_asset(value):
 if not isinstance(value,str) or not value or '\\' in value or ':' in value or value.startswith('/') or any(x in ('.','..','') for x in value.split('/')):
  raise ValueError('허용되지 않은 자료 경로입니다.')
 return value

def page(value,offset=0,limit=10000):
 if offset<0 or offset>len(value) or not 100<=limit<=12000:raise ValueError('offset 또는 limit 범위를 확인하세요.')
 end=min(len(value),offset+limit)
 return {'text':value[offset:end],'offset':offset,'total_chars':len(value),'truncated':end<len(value),'next_offset':end if end<len(value) else None}

def find(rows,query):
 q=query.casefold();return [r for r in rows if q in (r.get('title','')+' '+r.get('text','')+' '+r.get('id','')).casefold()]

class Client:
 def __init__(self,base,expected_manifest=None):
  if not base.startswith(('http://','https://')) or '?' in base or '#' in base:raise ValueError('서버 URL 설정을 확인하세요.')
  self.base=base.rstrip('/')+'/';raw=self.fetch('manifest.json')
  if expected_manifest:verify(raw,expected_manifest)
  self.manifest=json.loads(raw)
  if not isinstance(self.manifest.get('files'),dict) or not self.manifest.get('release'):raise ValueError('서버 manifest 형식이 올바르지 않습니다.')
 def fetch(self,path):
  with urllib.request.urlopen(self.base+safe_asset(path),timeout=25) as r:
   if r.status!=200:raise ValueError('서버 자료 조회에 실패했습니다.')
   data=r.read(16*1024*1024+1)
   if len(data)>16*1024*1024:raise ValueError('자료 크기가 조회 제한을 넘었습니다.')
   return data
 def asset(self,path):
  safe_asset(path)
  if path not in self.manifest['files']:raise ValueError('manifest에 등록되지 않은 자료입니다.')
  return verify(self.fetch(path),self.manifest['files'][path]['sha256'])
 def records(self,path,key):return json.loads(self.asset(path))[key]

def main(argv=None):
 p=argparse.ArgumentParser(description='QA 서버의 PLM 가이드와 교육 원문 읽기')
 p.add_argument('command',choices=['status','search','chapter','slide','video','image']);p.add_argument('value',nargs='?')
 p.add_argument('--base-url');p.add_argument('--offset',type=int,default=0);p.add_argument('--limit',type=int,default=10000);p.add_argument('--output')
 a=p.parse_args(argv);config=Path(__file__).resolve().parents[1]/'server.json'
 base=a.base_url or os.environ.get('PLM_KNOWLEDGE_URL')
 expected=None
 if not base and config.exists():
  settings=json.loads(config.read_text(encoding='utf-8-sig'));base=settings.get('base_url')
  if not base and settings.get('catalog_url'):
   url=settings['catalog_url']
   if not url.startswith(('http://','https://')):raise ValueError('자료 목록 URL을 확인하세요.')
   with urllib.request.urlopen(url,timeout=25) as response:catalog=json.load(response)
   release=catalog.get('release_path','')
   if not re.fullmatch('[a-zA-Z0-9-]+',release):raise ValueError('자료 목록의 release 경로를 확인하세요.')
   base=url.rsplit('/',1)[0]+'/'+release;expected=catalog['manifest_sha256']
 if not base:raise ValueError('server.json 또는 PLM_KNOWLEDGE_URL에 QA 서버 자료 주소를 지정하세요.')
 c=Client(base,expected);result={'release':c.manifest['release'],'base_url':c.base}
 if a.command=='status':
  for f in ['chapters.json','slides.json','videos.json']:c.asset(f)
  result.update({k:v for k,v in c.manifest.items() if k!='files'});result['metadata_hashes']={k:c.manifest['files'][k]['sha256'] for k in ['chapters.json','slides.json','videos.json']};result['metadata_verified']=True
 elif a.command=='search':
  if not a.value:raise ValueError('검색어를 입력하세요.')
  matches=[]
  for path,key,kind in [('chapters.json','chapters','chapter'),('slides.json','slides','slide')]:
   for row in find(c.records(path,key),a.value):
    raw=row['text'];ix=raw.casefold().find(a.value.casefold());start=max(0,ix-80)
    matches.append({'kind':kind,'id':row['id'],'title':row['title'],'excerpt':raw[start:start+260]})
  result['match_count']=len(matches);result.update(page(json.dumps(matches,ensure_ascii=False,indent=2),a.offset,a.limit))
 elif a.command in ('slide','image'):
  if not re.fullmatch('P[1-9][0-9]{0,2}',a.value or ''):raise ValueError('P7 형태의 ID를 입력하세요.')
  rows=c.records('slides.json','slides');row=next((r for r in rows if r['id']==a.value),None)
  if not row:raise ValueError('해당 원본 슬라이드가 없습니다.')
  result.update({k:v for k,v in row.items() if k!='text'});result['image_url']=c.base+row['image'];result.update(page(row['text'],a.offset,a.limit))
  if a.command=='image':
   if not a.output:raise ValueError('이미지 저장 위치를 --output으로 지정하세요.')
   target=Path(a.output).expanduser()
   if target.exists():raise ValueError('기존 파일을 덮어쓰지 않습니다. 새 출력 경로를 지정하세요.')
   target.parent.mkdir(parents=True,exist_ok=True);payload=c.asset(row['image'])
   with target.open('xb') as f:f.write(payload)
   result['saved']=str(target.resolve())
 elif a.command=='chapter':
  if not re.fullmatch('[1-9][0-9]?',a.value or ''):raise ValueError('장 번호를 입력하세요.')
  row=next((r for r in c.records('chapters.json','chapters') if r['id']=='c'+a.value),None)
  if not row:raise ValueError('해당 장이 없습니다.')
  result['title']=row['title'];result.update(page(row['text'],a.offset,a.limit))
 else:
  row=next((r for r in json.loads(c.asset('videos.json')) if r['id']==a.value),None)
  if not row:raise ValueError('등록된 영상 ID를 입력하세요.')
  result.update(row);result['video_url']=c.base+'media/'+row['file'];result['viewer_url']=c.base+'index.html#'+row['id'];result['review_scope']='10초 간격 및 끝부분 화면 표본. 음성 전사와 모든 중간 동작 확인은 미포함.'
 print(json.dumps(result,ensure_ascii=False,indent=2));return result

if __name__=='__main__':
 try:main()
 except Exception as e:print(json.dumps({'error':str(e),'read_complete':False},ensure_ascii=False),file=sys.stderr);raise SystemExit(1)

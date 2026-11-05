"""Render maintained workflow examples into Skill references and existing web guides."""
import argparse
import html
import json
from pathlib import Path
import re

WEB_PATHS = {
    'alm': Path('app/web/static/alm-ai/index.html'),
    'plm': Path('app/web/static/plm-ai/index.html'),
    'vxvue': Path('app/modules/vxvue_chatbot/templates/desktop-guide.html'),
    'bellalun': Path('app/web/static/skills/bellalun/index.html'),
}
START = '<!-- work-examples:start -->'
END = '<!-- work-examples:end -->'


def markdown(group):
    lines = ['# '+group['title'], '', '> 이 문서는 deploy/skill-examples.json에서 생성한다.', '',
             '아래 내용은 요청과 결과 보고 형식 예시다. 현재 작업의 완료 사실이 아니다.', '', group['intro'], '']
    for case in group['cases']:
        lines += ['## '+case['title'], '', '출처 구분: '+case['basis'], '', '### 준비할 입력', '']
        lines += ['- '+value for value in case['input']]
        lines += ['', '### 복사해서 사용할 요청', '', '```text', case['prompt'], '```', '', '### 수행할 범위', '']
        lines += [str(i)+'. '+value for i,value in enumerate(case['steps'],1)]
        lines += ['', '### 결과 보고 형식', '', '```text', case['result'], '```', '', '### 완료 기준과 제한', '']
        lines += ['- '+value for value in case['limits']]
        lines += ['']
    return '\n'.join(lines)+'\n'


def web(group, key):
    esc = html.escape
    parts = [START, '<section id="work-examples" aria-labelledby="work-examples-title">',
             '<h2 id="work-examples-title">'+esc(group['title'])+'</h2>',
             '<p class="note">'+esc(group['intro'])+'</p>',
             '<p>아래 결과는 보고 형식 예시이며 현재 작업의 완료 사실이 아닙니다. 대괄호 안에는 이번 작업에서 확인한 값을 넣습니다. 항목을 펼치면 필요한 입력부터 완료 기준까지 볼 수 있습니다.</p>']
    for index,case in enumerate(group['cases']):
        parts += ['<details class="work-case" id="'+esc(key+'-'+case['id'],quote=True)+'"'+(' open' if index==0 else '')+'>',
                  '<summary>'+esc(case['title'])+'</summary>', '<p class="case-basis">'+esc(case['basis'])+'</p>',
                  '<h4>준비할 입력</h4><ul>']
        parts += ['<li>'+esc(value)+'</li>' for value in case['input']]
        parts += ['</ul><h4>복사해서 사용할 요청</h4><pre>'+esc(case['prompt'])+'</pre>', '<h4>수행할 범위</h4><ol>']
        parts += ['<li>'+esc(value)+'</li>' for value in case['steps']]
        parts += ['</ol><h4>결과 보고 형식</h4><pre class="case-result">'+esc(case['result'])+'</pre>', '<h4>완료 기준과 제한</h4><ul>']
        parts += ['<li>'+esc(value)+'</li>' for value in case['limits']]
        parts += ['</ul></details>']
    return '\n'.join(parts+['</section>',END])


def render(root, check=False):
    root = Path(root)
    data = json.loads((root/'deploy/skill-examples.json').read_text(encoding='utf-8'))
    stale = []
    outputs = {}
    for key,path in WEB_PATHS.items():
        group = data[key]
        content = (root/path).read_text(encoding='utf-8')
        block = web(group,key)
        if START in content:
            if content.count(START)!=1 or content.count(END)!=1:
                raise ValueError('업무 예시 표시 개수 오류: '+str(path))
            value = re.sub(re.escape(START)+r'.*?'+re.escape(END),lambda _:block,content,flags=re.S)
        elif '<!-- work-examples:insert -->' in content:
            value = content.replace('<!-- work-examples:insert -->',block)
        else:
            raise ValueError('업무 예시 삽입 위치가 없습니다: '+str(path))
        outputs[path] = value
        if key in ('alm','plm'):
            outputs[Path('deploy')/(key+'-skill')/key/'references/work-examples.md'] = markdown(group)
    for path,value in outputs.items():
        target = root/path
        if not target.exists() or target.read_text(encoding='utf-8') != value:
            if check:
                stale.append(str(path))
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_text(value,encoding='utf-8')
    return stale


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='스킬 작업 사례 MD·웹 생성 및 일치 확인')
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    stale = render(args.root,args.check)
    print(json.dumps({'일치':not stale,'갱신필요':stale},ensure_ascii=False))
    raise SystemExit(1 if stale else 0)

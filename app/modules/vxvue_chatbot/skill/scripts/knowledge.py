"""Read-only, dependency-free XLSX evidence for the VXvue local skill.

Returns source records, never product Pass/Fail or semantic TC coverage verdicts.
No source workbook, correction, or index is written by this program.
"""
import argparse
import copy
import hashlib
import json
import posixpath
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

DEFAULT_ROOT = Path.home() / 'vxvue-knowledge'
N = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
     'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
ALIASES = {'ecoray': ('ecoray', '에코레이', 'egen')}


def column_number(name):
    result = 0
    for char in name: result = result * 26 + ord(char) - 64
    return result


def column_name(number):
    result = ''
    while number:
        number, rem = divmod(number - 1, 26)
        result = chr(65 + rem) + result
    return result


def coordinates(ref):
    match = re.fullmatch(r'([A-Z]+)(\d+)', ref)
    if not match: raise ValueError(f'Invalid cell reference: {ref}')
    return column_number(match[1]), int(match[2])


def enabled(node):
    return node is not None and node.get('val', '1').lower() not in ('0', 'false', 'off')


def rich_text(node):
    if node is None: return '', []
    runs = node.findall('s:r', N)
    if not runs: return ''.join(node.itertext()), []
    parts = [{'text': ''.join(r.find('s:t', N).itertext()) if r.find('s:t', N) is not None else '',
              'strike': enabled(r.find('s:rPr/s:strike', N))} for r in runs]
    return ''.join(p['text'] for p in parts), parts


def relationships(z, xml_path):
    rel_path = posixpath.join(posixpath.dirname(xml_path), '_rels', posixpath.basename(xml_path) + '.rels')
    if rel_path not in z.namelist(): return {}
    result = {}
    for rel in ET.fromstring(z.read(rel_path)):
        if rel.get('TargetMode') == 'External': continue
        target = rel.get('Target', '')
        result[rel.get('Id')] = {'path': target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(xml_path), target)), 'type': rel.get('Type', '')}
    return result


def read_workbook(path):
    with ZipFile(path) as z:
        shared = [rich_text(si) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        styles = ET.fromstring(z.read('xl/styles.xml')) if 'xl/styles.xml' in z.namelist() else None
        font_nodes = styles.findall('s:fonts/s:font', N) if styles is not None else []
        fonts = [enabled(f.find('s:strike', N)) for f in font_nodes]
        strikes = []
        for xf in styles.findall('s:cellXfs/s:xf', N) if styles is not None else []:
            index = int(xf.get('fontId', 0))
            strikes.append(fonts[index] if index < len(fonts) else False)
        rels = relationships(z, 'xl/workbook.xml')
        result = []
        for sheet in ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', N):
            xml_path = rels[sheet.get('{'+N['r']+'}id')]['path']
            doc = ET.fromstring(z.read(xml_path))
            comments = {}
            for rel in relationships(z, xml_path).values():
                if rel['type'].endswith('/comments'):
                    for comment in ET.fromstring(z.read(rel['path'])).findall('s:commentList/s:comment', N):
                        comments[comment.get('ref')] = rich_text(comment.find('s:text', N))[0]
            record = {'name': sheet.get('name'), 'state': sheet.get('state', 'visible'), 'cells': {},
                      'merges': [m.get('ref') for m in doc.findall('s:mergeCells/s:mergeCell', N)],
                      'hidden_rows': [], 'hidden_columns': [], 'max_row': 0, 'max_column': 0}
            for col in doc.findall('s:cols/s:col', N):
                if col.get('hidden') in ('1','true'):
                    record['hidden_columns'].extend(range(int(col.get('min')), int(col.get('max'))+1))
            for row in doc.findall('s:sheetData/s:row', N):
                if row.get('hidden') in ('1','true'): record['hidden_rows'].append(int(row.get('r')))
                for c in row.findall('s:c', N):
                    ref = c.get('r')
                    cn, rn = coordinates(ref)
                    record['max_row'] = max(record['max_row'], rn)
                    record['max_column'] = max(record['max_column'], cn)
                    value = c.findtext('s:v', default='', namespaces=N)
                    runs = []
                    if c.get('t') == 's': value, runs = shared[int(value)]
                    elif c.get('t') == 'inlineStr': value, runs = rich_text(c.find('s:is', N))
                    style = int(c.get('s', 0))
                    whole_strike = strikes[style] if style < len(strikes) else False
                    nonempty = [r for r in runs if r['text']]
                    if whole_strike or (nonempty and all(r['strike'] for r in nonempty)): validity = 'deleted'
                    elif any(r['strike'] for r in nonempty): validity = 'mixed-strike'
                    else: validity = 'current'
                    formula = c.findtext('s:f', namespaces=N)
                    if formula is not None and validity == 'current': validity = 'formula-review'
                    record['cells'][ref] = {'cell': ref, 'anchor': ref, 'text': value,
                                            'data_type': c.get('t', 'n'), 'formula': formula,
                                            'validity': validity, 'runs': runs, 'comment': comments.get(ref)}
            result.append(record)
        return result


def cell(sheet, ref):
    cn, rn = coordinates(ref)
    anchor = ref
    for merged in sheet['merges']:
        start, end = merged.split(':') if ':' in merged else (merged, merged)
        x1,y1 = coordinates(start); x2,y2 = coordinates(end)
        if x1 <= cn <= x2 and y1 <= rn <= y2:
            anchor = start
            break
    value = copy.deepcopy(sheet['cells'].get(anchor, {'text':'', 'validity':'missing', 'formula':None, 'comment':None, 'runs':[]}))
    value.update(cell=ref, anchor=anchor, hidden_row=rn in sheet['hidden_rows'], hidden_column=cn in sheet['hidden_columns'])
    return value


def support_status(text):
    value = text.strip().casefold()
    if value.startswith('not supported'): return 'unsupported'
    if value == 'supported': return 'supported'
    if value.startswith('supported'): return 'conditional'
    return 'unknown'


def model_matches(text, model):
    aliases = ALIASES.get(model.casefold(), (model.casefold(),))
    return any(re.search(r'(?<![a-z0-9])'+re.escape(alias)+r'(?![a-z0-9])', text.casefold()) for alias in aliases)


def latest_revision(paths):
    def key(path):
        match = re.search(r'Rev\.?([\d.]+)', path.name, re.I)
        return tuple(int(x) for x in match[1].strip('.').split('.')) if match else ()
    if not paths: return None
    best = max(key(p) for p in paths)
    candidates = [p for p in paths if key(p) == best]
    if len(candidates) != 1: raise ValueError('Ambiguous latest revision: '+', '.join(str(p) for p in candidates))
    return candidates[0]


def choose_workbook(root, name):
    paths = [p for p in root.rglob('*.xlsx') if name.casefold() in p.name.casefold() and not p.name.startswith('~$')]
    if not paths: raise FileNotFoundError(f'Workbook not found under {root}: {name}')
    if len(paths) == 1: return paths[0]
    def key(p):
        match = re.search(r'\((\d{6}|\d{8})\)', p.stem)
        return ('20'+match[1] if len(match[1])==6 else match[1]) if match else ''
    best = max(key(p) for p in paths)
    candidates = [p for p in paths if key(p) == best]
    if len(candidates) != 1 or not best: raise ValueError('Ambiguous workbook candidates: '+', '.join(str(p) for p in candidates))
    return candidates[0]


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(root):
    if not root.is_dir(): raise FileNotFoundError(f'Knowledge folder unavailable: {root}')
    paths = sorted(p for p in root.rglob('*') if p.is_file())
    prompt = latest_revision([p for p in paths if p.name.startswith('VXvue_검증_DB_AI_지침_프롬프트_Rev') and p.suffix == '.txt'])
    guide = latest_revision([p for p in paths if p.name.startswith('[QA 작성 규칙] VXvue TC 설계 및 자체검토 가이드_Rev') and p.suffix == '.md'])
    return {'root':str(root.resolve()), 'files':[{'path':str(p), 'bytes':p.stat().st_size} for p in paths],
            'latest_prompt': str(prompt) if prompt else None, 'latest_qa_guide': str(guide) if guide else None}


def tc_header_row(sheet):
    for row in range(1,min(15,sheet['max_row'])+1):
        values = [cell(sheet,column_name(c)+str(row))['text'].strip().casefold() for c in range(1,min(sheet['max_column'],12)+1)]
        if 'tc_id' in values or ('function' in values and ('test procedure' in values or 'title' in values)): return row
        if 'test item' in values and 'step' in values and any(v.startswith('expected result') for v in values): return row
    if sheet['name'] not in ('Generator','기본기능'):
        for row in range(1,min(5,sheet['max_row'])+1):
            versions = [cell(sheet,column_name(c)+str(row))['text'] for c in range(1,sheet['max_column']+1)]
            if sum(bool(re.match(r'^V\d+\.\d+\.',v,re.I)) for v in versions)>=2: return row
    return 0


def tc_columns(sheet, model):
    hr = tc_header_row(sheet)
    if not hr: return []
    result = []
    for c in range(1,sheet['max_column']+1):
        col = column_name(c)
        headers = [cell(sheet,col+str(r)) for r in range(1,hr+1)]
        text = '\n'.join(v['text'] for v in headers if v['text'])
        bottom = headers[-1]['text'].casefold()
        legacy = sheet['name'] not in ('Generator','기본기능') and model_matches(sheet['name'],model)
        if legacy and bottom.strip().startswith('expected'): continue
        is_version = bool(re.match(r'^v?\d+\.\d+\.',bottom))
        if not model_matches(text,model) and not (legacy and (is_version or 'result' in bottom or '결과' in bottom or re.fullmatch(r'\d+차',bottom))): continue
        if 'comment' in bottom or '코멘트' in bottom: role = 'comment'
        elif '사양' in bottom or 'spec' in bottom: role = 'model-spec'
        else: role = 'execution-history'
        result.append({'column':col, 'role':role, 'headers':headers})
    return result


def tc_records(sheet, file, model, common_only=False):
    hr = tc_header_row(sheet)
    if not hr: return []
    columns = [] if common_only else tc_columns(sheet,model)
    common_end = 5 if sheet['name']=='기본기능' else 9 if sheet['name']=='Generator' else min(12,sheet['max_column'])
    if sheet['name'] not in ('Generator','기본기능'):
        history = [column_number(c['column']) for c in columns if c['role']=='execution-history']
        if history: common_end = min(history)-1
    result = []
    for row in range(hr+1,sheet['max_row']+1):
        common = []
        for c in range(1,common_end+1):
            ref = column_name(c)+str(row)
            value = cell(sheet,ref)
            if value['text'] or value['comment'] or value['formula'] is not None:
                value['field'] = cell(sheet,column_name(c)+str(hr))['text']
                common.append(value)
        model_cells = []
        for col in columns:
            value = cell(sheet,col['column']+str(row))
            value.update(role=col['role'],header=col['headers'])
            model_cells.append(value)
        other_cells = []
        if sheet['name'] not in ('Generator','기본기능'):
            selected_cols = {col['column'] for col in columns}
            for c in range(common_end+1,sheet['max_column']+1):
                if column_name(c) in selected_cols: continue
                value = cell(sheet,column_name(c)+str(row))
                if value['text'] or value['comment']:
                    value['role'] = 'unclassified-model-reference'
                    other_cells.append(value)
        if not common and not any(c['text'] or c['comment'] for c in model_cells+other_cells): continue
        id_col = next((column_name(c) for c in range(1,common_end+1) if cell(sheet,column_name(c)+str(hr))['text'].strip().casefold()=='tc_id'), None)
        result.append({'file':file, 'sheet':sheet['name'], 'row':row, 'tc_id':cell(sheet,id_col+str(row))['text'] or None if id_col else None,
                       'common':common, 'model_cells':model_cells, 'unclassified':other_cells,
                       'context':'primary' if sheet['name']=='Generator' else 'basic' if sheet['name']=='기본기능' else 'model-reference-verify-applicability'})
    return result


def vss_records(sheet, file, model):
    cols = [column_name(c) for c in range(5,sheet['max_column']+1) if model_matches('\n'.join(cell(sheet,column_name(c)+str(r))['text'] for r in (4,5,8,9)),model)]
    if not cols: return []
    result = []
    subject = ''
    for row in range(7,sheet['max_row']+1):
        device = cell(sheet,'A'+str(row))['text'].strip()
        if device.casefold()!='generator': continue
        sub = cell(sheet,'B'+str(row))
        if sub['text']: subject = sub['text'].strip()
        item = cell(sheet,'C'+str(row))
        if not item['text']: continue
        for col in cols:
            value = cell(sheet,col+str(row))
            result.append({'model':model, 'device':device, 'subject':subject, 'item':item['text'].strip(),
                           'description':cell(sheet,'D'+str(row))['text'], 'value':value, 'support':support_status(value['text']),
                           'source':{'file':file,'sheet':sheet['name'],'cell':value['cell'],'anchor':value['anchor']},
                           'model_header':[cell(sheet,col+str(r)) for r in (4,5,6,8,9,10)]})
    return result


def apply_corrections(records, corrections, model):
    result = copy.deepcopy(records)
    for record in result: record['effective'] = copy.deepcopy(record['value'])
    seen = set()
    for correction in corrections:
        required = ('model','subject','item','expected','user_statement')
        if any(not isinstance(correction.get(f),str) or not correction[f].strip() for f in required): raise ValueError('Correction requires model, subject, item, expected and explicit user_statement')
        if correction['model'].casefold()!=model.casefold(): raise ValueError('Correction model differs from requested model')
        scope = (correction['subject'],correction['item'])
        if scope in seen: raise ValueError('Duplicate correction scope')
        seen.add(scope)
        matched = [r for r in result if r['subject']==scope[0] and r['item']==scope[1]]
        if not matched: raise ValueError(f'Correction scope not found: {scope}')
        for record in matched:
            if correction.get('conditions','').strip():
                record.setdefault('conditional_corrections',[]).append(copy.deepcopy(correction))
                continue
            record['effective'] = {'text':correction['expected'], 'validity':'user-explicit', 'user_statement':correction['user_statement'], 'conditions':correction.get('conditions','')}
            record['support'] = support_status(correction['expected'])
    return result


def filter_records(records, query, limit):
    matched = [r for r in records if not query or query.casefold() in json.dumps(r,ensure_ascii=False).casefold()]
    returned = matched[:limit] if limit else matched
    return returned, {'total':len(records),'matched':len(matched),'returned':len(returned),'filtered':bool(query),'truncated':bool(limit and len(matched)>limit)}


def load_source(path):
    before = fingerprint(path)
    sheets = read_workbook(path)
    if before != fingerprint(path): raise ValueError(f'Source changed while reading; retry: {path.name}')
    return sheets, {'file':str(path.resolve()), 'sha256':before,'sheets':[{'name':s['name'],'state':s['state'],'rows':s['max_row'],'columns':s['max_column']} for s in sheets]}


def bundle(root, model, query='', limit=0, corrections=None):
    listing = inventory(root)
    vss_path = choose_workbook(root,'VSS Specification')
    tc_path = choose_workbook(root,'RA16-149-003_VXvue_Generator_Test_Case')
    vss, vss_source = load_source(vss_path)
    tc, tc_source = load_source(tc_path)
    requirements = [r for sheet in vss for r in vss_records(sheet,str(vss_path),model)]
    if not requirements: raise ValueError(f'Model not found in VSS Generator headers: {model}')
    requirements = apply_corrections(requirements,corrections or [],model)
    ordered = [s for s in tc if s['name']=='Generator'] + [s for s in tc if s['name']=='기본기능'] + [s for s in tc if model_matches(s['name'],model)]
    names = [s['name'] for s in ordered]
    if 'Generator' not in names or '기본기능' not in names: raise ValueError('Required Generator or 기본기능 sheet missing')
    rows = [r for sheet in ordered for r in tc_records(sheet,str(tc_path),model)]
    selected_vss, vss_scope = filter_records(requirements,query,limit)
    selected_tc, tc_scope = filter_records(rows,query,limit)
    for path, source in ((vss_path,vss_source),(tc_path,tc_source)):
        if source['sha256'] != fingerprint(path): raise ValueError(f'Source changed while building evidence; retry: {path.name}')
    return {'schema_version':1, 'generated_utc':datetime.now(timezone.utc).isoformat(), 'model':model,
            'authority':'VSS specification by default; explicit scoped user specification corrections take priority',
            'inventory':listing, 'sources':[vss_source,tc_source], 'sheet_order':names,
            'sheet_status':[{'sheet':s['name'],'header_row':tc_header_row(s),'parsed':bool(tc_header_row(s))} for s in ordered],
            'model_columns':{s['name']:tc_columns(s,model) for s in ordered},
            'vss':selected_vss,'tc':selected_tc,'scope':{'vss':vss_scope,'tc':tc_scope},
            'limitations':['Records are evidence; semantic coverage and product Pass/Fail require separate review.',
                           'Model sheets and execution columns can be historical; inspect their applicability before use.',
                           'No PDF/DOCX text, drawings, embedded images, formula calculation or conditional formatting rendering is performed.',
                           'Missing XML is requested only when exact definitions or configuration affect the requested answer.'],
            'xml_request_template':'database/인테그레이션 폴더 내에 있는 해당 모델의 XML 파일을 첨부해주세요.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('inventory','bundle','inspect'))
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    parser.add_argument('--model',default='Ecoray')
    parser.add_argument('--query',default='')
    parser.add_argument('--limit',type=int,default=0,help='0 returns every matched record')
    parser.add_argument('--file',type=Path)
    parser.add_argument('--sheet')
    parser.add_argument('--corrections',type=Path,help='JSON array of explicit user specification corrections; no persistent write')
    args=parser.parse_args()
    try:
        if args.limit<0: raise ValueError('limit must be >= 0')
        if args.mode=='inventory': result=inventory(args.root)
        elif args.mode=='bundle':
            corrections=json.loads(args.corrections.read_text(encoding='utf-8-sig')) if args.corrections else None
            if corrections is not None and not isinstance(corrections,list): raise ValueError('Corrections must be a JSON array')
            result=bundle(args.root,args.model,args.query,args.limit,corrections)
        else:
            if not args.file: raise ValueError('inspect requires --file')
            sheets,source=load_source(args.file)
            if args.sheet:
                sheets=[s for s in sheets if s['name']==args.sheet]
                if not sheets: raise ValueError(f'Sheet not found: {args.sheet}')
            records=[{'sheet':s['name'],'value':cell(s,ref)} for s in sheets for ref in s['cells']]
            records,scope=filter_records(records,args.query,args.limit)
            result={'source':source,'records':records,'scope':scope}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (OSError,ValueError,KeyError,IndexError,ET.ParseError,BadZipFile) as exc:
        print(json.dumps({'error':type(exc).__name__,'message':str(exc),'status':'source-read-failed'},ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())

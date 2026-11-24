"""QA 서버 장애 확인과 로컬 전환 도구 (REQ-QACLIENTFALLBACK-001~004).

probe     : 서버 연결을 정해진 횟수만 다시 시도해 제품별 server/local/stop을 정한다.
local     : VXvue·XIPL·Bellalun 로컬 자료를 Bellalun 검색 엔진으로 조회한다(제품별 폴더·캐시 분리).
check     : 두 CLI의 공통 Skill·Skill별 보충 블록·전역 지침 블록을 점검한다.
overlay   : 사라진 보충 블록을 백업 후 다시 붙인다.
install   : 저장소 원본에서 설치한다(백업 → 복사 → Junction → 블록 → 점검).

서버 자료와 운영 ALM·PLM 기록은 바꾸지 않는다. 인증정보를 읽거나 출력하지 않는다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

PACKAGE = Path(__file__).resolve().parents[1]
SKILL_NAME = 'qa-server-fallback'
DEFAULT_SERVER = 'http://10.13.0.222:24357'
LIMITS = {'attempts': 5, 'timeout': 15.0, 'deadline': 60.0}
DEFAULTS = {'attempts': 3, 'timeout': 5.0, 'interval': 1.0, 'deadline': 30.0}
PRODUCT_TARGET = {'alm': 'guide:alm', 'plm': 'guide:plm', 'vxvue': 'mcp', 'xipl': 'mcp', 'bellalun': 'mcp'}
SEPARATE_COPIES = ('alm', 'plm', 'vxvue-ai', 'bellalun-server-ai')
IDENTITY_MARKERS = {'vxvue-ai': '<!-- vxvue-ai-managed:'}
EXIT = {'server': 0, 'local': 10, 'stop': 20}


class Integrity(Exception):
    """Response arrived but its format or hash is wrong."""


class AuthFailure(Exception):
    pass


class ServerNotOk(Exception):
    """Server answered but reports itself unhealthy; not an outage we may route around."""


# ---------------------------------------------------------------- probe

def classify(exc):
    """Map one failed attempt to a state. Only `outage` is retried and may switch to local."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403, 407):
            return 'auth_error'
        return 'outage' if exc.code >= 500 else 'server_error'
    if isinstance(exc, (Integrity, json.JSONDecodeError, UnicodeDecodeError)):
        return 'integrity_error'
    if isinstance(exc, AuthFailure):
        return 'auth_error'
    if isinstance(exc, ServerNotOk):
        return 'server_error'
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return 'integrity_error'
        if isinstance(reason, PermissionError) or getattr(reason, 'winerror', None) == 10013:
            return 'auth_error'
        return 'outage'
    if isinstance(exc, ssl.SSLError):
        return 'integrity_error'
    if isinstance(exc, PermissionError) or getattr(exc, 'winerror', None) == 10013:
        return 'auth_error'
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionError, OSError, http.client.HTTPException)):
        return 'outage'
    return 'server_error'


def fetch(url, timeout):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'Cache-Control': 'no-cache'}), timeout=timeout) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise Integrity('응답 크기 상한 초과')
    return data


def check_guide(catalog_url, timeout):
    catalog = json.loads(fetch(catalog_url, timeout))
    release = str(catalog.get('release_path', ''))
    expected = str(catalog.get('manifest_sha256', ''))
    if not re.fullmatch('[A-Za-z0-9-]+', release) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise Integrity('자료 목록 형식 오류')
    manifest = fetch(catalog_url.rsplit('/', 1)[0] + '/' + release + '/manifest.json', timeout)
    if hashlib.sha256(manifest).hexdigest() != expected:
        raise Integrity('manifest SHA256 불일치')
    if not json.loads(manifest).get('release'):
        raise Integrity('manifest 형식 오류')
    return 'release ' + release


def check_health(url, timeout):
    body = json.loads(fetch(url, timeout))
    if body.get('status') != 'ok':
        raise ServerNotOk('health status ' + str(body.get('status')))
    return 'health ok'


def probe_target(name, check, settings, clock=time.monotonic, sleep=time.sleep):
    start = clock()
    attempts, detail, state = 0, '', 'outage'
    while attempts < settings['attempts']:
        remaining = settings['deadline'] - (clock() - start)
        if remaining <= 0.2:
            detail = (detail + '; ' if detail else '') + '전체 시간 상한 도달'
            break
        attempts += 1
        try:
            detail = check(min(settings['timeout'], remaining))
            state = 'ok'
            break
        except Exception as exc:  # classified below, never re-raised
            state = classify(exc)
            detail = f'{type(exc).__name__}: {getattr(exc, "reason", None) or getattr(exc, "code", None) or exc}'
            if state != 'outage':
                break
        if attempts < settings['attempts'] and settings['deadline'] - (clock() - start) > settings['interval']:
            sleep(settings['interval'])
    return {'state': state, 'attempts': attempts, 'elapsed_s': round(clock() - start, 2), 'detail': str(detail)[:300]}


def guide_url(config, name, server_base_override):
    if server_base_override:
        return server_base_override.rstrip('/') + f'/static/{name}-guide/current.json'
    for root in config['skill_roots']:
        path = Path(root) / name / 'server.json'
        if path.is_file():
            url = json.loads(path.read_text(encoding='utf-8-sig')).get('catalog_url')
            if url:
                return url
    return config['server_base'].rstrip('/') + f'/static/{name}-guide/current.json'


def probe(config, settings=None, server_base=None, repair=True):
    settings = dict(DEFAULTS, **(settings or {}))
    for key, limit in LIMITS.items():
        if not 0 < settings[key] <= limit:
            raise ValueError(f'{key}는 0보다 크고 {limit} 이하여야 합니다.')
    server_base = server_base or os.environ.get('QA_FALLBACK_SERVER_BASE')
    base = (server_base or config['server_base']).rstrip('/')
    checks = {
        'guide:alm': lambda t, u=guide_url(config, 'alm', server_base): check_guide(u, t),
        'guide:plm': lambda t, u=guide_url(config, 'plm', server_base): check_guide(u, t),
        'mcp': lambda t: check_health(base + '/health', t),
    }
    with ThreadPoolExecutor(len(checks)) as pool:
        futures = {name: pool.submit(probe_target, name, fn, settings) for name, fn in checks.items()}
        targets = {name: future.result() for name, future in futures.items()}
    decision = {}
    for product, target in PRODUCT_TARGET.items():
        state = targets[target]['state']
        decision[product] = 'server' if state == 'ok' else 'local' if state == 'outage' else 'stop'
    result = {'checked_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'server_base': base,
              'settings': settings, 'targets': targets, 'decision': decision}
    if repair:
        try:
            result['overlay_repair'] = [r for r in ensure_overlays(config) if r['status'] in ('added', 'updated')]
        except Exception as exc:
            result['overlay_repair_error'] = f'{type(exc).__name__}: {exc}'
    state_dir = Path(config['state_dir'])
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / 'last-probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def probe_exit(result):
    values = set(result['decision'].values())
    return EXIT['stop'] if 'stop' in values else EXIT['local'] if 'local' in values else EXIT['server']


# ---------------------------------------------------------------- local evidence

def wild(pattern, text):
    """Only `*` and `?` are wildcards; brackets in Korean file names stay literal."""
    rx = re.escape(pattern).replace(r'\*', '.*').replace(r'\?', '.')
    return re.fullmatch(rx, text, re.IGNORECASE | re.DOTALL) is not None


def load_engine(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError('Bellalun 검색 엔진이 없습니다: ' + str(path))
    spec = importlib.util.spec_from_file_location('qa_fallback_engine', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def product_settings(config, product):
    if product not in config['products']:
        raise ValueError('제품은 ' + ', '.join(config['products']) + ' 중 하나입니다.')
    p = config['products'][product]
    root = Path(p['root'])
    if not root.is_dir():
        raise FileNotFoundError(f'{product} 지식 폴더가 없습니다: {root}')
    return p, root.resolve(), Path(p['cache']).resolve()


def included(p, rel):
    return any(wild(g, rel) for g in p.get('include', ['*'])) and not any(wild(g, rel) for g in p.get('exclude', []))


def open_product(config, product):
    p, root, cache = product_settings(config, product)
    engine = load_engine(config['engine'])
    original = engine.source_files
    engine.source_files = lambda r: [f for f in original(r) if included(p, Path(f).relative_to(r).as_posix())]
    manifest = engine.refresh(root, cache)
    return engine, p, root, cache, manifest


def snapshot_id(manifest):
    rows = sorted(f"{f['relative_path']}\0{f.get('sha256', f['status'])}" for f in manifest['files'])
    return hashlib.sha256('\n'.join(rows).encode('utf-8')).hexdigest()[:16]


def version_hint(name):
    hints = []
    for d in re.findall(r'\((\d{6})\)', name):
        hints.append(f'자료일 20{d[:2]}-{d[2:4]}-{d[4:]}')
    for v in re.findall(r'V(\d+(?:\.\d+)+(?:W\d+)?)', name):
        hints.append('V' + v)
    for v in re.findall(r'Rev\s?(\d+)', name, re.IGNORECASE):
        hints.append('Rev' + v)
    for v in re.findall(r'(?<![\d.])(\d+\.\d+\.\d+)(?![\d.])', name):
        if 'V' + v not in hints:
            hints.append(v)
    return hints


def local_status(config, product):
    engine, p, root, cache, manifest = open_product(config, product)
    files, newest = [], None
    for f in manifest['files']:
        path = Path(f['absolute_path'])
        modified = datetime.fromtimestamp(path.stat().st_mtime).astimezone() if path.exists() else None
        if modified and (newest is None or modified > newest):
            newest = modified
        files.append({'file': f['relative_path'], 'status': f['status'], 'units': f.get('units', 0),
                      'sha256': (f.get('sha256') or '')[:16], 'modified': modified.isoformat(timespec='seconds') if modified else None,
                      'version': version_hint(Path(f['relative_path']).name), 'error': f.get('error')})
    excluded = []
    for base, dirs, names in os.walk(root):
        for n in names:
            rel = (Path(base) / n).relative_to(root).as_posix()
            if not included(p, rel):
                excluded.append(rel)
    present = [f['relative_path'] for f in manifest['files'] if f['status'] in ('indexed_text', 'partial_text')]
    missing = [g for g in p.get('required', []) if not any(wild(g, rel) for rel in present)]
    return {'mode': 'local', 'product': product, 'snapshot': snapshot_id(manifest), 'root': str(root), 'cache': str(cache),
            'checked_at': manifest.get('checked_at_utc') or manifest.get('refreshed_at_utc'),
            'refreshed_at_utc': manifest.get('refreshed_at_utc'),
            'newest_file_modified': newest.isoformat(timespec='seconds') if newest else None,
            'freshness_note': '로컬 판은 서버 공개본보다 오래됐을 수 있다. 서버 장애 중에는 비교할 수 없다.',
            'files': files, 'indexed': len(present), 'errors': [f['file'] for f in files if f['status'] == 'error'],
            'missing': missing, 'not_local': p.get('not_local', []),
            'excluded_count': len(excluded), 'excluded': excluded[:50], 'excluded_reason': '지난 판 또는 다른 프로젝트 자료(제품 설정의 제외 규칙)'}


def local_command(config, product, command, **kw):
    engine, p, root, cache, manifest = open_product(config, product)
    if command == 'search':
        output = engine.search(cache, kw['query'], kw.get('mode', 'any'), kw.get('limit', 20), kw.get('file'))
    elif command == 'get':
        output = engine.get_units(cache, kw['id'], kw.get('neighbors', 1))
    elif command == 'page':
        path, _ = engine.find_source(manifest, kw['file'], {'.pdf'})
        output = engine.render_pdf_page(path, cache, kw['page'], kw.get('scale', 2.0))
    elif command == 'revisions':
        path, _ = engine.find_source(manifest, kw['file'], {'.docx'})
        output = engine.docx_revisions(path, kw.get('offset', 0), kw.get('limit', 300))
    else:
        raise ValueError('알 수 없는 명령: ' + command)
    output = dict(output) if isinstance(output, dict) else {'result': output}
    output.update({'mode': 'local', 'product': product, 'snapshot': snapshot_id(manifest),
                   'errors': [f['relative_path'] for f in manifest['files'] if f['status'] == 'error']})
    return output


# ---------------------------------------------------------------- overlays and install

def overlay_spec(config):
    spec = json.loads((Path(config['fallback_skill']).parent / 'overlays.json').read_text(encoding='utf-8'))
    target = Path(config['fallback_skill']).as_posix()
    render = lambda text: text.replace('{{FALLBACK_SKILL}}', target)
    return spec, {k: render(v) for k, v in spec['skills'].items()}, render(spec['global'])


def split_overlay(text, begin, end):
    i = text.find('\n' + begin)
    if i < 0:
        return text, ''
    j = text.find(end + '\n', i)
    if j < 0:
        j = text.find(end, i)
        if j < 0:
            return text, ''
        return text[:i] + text[j + len(end):], text[i:j + len(end)]
    return text[:i] + text[j + len(end) + 1:], text[i:j + len(end) + 1]


def with_overlay(base, body, begin, end):
    return base + '\n' + begin + '\n' + body + '\n' + end + '\n'


def backup_file(config, path):
    stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
    target = Path(config['state_dir']) / 'backups' / stamp / (hashlib.sha256(str(path).encode()).hexdigest()[:8] + '-' + Path(path).parent.name + '-' + Path(path).name)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return str(target)


def ensure_block(config, path, body, begin, end, check_only=False):
    path = Path(path)
    if not path.is_file():
        return {'path': str(path), 'status': 'missing_file'}
    text = path.read_bytes().decode('utf-8')
    base, old = split_overlay(text, begin, end)
    new = with_overlay(base, body, begin, end)
    if new == text:
        return {'path': str(path), 'status': 'unchanged'}
    if check_only:
        return {'path': str(path), 'status': 'absent' if not old else 'outdated'}
    record = {'path': str(path), 'status': 'updated' if old else 'added', 'backup': backup_file(config, path)}
    path.write_bytes(new.encode('utf-8'))
    if path.read_bytes().decode('utf-8') != new:
        raise RuntimeError('기록 후 재조회 불일치: ' + str(path))
    return record


def skill_files(config):
    """Each (skill, SKILL.md) once; a junction and its target are one file."""
    spec, bodies, _ = overlay_spec(config)
    seen, out = set(), []
    for root in config['skill_roots']:
        for skill in bodies:
            path = Path(root) / skill / 'SKILL.md'
            key = os.path.normcase(str(path.resolve())) if path.exists() else str(path)
            if key in seen:
                continue
            seen.add(key)
            out.append((skill, path))
    return out


def ensure_overlays(config, check_only=False):
    spec, bodies, global_body = overlay_spec(config)
    results = []
    for skill, path in skill_files(config):
        record = ensure_block(config, path, bodies[skill], spec['begin'], spec['end'], check_only)
        results.append(dict(record, skill=skill))
    for path in config['global_files']:
        if Path(path).is_file() or not check_only:
            if not Path(path).exists():
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_text('', encoding='utf-8')
            results.append(dict(ensure_block(config, path, global_body, spec['begin'], spec['end'], check_only), skill='global'))
        else:
            results.append({'path': str(path), 'status': 'missing_file', 'skill': 'global'})
    return results


def is_link(path):
    path = Path(path)
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def check(config):
    spec, bodies, _ = overlay_spec(config)
    skill_dir = Path(config['fallback_skill']).parent
    problems = []
    for rel in ('scripts/qa_document_guard.py', 'references/legacy-validation.md'):
        if not (skill_dir / rel).is_file():
            problems.append('공통 변경 검사 파일 없음: ' + rel)
    links = []
    for root in config['skill_roots']:
        candidate = Path(root) / SKILL_NAME
        if not candidate.exists():
            problems.append('공통 Skill 없음: ' + str(candidate))
        elif candidate.resolve() != skill_dir.resolve():
            problems.append('공통 Skill이 다른 폴더를 가리킴: ' + str(candidate))
        links.append({'path': str(candidate), 'link': is_link(candidate), 'resolves_to': str(candidate.resolve()) if candidate.exists() else None})
    overlays = ensure_overlays(config, check_only=True)
    for r in overlays:
        if r['status'] != 'unchanged':
            problems.append(f"보충 블록 {r['status']}: {r['path']}")
    identity = []
    for skill, path in skill_files(config):
        if not path.is_file():
            continue
        base, _ = split_overlay(path.read_bytes().decode('utf-8'), spec['begin'], spec['end'])
        name = re.search(r'^name:\s*(\S+)', base, re.MULTILINE)
        ok = bool(name and name.group(1) == skill) and IDENTITY_MARKERS.get(skill, '') in base
        identity.append({'skill': skill, 'path': str(path), 'name': name.group(1) if name else None, 'ok': ok})
        if not ok:
            problems.append('Skill 이름·설치 표시 확인 필요: ' + str(path))
    copies = []
    if len(config['skill_roots']) > 1:
        for skill in SEPARATE_COPIES:
            files = [Path(r) / skill / 'SKILL.md' for r in config['skill_roots']]
            present = [f for f in files if f.is_file()]
            same = len(present) == len(files) and len({f.read_bytes() for f in present}) == 1
            copies.append({'skill': skill, 'identical': same, 'present': len(present)})
            if present and not same:
                problems.append(f'두 CLI의 {skill} SKILL.md가 다름')
    return {'ok': not problems, 'problems': problems, 'fallback_skill': links, 'overlays': overlays,
            'identity': identity, 'cli_copies': copies}


def make_link(link, target):
    if os.name == 'nt':
        done = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError('Junction 생성 실패: ' + (done.stderr or done.stdout).strip())
    else:
        os.symlink(target, link, target_is_directory=True)


def default_config(home, python, server_base, roots):
    home = Path(home)
    codex_skill = home / '.codex/skills' / SKILL_NAME
    bellalun_skill = home / '.codex/skills/bellalun-local-knowledge'
    bellalun_root = roots.get('bellalun')
    config_file = bellalun_skill / 'knowledge-config.json'
    if not bellalun_root and config_file.is_file():
        bellalun_root = json.loads(config_file.read_text(encoding='utf-8-sig')).get('knowledge_root')
    state = home / '.local/state' / SKILL_NAME
    return {
        'schema': 1,
        'python_executable': str(python),
        'engine': str(bellalun_skill / 'scripts/knowledge.py'),
        'server_base': server_base,
        'state_dir': str(state),
        'fallback_skill': str(codex_skill / 'SKILL.md'),
        'skill_roots': [str(home / '.codex/skills'), str(home / '.claude/skills')],
        'global_files': [str(home / '.claude/CLAUDE.md'), str(home / '.codex/AGENTS.md')],
        'products': {
            'vxvue': {'root': str(roots['vxvue']), 'include': ['*'], 'exclude': ['_이전판/*', '*/_이전판/*'],
                      'cache': str(state / 'cache/vxvue'),
                      'required': ['(사양서) VXvue 사양서*.pdf', '[Skill 지침] VXvue.md', '[QA 작성 규칙]*.md', '(TC)*.xlsx', '(매뉴얼)*'],
                      'not_local': ['Polarion VP 상세·관계·필터 조회(서버 vxvue-ai 도구)', 'VXvue Redmine 조회(서버 도구)',
                                    'Generator 근거 도구', '매뉴얼 개정 검토 보고서 도구', '서버 통합 QA 지침(get_workflow) 최신판']},
            'xipl': {'root': str(roots['xipl']), 'include': ['XIPL User Manual*.docx', 'XIPL*.pdf'], 'exclude': [],
                     'cache': str(state / 'cache/xipl'), 'required': ['XIPL User Manual*'],
                     'not_local': ['XIPL.STUDIO 매뉴얼', '서버 XIPL 개정 이력·문서 목록']},
            'bellalun': {'root': str(bellalun_root), 'include': ['*'], 'exclude': [],
                         'cache': str(bellalun_skill / '.cache'),
                         'required': ['(사양서) Bellalun Viewer ALM 사양서*.pdf', 'Bellalun Viewer QA 지침.md', '(TC)*.xlsx', '(매뉴얼)*'],
                         'not_local': ['서버 경유 Polarion VP 조회(bellalun_get_polarion_item)', '서버 통합 QA 지침 최신판(로컬 references/bellalun-qa-guide.md 사용)']},
        },
    }


def guard_resources(source):
    source = Path(source).resolve()
    common = source.parents[1] / 'deploy/qa-validation'
    resources = {}
    for rel in ('scripts/qa_document_guard.py', 'references/legacy-validation.md'):
        bundled = source / rel
        resources[rel] = (bundled if bundled.is_file() else common / rel).read_bytes().decode('utf-8')
    return resources


def install(home=None, python=None, server_base=DEFAULT_SERVER, roots=None, source=PACKAGE):
    home = Path(home or Path.home())
    roots = dict(roots or {})
    automation = Path(source).resolve().parents[3] if len(Path(source).resolve().parents) > 3 else Path('.')
    roots.setdefault('vxvue', automation / 'projects/vxvue/VXvue 지식파일')
    roots.setdefault('xipl', automation / 'projects/xipl-virtual-memory-automation/docs')
    bellalun_cfg = home / '.codex/skills/bellalun-local-knowledge/knowledge-config.json'
    if not python and bellalun_cfg.is_file():
        python = json.loads(bellalun_cfg.read_text(encoding='utf-8-sig')).get('python_executable')
    python = python or sys.executable
    config = default_config(home, python, server_base, roots)
    skill_dir = Path(config['fallback_skill']).parent
    rendered = {
        'SKILL.md': (Path(source) / 'SKILL.md').read_text(encoding='utf-8').replace('{{PYTHON}}', Path(python).as_posix()).replace('{{SKILL_DIR}}', skill_dir.as_posix()),
        'overlays.json': (Path(source) / 'overlays.json').read_text(encoding='utf-8'),
        'scripts/qa_fallback.py': (Path(source) / 'scripts/qa_fallback.py').read_text(encoding='utf-8'),
        'fallback-config.json': json.dumps(config, ensure_ascii=False, indent=2) + '\n',
    }
    rendered.update(guard_resources(source))
    backups, changed = [], []
    if skill_dir.exists() and any(not (skill_dir / k).is_file() or (skill_dir / k).read_bytes() != v.encode('utf-8') for k, v in rendered.items()):
        target = Path(config['state_dir']) / 'backups' / datetime.now().strftime('%Y%m%dT%H%M%S') / SKILL_NAME
        shutil.copytree(skill_dir, target, ignore=shutil.ignore_patterns('__pycache__'))
        backups.append(str(target))
    for rel, text in rendered.items():
        path = skill_dir / rel
        if not path.is_file() or path.read_bytes() != text.encode('utf-8'):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode('utf-8'))
            changed.append(str(path))
        if path.read_bytes().decode('utf-8') != text:
            raise RuntimeError('설치 후 재조회 불일치: ' + str(path))
    for root in config['skill_roots'][1:]:
        link = Path(root) / SKILL_NAME
        if link.exists() or is_link(link):
            if link.resolve() != skill_dir.resolve():
                raise RuntimeError('기존 폴더가 있어 연결을 만들지 않았습니다. 비교 후 정리하세요: ' + str(link))
        else:
            link.parent.mkdir(parents=True, exist_ok=True)
            make_link(link, skill_dir)
            changed.append(str(link) + ' -> ' + str(skill_dir))
    overlays = ensure_overlays(config)
    backups += [r['backup'] for r in overlays if r.get('backup')]
    result = check(config)
    result.update({'installed': str(skill_dir), 'changed': changed, 'backups': backups,
                   'overlay_changes': [r for r in overlays if r['status'] in ('added', 'updated')]})
    return result


# ---------------------------------------------------------------- CLI

def load_config(path=None):
    path = Path(path or PACKAGE / 'fallback-config.json')
    if not path.is_file():
        raise FileNotFoundError('설정 파일이 없습니다. 저장소 원본에서 install을 실행하세요: ' + str(path))
    return json.loads(path.read_text(encoding='utf-8'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--config')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('probe')
    p.add_argument('--attempts', type=int, default=DEFAULTS['attempts'])
    p.add_argument('--timeout', type=float, default=DEFAULTS['timeout'])
    p.add_argument('--interval', type=float, default=DEFAULTS['interval'])
    p.add_argument('--deadline', type=float, default=DEFAULTS['deadline'])
    p.add_argument('--server-base')
    p.add_argument('--no-repair', action='store_true')
    loc = sub.add_parser('local')
    loc.add_argument('action', choices=['status', 'search', 'get', 'page', 'revisions'])
    loc.add_argument('--product', required=True)
    loc.add_argument('--query', action='append')
    loc.add_argument('--mode', choices=['any', 'all'], default='any')
    loc.add_argument('--limit', type=int)
    loc.add_argument('--file')
    loc.add_argument('--id')
    loc.add_argument('--neighbors', type=int, default=1)
    loc.add_argument('--page', type=int)
    loc.add_argument('--offset', type=int, default=0)
    sub.add_parser('check')
    ov = sub.add_parser('overlay')
    ov.add_argument('action', choices=['apply', 'check'])
    ins = sub.add_parser('install')
    ins.add_argument('--home')
    ins.add_argument('--python')
    ins.add_argument('--server-base', default=DEFAULT_SERVER)
    ins.add_argument('--vxvue-root')
    ins.add_argument('--xipl-root')
    ins.add_argument('--bellalun-root')
    a = parser.parse_args(argv)
    code = 0
    if a.command == 'install':
        roots = {k: v for k, v in {'vxvue': a.vxvue_root, 'xipl': a.xipl_root, 'bellalun': a.bellalun_root}.items() if v}
        out = install(a.home, a.python, a.server_base, roots)
        code = 0 if out['ok'] else 1
    else:
        config = load_config(a.config)
        if a.command == 'probe':
            out = probe(config, {'attempts': a.attempts, 'timeout': a.timeout, 'interval': a.interval, 'deadline': a.deadline},
                        a.server_base, repair=not a.no_repair)
            code = probe_exit(out)
        elif a.command == 'check':
            out = check(config)
            code = 0 if out['ok'] else 1
        elif a.command == 'overlay':
            out = ensure_overlays(config, check_only=a.action == 'check')
        elif a.action == 'status':
            out = local_status(config, a.product)
            code = 2 if out['errors'] else 0
        else:
            if a.action == 'search' and not a.query:
                parser.error('search에는 --query가 필요합니다.')
            if a.action == 'get' and not a.id:
                parser.error('get에는 --id가 필요합니다.')
            if a.action in ('page', 'revisions') and not a.file:
                parser.error('--file이 필요합니다.')
            if a.action == 'page' and not a.page:
                parser.error('page에는 --page가 필요합니다.')
            kw = {'query': a.query, 'mode': a.mode, 'file': a.file, 'id': a.id, 'neighbors': a.neighbors,
                  'page': a.page, 'offset': a.offset}
            if a.limit:
                kw['limit'] = a.limit
            out = local_command(config, a.product, a.action, **kw)
            code = 2 if out.get('errors') else 0
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8')
        print(json.dumps({'tool_error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)

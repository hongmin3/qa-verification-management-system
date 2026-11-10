"""Keep installed Claude Code/Codex skill copies identical to the QA server's published skills.

The server is the reference. Each package is downloaded and verified before anything is written:
VXvue/Bellalun server skills come from the live download routes, ALM/PLM through the
current.json → manifest.json → zip SHA256 chain. Changed files are written atomically after the
whole skill folder is backed up. Local-only files (config, notes) are kept; nothing is deleted.
"""
import argparse
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile

DYNAMIC = {'vxvue-ai': '/vxvue-desktop/skill.zip', 'bellalun-server-ai': '/vxvue-desktop/bellalun-skill.zip'}
GUIDED = ('alm', 'plm')
SKILLS = ('vxvue-ai', 'bellalun-server-ai', 'alm', 'plm')
STATE_ROOT = Path.home() / '.local/state/qa-skill-sync'


class SyncError(Exception):
    pass


def http_fetch(url, timeout=30):
    try:
        with urlopen(Request(url, headers={'Cache-Control': 'no-cache'}), timeout=timeout) as response:
            return response.read()
    except Exception as exc:  # network, HTTP status, timeout
        raise SyncError(f'{url}: {type(exc).__name__}') from None


def download(server, name, fetch):
    base = server.rstrip('/')
    if name in DYNAMIC:
        return fetch(base + DYNAMIC[name])
    if name not in GUIDED:
        raise SyncError(f'unknown skill {name}')
    current = json.loads(fetch(f'{base}/static/{name}-guide/current.json'))
    release = str(current.get('release_path', ''))
    if not release or '/' in release or '..' in release:
        raise SyncError(f'{name}: invalid release path')
    manifest_bytes = fetch(f'{base}/static/{name}-guide/{release}/manifest.json')
    if hashlib.sha256(manifest_bytes).hexdigest() != current.get('manifest_sha256'):
        raise SyncError(f'{name}: manifest SHA256 does not match current.json')
    expected = json.loads(manifest_bytes).get('files', {}).get(f'{name}-skill.zip', {}).get('sha256')
    data = fetch(f'{base}/static/{name}-guide/{release}/{name}-skill.zip')
    if not expected or hashlib.sha256(data).hexdigest() != expected:
        raise SyncError(f'{name}: skill zip SHA256 does not match the release manifest')
    return data


def unpack(name, data):
    """`{relative path: bytes}` for one skill; every entry must live under `<name>/`."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SyncError(f'{name}: not a zip file') from None
    files = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        path = PurePosixPath(info.filename.replace('\\', '/'))
        if path.is_absolute() or '..' in path.parts or len(path.parts) < 2 or path.parts[0] != name:
            raise SyncError(f'{name}: package entry outside the skill folder')
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise SyncError(f'{name}: symbolic links are not installed')
        files['/'.join(path.parts[1:])] = archive.read(info)
    if 'SKILL.md' not in files:
        raise SyncError(f'{name}: SKILL.md missing')
    return files


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.sync-')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def apply(name, files, target, *, check, backup_dir):
    changed = sorted(rel for rel, data in files.items()
                     if not (target / rel).is_file() or (target / rel).read_bytes() != data)
    record = {'path': str(target), 'changed': changed}
    if not changed or check:
        return record
    if target.exists():
        backup = backup_dir / hashlib.sha256(str(target).encode()).hexdigest()[:12] / name
        shutil.copytree(target, backup, ignore=shutil.ignore_patterns('__pycache__'))
        record['backup'] = str(backup)
    for rel in changed:
        atomic_write(target / rel, files[rel])
    stale = [rel for rel in changed if (target / rel).read_bytes() != files[rel]]
    if stale:
        raise SyncError(f'{name}: readback mismatch {stale}')
    return record


def sync(server, roots, names=SKILLS, *, fetch=http_fetch, backup_root=None, check=False):
    backup_dir = Path(backup_root or STATE_ROOT / 'backups') / datetime.now().strftime('%Y%m%dT%H%M%S%f')
    result = {'server': server, 'checked_at': datetime.now().astimezone().isoformat(timespec='seconds'), 'skills': {}}
    for name in names:
        try:
            files = unpack(name, download(server, name, fetch))
        except (SyncError, ValueError) as exc:
            result['skills'][name] = {'status': 'FAILED', 'error': str(exc)}
            continue
        targets, seen = [], set()
        try:
            for root in roots:
                target = Path(root) / name
                key = os.path.normcase(str(target.resolve()))   # a junction and its target are one folder
                if key in seen:
                    continue
                seen.add(key)
                targets.append(apply(name, files, target, check=check, backup_dir=backup_dir))
        except (OSError, SyncError) as exc:
            result['skills'][name] = {'status': 'FAILED', 'error': f'{type(exc).__name__}: {exc}', 'targets': targets}
            continue
        dirty = any(t['changed'] for t in targets)
        status = ('OUTDATED' if check else 'UPDATED') if dirty else 'UNCHANGED'
        result['skills'][name] = {'status': status, 'files': len(files), 'targets': targets,
                                  'sha256': hashlib.sha256(b''.join(k.encode() + v for k, v in sorted(files.items()))).hexdigest()}
    statuses = {skill['status'] for skill in result['skills'].values()}
    result['status'] = next(s for s in ('FAILED', 'UPDATED', 'OUTDATED', 'UNCHANGED') if s in statuses or s == 'UNCHANGED')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--server', required=True, help='QA server base URL')
    parser.add_argument('--root', action='append', type=Path,
                        help='skill root; default ~/.claude/skills and ~/.codex/skills')
    parser.add_argument('--skill', action='append', choices=SKILLS, help='default: all server skills')
    parser.add_argument('--check', action='store_true', help='report differences without writing')
    args = parser.parse_args()
    roots = args.root or [Path.home() / '.claude/skills', Path.home() / '.codex/skills']
    result = sync(args.server, roots, args.skill or SKILLS, check=args.check)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    (STATE_ROOT / 'last-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result['status'] == 'FAILED' else 0


if __name__ == '__main__':
    raise SystemExit(main())

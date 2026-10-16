"""Select the same verified QA source for server chat and automated QA."""
from __future__ import annotations
import hashlib
import re
from pathlib import Path

UNIFIED_MARKER = '<!-- qa-rules-format: unified-v1 -->'
OPERATIONS_MARKER = '<!-- qa-rules:operations -->'
DETAILS_MARKER = '<!-- qa-rules:details -->'


def unified_parts(text: str) -> tuple[str, str]:
    if text.count(UNIFIED_MARKER) != 1 or text.count(OPERATIONS_MARKER) != 1 or text.count(DETAILS_MARKER) != 1:
        raise ValueError('통합 QA 지침의 운영 지침·상세 규칙 구역을 확인해주세요.')
    before, operations = text.split(OPERATIONS_MARKER)
    if DETAILS_MARKER in before:
        raise ValueError('통합 QA 지침 구역 순서를 확인해주세요.')
    operations, details = operations.split(DETAILS_MARKER)
    for part in (operations, details):
        body = '\n'.join(line for line in part.splitlines() if line.strip() and not line.lstrip().startswith(('#', '<!--')))
        if not body.strip():
            raise ValueError('통합 QA 지침에 빈 운영 지침 또는 상세 규칙 구역이 있습니다.')
    return operations, details


def _read(doc):
    if doc.get('error'):
        raise ValueError('QA 지침 원본 동기화 실패를 확인해주세요.')
    try:
        data = Path(doc['path']).read_bytes()
        if doc.get('expected_sha256') and hashlib.sha256(data).hexdigest() != doc['expected_sha256']:
            raise ValueError('QA 지침 원본 해시가 동기화 목록과 다릅니다.')
        text = data.decode('utf-8-sig')
        if not text.strip():
            raise ValueError('QA 지침 원본이 비어 있습니다.')
        return text
    except (OSError, UnicodeError) as exc:
        raise ValueError('최신 QA 지침을 읽지 못했습니다. 동기화 상태를 확인해주세요.') from exc


def _revision(doc):
    found = re.search(r'Rev\.?\s*(\d+(?:\.\d+)*)', doc['name'], re.I)
    return tuple(map(int, found[1].split('.'))) if found else (0,)


def select_rule_documents(documents, *, require_prompt=False):
    """Unified declared sources fail closed; legacy products keep numeric selection."""
    guides = [d for d in documents if d['kind'] == 'qa_rules']
    unified = []
    for doc in guides:
        # Inspect bytes before selecting: a broken declaration must not fall back.
        try:
            text = Path(doc['path']).read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError):
            raise ValueError('QA 지침 원본을 읽지 못했습니다.') from None
        if '<!-- qa-rules-format:' in text:
            checked = _read(doc)
            unified_parts(checked)
            unified.append((doc, checked))
    if unified:
        if len({text for _, text in unified}) != 1:
            raise ValueError('통합 QA 지침 원본이 서로 다릅니다. 관리 원본을 확인해주세요.')
        return [unified[0][0]]
    selected = []
    for kind in ('instruction_prompt', 'qa_rules'):
        candidates = [d for d in documents if d['kind'] == kind]
        if not candidates:
            if kind == 'qa_rules' or require_prompt:
                raise ValueError('필수 QA 지침 원본이 없습니다. 지식 동기화를 확인해주세요.')
            continue
        latest = max(map(_revision, candidates))
        winners = [d for d in candidates if _revision(d) == latest]
        if len({_read(d) for d in winners}) != 1:
            raise ValueError('같은 개정 번호의 지침이 서로 다릅니다.')
        selected.append(winners[0])
    return selected


def collected_rule_documents(product, root=None):
    from app.core.product_knowledge import load_manifest, collected_path, product_dir
    docs=[]
    for asset in load_manifest(product,root).get('assets',[]):
        if asset.get('kind') not in ('qa_rules','instruction_prompt'):
            continue
        path=collected_path(product,asset,root)
        if not path.resolve().is_relative_to(product_dir(product,root).resolve()):
            raise ValueError('QA 지침 경로가 지식 폴더 밖을 가리킵니다.')
        docs.append(dict(kind=asset['kind'],name=asset['file_name'],path=str(path),error=asset.get('error'),expected_sha256=asset.get('sha256')))
    return select_rule_documents(docs)

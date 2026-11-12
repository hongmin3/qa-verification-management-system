"""한영 동의어 확장 (REQ-VXVUECHATBOT-021).

검색어의 한 단어가 동의어 묶음에 들어 있으면, 그 단어를 같은 묶음의 다른 말로 바꾼 변형 검색어를 만든다.
원래 검색어는 항상 그대로 먼저 쓰며, 확장은 후보를 더할 뿐 원래 결과를 바꾸거나 지우지 않는다.
"""
import re

SYNONYM_GROUPS = (
    ("버키", "Bucky", "Receptor", "리셉터"),
    ("재실행", "restart", "재기동", "재시작"),
    ("유지", "remember"),
)
MAX_VARIANTS = 4


def _group_of(word):
    folded = word.casefold()
    for group in SYNONYM_GROUPS:
        if folded in (g.casefold() for g in group):
            return group
    return None


def variants(query, limit=MAX_VARIANTS):
    """원래 검색어를 뺀 변형 검색어 목록(한 번에 한 단어만 바꾼다)."""
    if not isinstance(query, str):
        return []
    tokens = re.split(r'(\s+)', query.strip())
    per_word = []
    for i, token in enumerate(tokens):
        group = _group_of(token) if token.strip() else None
        if group:
            per_word.append([''.join(tokens[:i] + [alt] + tokens[i + 1:])
                             for alt in group if alt.casefold() != token.casefold()])
    # 단어마다 번갈아 뽑아, 앞 단어의 변형만으로 상한이 차지 않게 한다.
    out = []
    for rank in range(max((len(w) for w in per_word), default=0)):
        for word in per_word:
            if rank < len(word) and word[rank] not in out and word[rank] != query.strip():
                out.append(word[rank])
                if len(out) >= limit:
                    return out
    return out


def expand_any(query, limit=MAX_VARIANTS):
    """match_mode=any 검색어에 동의어를 덧붙인다. 덧붙인 말 목록도 돌려준다."""
    if not isinstance(query, str):
        return query, []
    words = query.split()
    added = []
    for word in words:
        group = _group_of(word)
        for alt in group or ():
            if alt.casefold() not in (w.casefold() for w in words + added):
                added.append(alt)
    added = added[: limit * 2]
    return (query + ' ' + ' '.join(added)).strip() if added else query, added


def flag(value, name):
    """새 boolean 옵션 값. 배포 전 도구 스키마를 가진 클라이언트는 "true"/"false" 문자열로 보내므로 함께 받는다."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ('true', 'false'):
        return value.strip().lower() == 'true'
    raise ValueError(f'{name}는 boolean입니다.')

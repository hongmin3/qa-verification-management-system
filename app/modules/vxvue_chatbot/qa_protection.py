"""Preserve one approved QA template only while transporting its authoritative rule."""
import hashlib
import re
import uuid
from app.core.security_filter import mask_text

# Digest of the reviewed QA 29.8 literal; the internal address/path stays in the QA source only.
APPROVED_TEMPLATE_SHA256 = '344ae6ca6bc63784682a18788f5e7266fc13caf05346928a5171e259b56070ba'


def templates(rules):
    if '<!-- qa-rules-format: unified-v1 -->' not in rules:
        return []
    found = re.search(r'^## 29\.8\s[^\n]*\n(.*?)(?=^## |\Z)', rules, re.M | re.S)
    if not found:
        return []
    return [text for text in re.findall(r'`(\\\\[^`\r\n]+)`', found.group(1))
            if hashlib.sha256(text.encode('utf-8')).hexdigest() == APPROVED_TEMPLATE_SHA256]


def mask_qa_text(text, rules):
    replacements = {}
    protected = text
    for template in templates(rules):
        sentinel = 'QALOGTEMPLATE' + uuid.uuid4().hex.upper()
        pattern = re.compile(re.escape(template) + r'''(?=$|[\s`"'|])''')
        if pattern.search(protected):
            protected = pattern.sub(lambda _: sentinel, protected)
            replacements[sentinel] = template
    result, report = mask_text(protected)
    for sentinel, template in replacements.items():
        result = result.replace(sentinel, template)
    report.chars_before, report.chars_after = len(text), len(result)
    return result, report


def mask_qa_payload(value, rules):
    def walk(node):
        if isinstance(node, str): return mask_qa_text(node, rules)[0]
        if isinstance(node, list): return [walk(v) for v in node]
        if isinstance(node, dict): return {k:walk(v) for k,v in node.items()}
        return node
    return walk(value)

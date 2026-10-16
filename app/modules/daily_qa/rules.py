"""수집된 QA 원문 검사. 선택적 판 고정은 기존 제품에만 설정한다."""

from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from app.core.product_knowledge import KIND_INSTRUCTION_PROMPT, KIND_QA_RULES


REV_RE = re.compile(r"Rev\.?\s*(\d+(?:\.\d+)+)", re.IGNORECASE)


@dataclass
class RulesState:
    ok: bool
    found_rev: str
    reason: str
    guide_path: Path | None = None
    prompt_path: Path | None = None
    supported_rev: str = ""
    source_hashes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "found_rev": self.found_rev,
            "supported_rev": self.supported_rev,
            "reason": self.reason,
            "source_hashes": self.source_hashes,
            "guide": self.guide_path.name if self.guide_path else "",
            "prompt": self.prompt_path.name if self.prompt_path else "",
        }


def rev_of(file_name: str) -> str:
    match = REV_RE.search(file_name)
    return match.group(1) if match else ""


def check(product: str, root: Path | None = None, supported_rev: str = "") -> RulesState:
    """`supported_rev` 가 비어 있으면 판을 고정하지 않는다. 원문 읽기·필수 구역·해시는 검사한다."""
    from app.core.qa_rule_source import collected_rule_documents
    try:
        sources = collected_rule_documents(product, root)
    except ValueError as exc:
        return RulesState(False, "", str(exc), supported_rev=supported_rev)
    guide = next((Path(d["path"]) for d in sources if d["kind"] == KIND_QA_RULES), None)
    prompt = next((Path(d["path"]) for d in sources if d["kind"] == KIND_INSTRUCTION_PROMPT), None)
    if guide is None:
        return RulesState(False, "", "수집된 QA 규칙 파일이 없습니다. 지식 폴더 동기화를 확인하세요.", None, prompt, supported_rev)
    found = rev_of(guide.name)
    if supported_rev and found != supported_rev:
        return RulesState(
            False, found,
            f"QA 규칙 판이 Skill 기준과 다릅니다 (수집본 Rev{found or '?'} / Skill 기준 Rev{supported_rev}). "
            "제품 규칙 Skill 을 새 판에 맞춰 검토한 뒤 제품 설정의 qa_intelligence.rules.supported_rev 를 올리세요.",
            guide, prompt, supported_rev,
        )
    hashes = {Path(d["path"]).name: hashlib.sha256(Path(d["path"]).read_bytes()).hexdigest() for d in sources}
    return RulesState(True, found, "", guide, prompt, supported_rev, hashes)

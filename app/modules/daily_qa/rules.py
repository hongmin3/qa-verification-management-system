"""QA 규칙 판(Rev) 확인 (SPEC REQ-DAILY-010).

Skill 은 특정 판의 QA 규칙을 기준으로 쓰였다. 규칙이 새 판으로 바뀌었는데 Skill 이 그대로면
Gate 번호나 판정 기준이 어긋난 채로 결과가 나온다. 그래서 판이 다르면 AI 단계를 멈추고
사람에게 알린다. 규칙 원문은 사내 자료라 저장소에 넣지 않고 수집된 사본을 작업 폴더로 복사한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.core.product_knowledge import KIND_INSTRUCTION_PROMPT, KIND_QA_RULES, collected_assets, collected_path

#: Skill 이 기준으로 삼는 QA 규칙 판. Skill 을 새 판에 맞춰 고친 뒤에만 올린다.
SUPPORTED_RULES_REV = "1.17"

REV_RE = re.compile(r"Rev\.?\s*(\d+(?:\.\d+)+)", re.IGNORECASE)


@dataclass
class RulesState:
    ok: bool
    found_rev: str
    reason: str
    guide_path: Path | None = None
    prompt_path: Path | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "found_rev": self.found_rev,
            "supported_rev": SUPPORTED_RULES_REV,
            "reason": self.reason,
            "guide": self.guide_path.name if self.guide_path else "",
            "prompt": self.prompt_path.name if self.prompt_path else "",
        }


def rev_of(file_name: str) -> str:
    match = REV_RE.search(file_name)
    return match.group(1) if match else ""


def _pick(assets: list[dict], product: str, root: Path | None) -> Path | None:
    ranked = sorted(assets, key=lambda asset: tuple(int(part) for part in (rev_of(asset["file_name"]) or "0").split(".")))
    for asset in reversed(ranked):
        path = collected_path(product, asset, root)
        if path.is_file():
            return path
    return None


def check(product: str, root: Path | None = None) -> RulesState:
    guide = _pick(collected_assets(product, KIND_QA_RULES, root), product, root)
    prompt = _pick(collected_assets(product, KIND_INSTRUCTION_PROMPT, root), product, root)
    if guide is None:
        return RulesState(False, "", "수집된 QA 규칙 파일이 없습니다. 지식 폴더 동기화를 확인하세요.", None, prompt)
    found = rev_of(guide.name)
    if found != SUPPORTED_RULES_REV:
        return RulesState(
            False, found,
            f"QA 규칙 판이 Skill 기준과 다릅니다 (수집본 Rev{found or '?'} / Skill 기준 Rev{SUPPORTED_RULES_REV}). "
            "Skill 을 새 판에 맞춰 검토한 뒤 SUPPORTED_RULES_REV 를 올리세요.",
            guide, prompt,
        )
    return RulesState(True, found, "", guide, prompt)

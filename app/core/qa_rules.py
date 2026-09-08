"""QA 규칙 문서 로더 — 지침 프롬프트와 TC 작성 규칙을 기계가 쓸 수 있는 형태로 만든다.

QA는 제품별로 두 개의 규칙 문서를 유지한다.

- `<제품> 검증 DB AI 지침 프롬프트` — 역할·Skill Routing·Gate·우선순위 (약 14KB)
- `[QA 작성 규칙] <제품> TC 설계 및 자체검토 가이드_Rev1.12.md` — 세부 실행 절차 (약 43KB, 52개 절)

이 문서는 원래 GPTs(클라우드)에 통째로 올려 쓰던 것이다. 이 프로젝트는 같은 규칙을 **로컬에서
Gemini API로** 실행하므로, 규칙 전문을 매 호출마다 보내지 않는다. 대신:

1. 문서를 절(section) 단위로 쪼갠다.
2. 각 절이 어느 Skill(S01~S11)·어느 Gate(G1~G5)에 필요한지 태깅한다.
3. 호출 시점에 **그 Skill에 필요한 절만** 문자 예산 안에서 주입한다.

`akela compile`이 에이전트에게 지식을 잘라 주는 것과 같은 원리다. 43KB를 다 보내면 호출 한
번에 그것만으로 1만 토큰이 넘고, 규칙 대부분은 그 요청과 무관하다.

규칙 문서는 사내 QA 노하우라 저장소에 커밋하지 않는다. `data/product_knowledge/<slug>/`에
수집된 사본을 읽고, 없으면 규칙 없이 동작한다 (Gate가 `NEED_RULES`로 보고한다).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.core.product_knowledge import KIND_INSTRUCTION_PROMPT, KIND_QA_RULES, collected_assets, product_dir

# Skill 식별자. 지침 프롬프트 §3 Agent Skill Routing 과 규칙 §3 Skill Registry 의 정의를 따른다.
SKILL_TITLES: dict[str, str] = {
    "S01": "Issue Analysis",
    "S02": "Specification Trace",
    "S03": "TC Coverage Review",
    "S04": "Fix Verification",
    "S05": "Regression Impact",
    "S06": "Issue Writing & Closure",
    "S07": "Document Change Review",
    "S08": "API/WebSocket/DICOM/Integration",
    "S09": "Generator Verification",
    "S10": "Release Validation",
    "S11": "Usage Help",
}

GATE_TITLES: dict[str, str] = {
    "G1": "Source Completeness",
    "G2": "Specification Evidence",
    "G3": "TC & Root Cause Coverage",
    "G4": "Execution Feasibility",
    "G5": "Cross-check",
}

# 절 제목에 이 낱말이 있으면 해당 Skill 에 태깅한다. 절이 `S0x`를 직접 언급하면 그 태그가
# 우선하고, 이 표는 Skill 번호를 적지 않은 주제 절(예: §19 API Semantic)을 잇는 데 쓴다.
#
# 비교는 공백을 지운 뒤 수행한다 — 같은 규칙을 제품마다 `자체검토`/`자체 검토`처럼 다르게
# 적어 두기 때문이다. 그래서 이 표의 낱말도 제품 고유 용어가 아니라 **QA 공통 용어**만 쓴다.
TOPIC_SKILLS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("IssueAnalysis", ("S01",)),
    ("Issue분석", ("S01",)),
    ("결함판단", ("S01",)),
    ("입력분류", ("S01",)),
    ("SpecificationTrace", ("S02",)),
    ("사양조사", ("S02",)),
    ("사양유효", ("S02",)),
    ("사양품질", ("S02",)),
    ("사양분석", ("S02",)),
    ("분할사양", ("S02",)),
    ("TCCoverage", ("S03",)),
    ("TC설계", ("S03",)),
    ("TC첨삭", ("S03",)),
    ("TC비교", ("S03",)),
    ("TC문장", ("S03",)),
    ("TC출력", ("S03",)),
    ("TC형식", ("S03",)),
    ("테스트설계", ("S03",)),
    ("Step–Expected", ("S03",)),
    ("Issue–TC", ("S03",)),
    ("RootCauseCoverage", ("S03", "S05")),
    ("FixVerification", ("S04",)),
    ("FixedIssueChecklist", ("S04",)),
    ("수정확인", ("S04",)),
    ("TestStep상세도", ("S04", "S03")),
    ("Regression", ("S05",)),
    ("상태전이", ("S05",)),
    ("영향범위", ("S05",)),
    ("위험", ("S05",)),
    ("Issue작성", ("S06",)),
    ("Issue종료", ("S06",)),
    ("종료금지", ("S06",)),
    ("종료Comment", ("S06",)),
    ("문서개정", ("S07",)),
    ("DocumentChange", ("S07",)),
    ("문서사용규칙", ("S07",)),
    ("API", ("S08",)),
    ("WebSocket", ("S08",)),
    ("DICOM", ("S08",)),
    ("Generator", ("S09",)),
    ("Dose", ("S09",)),
    ("ProcedureManager", ("S09",)),
    ("Release", ("S10",)),
    ("사용법", ("S11",)),
    ("UsageHelp", ("S11",)),
)

# 절 제목에 이 낱말이 있으면 해당 Gate 에 태깅한다.
TOPIC_GATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SourceCompleteness", ("G1",)),
    ("필수정보확인", ("G1",)),
    ("작업범위", ("G1",)),
    ("SpecificationEvidence", ("G2",)),
    ("사양유효", ("G2",)),
    ("근거수준", ("G2",)),
    ("근거없는추론", ("G2",)),
    ("정보우선순위", ("G2",)),
    ("RootCauseCoverage", ("G3",)),
    ("Issue–TC", ("G3",)),
    ("ExecutionFeasibility", ("G4",)),
    ("원격검증", ("G4",)),
    ("DoseTable미제공", ("G4",)),
    ("Cross-check", ("G5",)),
    ("자체검토", ("G5",)),
    ("확인필요", ("G5",)),
)

# 어느 Skill 에도 속하지 않는 공통 절 중 **먼저** 넣을 것. 역할·원칙·근거 우선순위·출력
# 규칙은 어떤 Skill 을 돌려도 지켜야 하므로 공통 예산을 이 순서로 채운다.
GENERAL_PRIORITY: tuple[str, ...] = (
    "역할",
    "기본운영원칙",
    "기본원칙",
    "정보·근거우선순위",
    "정보우선순위",
    "출력규칙",
    "출력모드",
    "조사·추적성원칙",
    "확인필요표준표현",
)

SKILL_TOKEN_RE = re.compile(r"\bS(0[1-9]|1[01])\b")
GATE_TOKEN_RE = re.compile(r"\bG([1-5])\b")
REVISION_RE = re.compile(r"Rev\.?\s*(\d+(?:\.\d+)+)", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
SECTION_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*)$")


def normalize_markdown(text: str) -> str:
    """QA 규칙 MD의 escape 를 풀어 일반 Markdown 으로 만든다.

    실제 파일은 편집 도구가 모든 특수문자를 escape 한 상태로 저장돼 있다 — `\\#`, `\\-`,
    `&#x20;`, 그리고 모든 줄 사이에 빈 줄이 하나 더. 그대로 파싱하면 제목이 하나도 잡히지
    않는다(실제로 `grep '^#'` 결과가 0건이었다).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = html.unescape(text)
    # `\#`, `\-`, `\|`, `\&`, `\*`, `\_`, `\.` 처럼 ASCII 특수문자 앞의 backslash 를 제거한다.
    text = re.sub(r"\\([#\-|&*_.`>\[\]()~+=!])", r"\1", text)
    # NBSP(&#x20; 가 풀린 것 포함)를 보통 공백으로.
    text = text.replace("\u00a0", " ")
    # 줄마다 빈 줄이 하나 더 들어간 형태를 원래대로 되돌린다.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


@dataclass
class RuleSection:
    """규칙 문서의 절 하나. 이것이 프롬프트에 주입되는 최소 단위다."""

    document: str
    number: str
    title: str
    level: int
    body: str
    skills: tuple[str, ...] = ()
    gates: tuple[str, ...] = ()

    @property
    def anchor(self) -> str:
        """근거로 인용할 때 쓰는 식별자. `VXvue TC 가이드#32` 형태."""
        return f"{self.document}#{self.number or self.title}"

    @property
    def heading(self) -> str:
        return f"{self.number} {self.title}".strip()

    @property
    def size(self) -> int:
        return len(self.body)

    def render(self) -> str:
        return f"### {self.heading}\n{self.body}".strip()


@dataclass
class RuleDocument:
    """규칙 문서 하나(지침 프롬프트 또는 TC 작성 규칙)."""

    kind: str
    name: str
    revision: str
    path: str
    sections: list[RuleSection] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(section.size for section in self.sections)


@dataclass
class RuleSet:
    """한 제품의 규칙 전체. 프롬프트에 무엇을 넣을지는 `slice()` 가 정한다."""

    product: str
    documents: list[RuleDocument] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return bool(self.documents)

    @property
    def sections(self) -> list[RuleSection]:
        return [section for document in self.documents for section in document.sections]

    @property
    def revision(self) -> str:
        """대표 리비전 — TC 작성 규칙의 Rev 를 쓴다 (지침 프롬프트와 같은 Rev 로 관리된다)."""
        for document in self.documents:
            if document.kind == KIND_QA_RULES and document.revision:
                return document.revision
        for document in self.documents:
            if document.revision:
                return document.revision
        return ""

    def revisions(self) -> dict[str, str]:
        return {document.name: document.revision for document in self.documents}

    def slice(self, skill: str | None = None, gate: str | None = None, char_budget: int = 6000, general_ratio: float = 0.3) -> list[RuleSection]:
        """이 Skill/Gate 에 필요한 절만 예산 안에서 고른다.

        예산을 두 몫으로 나눈다. **Skill 전용 절**이 먼저 자리를 잡고, 어느 Skill 에도 속하지
        않는 **공통 절**(역할·원칙·출력 규칙)은 남은 `general_ratio` 몫만 쓴다. 한 예산으로
        합쳐 두면 문서 앞쪽 공통 절이 예산을 다 먹고 정작 그 Skill 규칙이 빠지는 일이 생긴다
        (실제로 S01 slice 5.8KB 중 Skill 전용은 0.4KB뿐이었다).

        공통 절은 문서 순서가 아니라 `GENERAL_PRIORITY` 순으로 넣는다 — 어떤 Skill 이든
        지켜야 하는 규칙이 먼저 들어가게 하기 위함이다.
        """
        targeted, general = [], []
        for section in self.sections:
            if (skill and skill in section.skills) or (gate and gate in section.gates):
                targeted.append(section)
            elif not section.skills and not section.gates:
                general.append(section)

        def general_rank(section: RuleSection) -> tuple[int, int]:
            squashed = _squash(section.title)
            for index, keyword in enumerate(GENERAL_PRIORITY):
                if _squash(keyword) in squashed:
                    return (index, section.size)
            return (len(GENERAL_PRIORITY), section.size)

        selected: list[RuleSection] = []
        used = 0
        for section in targeted:
            if used + section.size > char_budget:
                continue
            selected.append(section)
            used += section.size

        general_budget = min(char_budget - used, int(char_budget * general_ratio))
        general_used = 0
        for section in sorted(general, key=general_rank):
            if general_used + section.size > general_budget:
                continue
            selected.append(section)
            general_used += section.size
        return selected

    def dropped_sections(self, skill: str | None = None, gate: str | None = None, char_budget: int = 6000) -> list[RuleSection]:
        kept = {section.anchor for section in self.slice(skill, gate, char_budget)}
        return [section for section in self.sections if section.anchor not in kept]

    def render(self, skill: str | None = None, gate: str | None = None, char_budget: int = 6000) -> str:
        sections = self.slice(skill, gate, char_budget)
        if not sections:
            return ""
        header = f"[{self.product} QA 규칙 {self.revision}] — 이 작업에 해당하는 절만 발췌"
        return "\n\n".join([header, *(section.render() for section in sections)])

    def find(self, anchor: str) -> RuleSection | None:
        return next((section for section in self.sections if section.anchor == anchor), None)


def _squash(text: str) -> str:
    """공백을 지우고 소문자로. 제품마다 `자체검토`/`자체 검토`처럼 표기가 갈리기 때문이다."""
    return re.sub(r"\s+", "", text).casefold()


def _tag_section(
    title: str,
    body: str,
    inherited: tuple[tuple[str, ...], tuple[str, ...]] = ((), ()),
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """절 하나에 Skill·Gate 태그를 붙인다.

    ① 제목이 `S0x`/`Gn` 을 직접 적었으면 그것만 쓴다 (Skill Registry 절이 이 경우다).
    ② 아니면 제목의 주제 낱말로 잇는다.
    ③ 그래도 없으면 **부모 절의 태그를 물려받는다.** `# 32. Regression 영향 범위` 아래의
       `## 직접`/`## 상태`/`## 데이터`처럼, 하위 절 제목만으로는 주제를 알 수 없고 부모가
       주제를 갖는 구조가 두 제품 문서 모두에서 쓰인다.
    ④ 마지막으로 본문이 특정 Skill 하나만 가리키면 보조 신호로 쓴다.
    """
    explicit_skills = tuple(dict.fromkeys(f"S{match}" for match in SKILL_TOKEN_RE.findall(title)))
    explicit_gates = tuple(dict.fromkeys(f"G{match}" for match in GATE_TOKEN_RE.findall(title)))
    if explicit_skills or explicit_gates:
        return explicit_skills, explicit_gates

    squashed = _squash(title)
    skills: list[str] = []
    gates: list[str] = []
    for keyword, mapped in TOPIC_SKILLS:
        if _squash(keyword) in squashed:
            skills.extend(mapped)
    for keyword, mapped in TOPIC_GATES:
        if _squash(keyword) in squashed:
            gates.extend(mapped)
    if skills or gates:
        return tuple(dict.fromkeys(skills)), tuple(dict.fromkeys(gates))

    if inherited[0] or inherited[1]:
        return inherited

    body_skills = SKILL_TOKEN_RE.findall(body)
    # 본문이 딱 한 Skill 만 가리킬 때에만 태깅한다. 여러 Skill 을 나열하는 절은 공통 절이다.
    if len(set(body_skills)) == 1:
        skills.append(f"S{body_skills[0]}")
    body_gates = GATE_TOKEN_RE.findall(body)
    if len(set(body_gates)) == 1:
        gates.append(f"G{body_gates[0]}")
    return tuple(dict.fromkeys(skills)), tuple(dict.fromkeys(gates))


def parse_rule_document(text: str, name: str, kind: str, path: str = "") -> RuleDocument:
    """규칙 문서 텍스트를 절 목록으로 만든다. escape 정규화를 먼저 수행한다."""
    normalized = normalize_markdown(text)
    revision_match = REVISION_RE.search(normalized[:2000])
    document = RuleDocument(kind=kind, name=name, revision=f"Rev{revision_match.group(1)}" if revision_match else "", path=path)

    lines = normalized.splitlines()
    current: dict | None = None
    buffer: list[str] = []
    # 레벨 → 그 레벨 제목이 가진 태그. 하위 절이 태그를 못 얻으면 여기서 물려받는다.
    tag_stack: dict[int, tuple[tuple[str, ...], tuple[str, ...]]] = {}

    def flush() -> None:
        nonlocal tag_stack
        if current is None:
            return
        body = "\n".join(buffer).strip()
        level = current["level"]
        inherited = ((), ())
        for parent_level in sorted((key for key in tag_stack if key < level), reverse=True):
            if tag_stack[parent_level][0] or tag_stack[parent_level][1]:
                inherited = tag_stack[parent_level]
                break
        skills, gates = _tag_section(current["title"], body, inherited)
        tag_stack = {key: value for key, value in tag_stack.items() if key < level}
        tag_stack[level] = (skills, gates)
        # 본문이 없는 절은 하위 절을 묶는 컨테이너 제목이다. 태그는 위에서 물려주되
        # 프롬프트에 넣을 내용이 없으므로 절 목록에는 담지 않는다.
        if not body:
            return
        document.sections.append(
            RuleSection(
                document=name,
                number=current["number"],
                title=current["title"],
                level=level,
                body=body,
                skills=skills,
                gates=gates,
            )
        )

    for line in lines:
        heading = HEADING_RE.match(line)
        if heading:
            flush()
            buffer = []
            raw_title = heading.group(2)
            numbered = SECTION_NUMBER_RE.match(raw_title)
            current = {
                "level": len(heading.group(1)),
                "number": numbered.group(1) if numbered else "",
                "title": (numbered.group(2) if numbered else raw_title).strip(),
            }
            continue
        if current is not None:
            buffer.append(line)
    flush()
    return document


def load_rule_set(product: str, root: Path | None = None) -> RuleSet:
    """수집된 규칙 자산에서 이 제품의 RuleSet 을 만든다.

    원본 지식 폴더에 접근하지 않는다 — `data/product_knowledge/` 사본만 읽으므로 서버에서도
    동작한다. 수집된 규칙이 없으면 `available=False` 인 빈 RuleSet 을 준다 (예외를 올리지
    않는다 — 규칙이 없어도 나머지 분석은 계속 돌아야 한다).
    """
    rule_set = RuleSet(product=product)
    base = product_dir(product, root)
    for kind in (KIND_INSTRUCTION_PROMPT, KIND_QA_RULES):
        for asset in collected_assets(product, kind=kind, root=root):
            path = base / "original" / kind / asset["file_name"]
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            document = parse_rule_document(text, name=asset.get("base_name") or path.stem, kind=kind, path=str(path))
            if not document.revision:
                document.revision = asset.get("revision", "")
            rule_set.documents.append(document)
    return rule_set


@lru_cache(maxsize=8)
def _cached_rule_set(product: str, stamp: str) -> RuleSet:
    return load_rule_set(product)


def get_rule_set(product: str) -> RuleSet:
    """규칙을 캐시해 재사용한다. 수집 시각이 바뀌면 자동으로 다시 읽는다."""
    from app.core.product_knowledge import load_manifest

    stamp = load_manifest(product).get("synced_at", "")
    return _cached_rule_set(product, stamp)


def rule_coverage(rule_set: RuleSet) -> dict:
    """규칙이 Skill/Gate 별로 몇 개 절·몇 KB 로 잡혔는지. 태깅 누락을 눈으로 보기 위한 집계다."""
    by_skill = {skill: [] for skill in SKILL_TITLES}
    by_gate = {gate: [] for gate in GATE_TITLES}
    untagged: list[RuleSection] = []
    for section in rule_set.sections:
        for skill in section.skills:
            by_skill.setdefault(skill, []).append(section)
        for gate in section.gates:
            by_gate.setdefault(gate, []).append(section)
        if not section.skills and not section.gates:
            untagged.append(section)
    return {
        "product": rule_set.product,
        "revision": rule_set.revision,
        "documents": [{"name": document.name, "revision": document.revision, "sections": len(document.sections), "chars": document.size} for document in rule_set.documents],
        "total_sections": len(rule_set.sections),
        "total_chars": sum(section.size for section in rule_set.sections),
        "skills": {skill: {"sections": len(sections), "chars": sum(section.size for section in sections)} for skill, sections in by_skill.items()},
        "gates": {gate: {"sections": len(sections), "chars": sum(section.size for section in sections)} for gate, sections in by_gate.items()},
        "untagged": {"sections": len(untagged), "chars": sum(section.size for section in untagged)},
    }

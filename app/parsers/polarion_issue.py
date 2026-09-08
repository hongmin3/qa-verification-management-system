"""Polarion Issue Export 를 정규화한다 (로드맵 Layer 2 — Source 구조화).

입력은 별도 프로젝트(`alm-issue-export`)가 Polarion REST API 로 만든
`<ISSUE-ID>/backup.json` 이다. 그 프로젝트의 코드·설정은 읽지 않고 산출물만 읽는다
(`vxvue_spec_sync` 가 ALM 크롤러 output 만 읽는 것과 같은 방식).

**이 파서가 LLM 을 부르지 않는 이유.** 실제 Issue 는 다음 두 가지 덕에 결정적으로 구조화된다.

1. `reproductionStep` 본문이 QA 규칙 §29 의 표준 형식을 그대로 쓴다 —
   `[Title]` / `[Precondition]` / `[Step]` / `[Expected Result]` / `[Actual Result]` / `[Log, Data]`.
   실측 38건 중 29~31건이 이 마커를 갖고 있다.
2. `rndReviewResult`(연구소 검토 결과)가 규칙 §6 의 Issue 산출물 유형과 대응한다.
   `lab_fixed` → Program Fixed, `lab_duplicate` → Duplicate 처럼 코드로 분류된다.

그래서 Issue 분석(S01)에서 LLM 이 필요한 부분은 "유형 분류"나 "필드 추출"이 아니라
Trigger·Root Cause 의 의미 판단뿐이다. 규칙 §37 이 금지한 추정을 피하려면 추출과 판단을
이렇게 분리해 두는 편이 안전하다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

# --- 규칙 §6 Issue 산출물 유형 ------------------------------------------------

TYPE_PROGRAM_FIXED = "A_PROGRAM_FIXED"
TYPE_SPEC_NOT_BUG = "B_SPEC_NOT_BUG"
TYPE_DOCUMENT_FIX = "C_DOCUMENT_FIX"
TYPE_INQUIRY = "D_INQUIRY"
TYPE_DUPLICATE = "E_DUPLICATE"
TYPE_BLOCKED = "F_BLOCKED"
TYPE_CANNOT_REPRODUCE = "G_CANNOT_REPRODUCE"
TYPE_UNCLASSIFIED = "UNCLASSIFIED"

ISSUE_TYPE_LABELS = {
    TYPE_PROGRAM_FIXED: "A. Program Fixed",
    TYPE_SPEC_NOT_BUG: "B. Spec / Not Bug / As Designed",
    TYPE_DOCUMENT_FIX: "C. Spec Change / Document Fix",
    TYPE_INQUIRY: "D. Inquiry / 사양 문의",
    TYPE_DUPLICATE: "E. Duplicate / 동일 Root Cause",
    TYPE_BLOCKED: "F. Blocked",
    TYPE_CANNOT_REPRODUCE: "G. Cannot Reproduce / 환경·외부 모듈",
    TYPE_UNCLASSIFIED: "미분류 — QA 확인 필요",
}

# 연구소 검토 결과 코드 → 규칙 §6 유형. 값이 한 유형으로 확정되지 않는 코드는 후보를
# 여러 개 두고 QA 가 고르게 한다 — 규칙 §37 이 근거 없는 확정을 금지하기 때문이다.
LAB_REVIEW_TO_TYPE: dict[str, tuple[str, ...]] = {
    "lab_fixed": (TYPE_PROGRAM_FIXED,),
    "lab_inspec": (TYPE_SPEC_NOT_BUG,),
    "lab_nobug": (TYPE_SPEC_NOT_BUG,),
    "lab_duplicate": (TYPE_DUPLICATE,),
    "lab_pending": (TYPE_BLOCKED,),
    # 조치 없음. 사양대로(B)인지 재현 불가·환경(G)인지 코드만으로는 갈리지 않는다.
    "lab_noact": (TYPE_SPEC_NOT_BUG, TYPE_CANNOT_REPRODUCE),
}

# 규칙 §6: Spec / Not Bug / Document Fix / Inquiry 는 사용자가 요구하지 않으면 Runtime TC 를
# 자동 생성하지 않는다.
NO_AUTO_RUNTIME_TC_TYPES = frozenset({TYPE_SPEC_NOT_BUG, TYPE_DOCUMENT_FIX, TYPE_INQUIRY})

# --- 본문 구획 마커 (규칙 §29 표준 형식) --------------------------------------

SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "제목"),
    "precondition": ("precondition", "사전조건", "전제조건"),
    "steps": ("step", "steps", "재현절차", "절차"),
    "expected": ("expected result", "expected", "기대결과", "예상결과"),
    "actual": ("actual result", "actual", "현재결과", "실제결과"),
    "log_data": ("log, data", "log,data", "log", "data", "로그"),
}

MARKER_RE = re.compile(r"^\s*\[([^\]]{1,40})\]\s*(.*)$")
WORK_ITEM_ID_RE = re.compile(r"\b([A-Z]{2,5}-\d{2,6})\b")
DICOM_TAG_RE = re.compile(r"\(\s*[0-9A-Fa-f]{4}\s*,\s*[0-9A-Fa-f]{4}\s*\)")
COMMAND_RE = re.compile(r"(?:Command|CMD)\s*(?:No\.?|번호)?\s*[:=]?\s*(0[xX][0-9A-Fa-f]+|\d{1,5})")
QUOTED_RE = re.compile(r"[\"'“”‘’`]([^\"'“”‘’`\n]{2,40})[\"'“”‘’`]")
NUMBERED_LINE_RE = re.compile(r"^\s*(\d+)\s*[.)]\s*(.+)$")
VERSION_TAIL_RE = re.compile(r"([0-9]+(?:[._][0-9]+)+[0-9A-Za-z_]*)$")


class _TextExtractor(HTMLParser):
    """Polarion 본문 HTML 을 줄 단위 텍스트로 만든다.

    표준 라이브러리만 쓴다 — 이 한 가지 때문에 새 의존성을 추가하지 않는다. 이미지는
    버리지 않고 `[이미지: ...]` 로 남긴다. 규칙과 이 저장소의 기존 원칙(매뉴얼 개정 검증의
    이미지 Human Review Gate) 모두 "이미지는 사람이 본다"이므로 있었다는 사실이 남아야 한다.
    """

    BREAKING = {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "table", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "img":
            source = dict(attrs).get("src") or dict(attrs).get("alt") or ""
            # Polarion 본문은 첨부를 `workitemimg:1-shot.png` 같은 내부 참조로 적는다.
            # 스킴을 떼어 첨부 파일명과 대조 가능한 형태로 남긴다.
            name = source.split(":", 1)[-1].rsplit("/", 1)[-1] if source else "첨부"
            self.images.append(name)
            self.parts.append(f"\n[이미지: {name}]\n")
            return
        if tag in self.BREAKING:
            self.parts.append("\n")
        if tag == "td":
            self.parts.append("\t")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.BREAKING:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(value: str) -> tuple[str, list[str]]:
    """HTML 본문 → (텍스트, 이미지 파일명 목록)."""
    if not value:
        return "", []
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts).replace(" ", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    return "\n".join(line for line in lines if line), parser.images


def _field_text(raw: object) -> tuple[str, list[str]]:
    """Polarion 의 `{type, value}` 리치텍스트 필드를 평문으로 만든다."""
    if isinstance(raw, dict):
        value = str(raw.get("value") or "")
        if str(raw.get("type") or "").endswith("html"):
            return html_to_text(value)
        return value.replace(" ", " ").strip(), []
    if isinstance(raw, str):
        return raw.strip(), []
    return "", []


def _canonical_marker(label: str) -> str | None:
    normalized = re.sub(r"\s+", " ", label).strip().casefold()
    for canonical, aliases in SECTION_ALIASES.items():
        if normalized in aliases:
            return canonical
    return None


def split_marked_sections(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """`[Step]` 같은 마커로 구획된 본문을 구획별 줄 목록으로 나눈다.

    마커 밖에 있는 줄은 `preamble` 로 따로 돌려준다 — 버리면 Issue 재현 조건이 사라진다.
    """
    sections: dict[str, list[str]] = {}
    preamble: list[str] = []
    current: str | None = None
    for line in text.splitlines():
        match = MARKER_RE.match(line)
        if match:
            canonical = _canonical_marker(match.group(1))
            if canonical:
                current = canonical
                sections.setdefault(current, [])
                remainder = match.group(2).strip()
                if remainder:
                    sections[current].append(remainder)
                continue
            # 알려진 마커가 아니면 구획을 바꾸지 않고 내용으로 취급한다
            # (`[동물용]`, `[뷰어]` 처럼 본문 안의 꼬리표가 실제로 쓰인다).
        stripped = line.strip()
        if not stripped:
            continue
        (sections.setdefault(current, []) if current else preamble).append(stripped)
    return sections, preamble


def _as_steps(lines: list[str]) -> list[str]:
    """번호가 붙은 줄은 번호를 떼고, 안 붙은 줄은 그대로. Step–Expected 번호 대조에 쓴다."""
    steps: list[str] = []
    for line in lines:
        match = NUMBERED_LINE_RE.match(line)
        steps.append(match.group(2).strip() if match else line)
    return steps


@dataclass
class IssueComment:
    comment_id: str
    author: str
    created: str
    text: str


@dataclass
class IssueRecord:
    """정규화된 Issue 하나. Skill 입력의 단위다 (규칙 §46 Skill I/O Contract)."""

    issue_id: str
    project: str = ""
    title: str = ""
    title_en: str = ""
    status: str = ""
    severity: str = ""
    priority: str = ""
    occurrence_frequency: str = ""
    defect_score: str = ""
    product_stage: str = ""
    created: str = ""
    updated: str = ""

    description: str = ""
    precondition: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    expected: list[str] = field(default_factory=list)
    actual: list[str] = field(default_factory=list)
    log_data: list[str] = field(default_factory=list)
    unmarked_body: list[str] = field(default_factory=list)

    occurrence_cause: str = ""
    action_details: str = ""
    lab_review_result: str = ""
    issue_type: str = TYPE_UNCLASSIFIED
    issue_type_candidates: tuple[str, ...] = ()

    linked_srs: list[str] = field(default_factory=list)
    linked_issues: list[str] = field(default_factory=list)
    mentioned_work_items: list[str] = field(default_factory=list)
    occurred_versions: list[str] = field(default_factory=list)
    target_versions: list[str] = field(default_factory=list)

    comments: list[IssueComment] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    body_images: list[str] = field(default_factory=list)
    log_path: str = ""
    test_environment: str = ""

    search_terms: list[str] = field(default_factory=list)
    source_path: str = ""
    portal_url: str = ""

    @property
    def has_standard_body(self) -> bool:
        """규칙 §29 표준 형식으로 작성된 Issue 인지. G1 Source Completeness 가 쓴다."""
        return bool(self.steps) and bool(self.expected or self.actual)

    @property
    def type_label(self) -> str:
        return ISSUE_TYPE_LABELS.get(self.issue_type, self.issue_type)

    @property
    def needs_type_confirmation(self) -> bool:
        return self.issue_type == TYPE_UNCLASSIFIED or len(self.issue_type_candidates) > 1

    @property
    def blocks_auto_runtime_tc(self) -> bool:
        """규칙 §6: Spec/Not Bug/Document Fix/Inquiry 는 Runtime TC 를 자동 생성하지 않는다."""
        return self.issue_type in NO_AUTO_RUNTIME_TC_TYPES


def classify_issue_type(lab_review_result: str) -> tuple[str, tuple[str, ...]]:
    """연구소 검토 결과 → (확정 유형, 후보 목록). 후보가 여럿이면 확정하지 않는다."""
    candidates = LAB_REVIEW_TO_TYPE.get((lab_review_result or "").strip().casefold(), ())
    if len(candidates) == 1:
        return candidates[0], candidates
    return TYPE_UNCLASSIFIED, candidates


def _short_id(raw: object) -> str:
    """`VXvue/VP-6573` -> `VP-6573`, `VXvue/VXvue_1_1_0_001` -> `VXvue_1_1_0_001`."""
    value = str(raw or "")
    return value.rsplit("/", 1)[-1] if value else ""


def _version_ids(relationship: dict) -> list[str]:
    data = (relationship or {}).get("data") or []
    if isinstance(data, dict):
        data = [data]
    return [_short_id(entry.get("id")) for entry in data if isinstance(entry, dict) and entry.get("id")]


def build_search_terms(record: IssueRecord) -> list[str]:
    """1차 exact 검색에 쓸 키를 결정적으로 뽑는다 (규칙 §9.1 TC 조사 키).

    LLM 에 키워드 생성을 맡기지 않는다. Work Item ID·DICOM Tag·Command 번호·따옴표로 묶인
    UI 문구는 규칙이 정한 조사 키이고, 전부 정규식으로 뽑을 수 있다.
    """
    haystack = "\n".join(
        [record.title, record.title_en, record.description, record.occurrence_cause, record.action_details, *record.steps, *record.expected, *record.actual]
    )
    terms: list[str] = []
    terms.extend(record.linked_srs)
    terms.extend(record.linked_issues)
    terms.extend(match for match in WORK_ITEM_ID_RE.findall(haystack) if match != record.issue_id)
    terms.extend(DICOM_TAG_RE.findall(haystack))
    terms.extend(f"Command {match}" for match in COMMAND_RE.findall(haystack))
    terms.extend(match.strip() for match in QUOTED_RE.findall(haystack) if len(match.strip()) >= 2)
    seen: dict[str, None] = {}
    for term in terms:
        cleaned = term.strip()
        if cleaned and cleaned.casefold() not in {key.casefold() for key in seen}:
            seen[cleaned] = None
    return list(seen)


def parse_issue_backup(payload: dict, source_path: str = "") -> IssueRecord:
    """`backup.json` 한 건 → IssueRecord. 필드가 없어도 예외를 올리지 않는다.

    실측에서 필드 존재율이 고르지 않다(38건 중 `occurrenceCause` 32, `actionDetails` 29,
    `description` 1). 없는 필드는 빈 값으로 두고 무엇이 없는지는 Gate 가 보고한다.
    """
    workitem = payload.get("workitem") or {}
    attributes = workitem.get("attributes") or {}
    relationships = workitem.get("relationships") or {}
    links = workitem.get("links") or {}

    issue_id = str(attributes.get("id") or _short_id(workitem.get("id")))
    record = IssueRecord(
        issue_id=issue_id,
        project=str(workitem.get("id") or "").split("/", 1)[0],
        title=str(attributes.get("title") or ""),
        title_en=str(attributes.get("titleEng") or ""),
        status=str(attributes.get("status") or ""),
        severity=str(attributes.get("severity") or ""),
        priority=str(attributes.get("priority") or ""),
        occurrence_frequency=str(attributes.get("occurrenceFrequency") or ""),
        defect_score=str(attributes.get("defectScore") or ""),
        product_stage=str(attributes.get("productStage") or ""),
        created=str(attributes.get("created") or ""),
        updated=str(attributes.get("updated") or ""),
        lab_review_result=str(attributes.get("rndReviewResult") or ""),
        source_path=source_path,
        portal_url=str(links.get("portal") or ""),
    )

    record.description, description_images = _field_text(attributes.get("description"))
    body_text, body_images = _field_text(attributes.get("reproductionStep"))
    record.body_images = [*description_images, *body_images]

    sections, preamble = split_marked_sections(body_text)
    if sections.get("title") and not record.title:
        record.title = sections["title"][0]
    record.precondition = sections.get("precondition", [])
    record.steps = _as_steps(sections.get("steps", []))
    record.expected = sections.get("expected", [])
    record.actual = sections.get("actual", [])
    record.log_data = sections.get("log_data", [])
    record.unmarked_body = preamble
    # 마커가 전혀 없는 Issue 는 본문 전체를 재현 절차로 본다 — 내용을 잃지 않기 위함이다.
    if not sections and preamble:
        record.steps = _as_steps(preamble)

    record.occurrence_cause, _ = _field_text(attributes.get("occurrenceCause"))
    record.action_details, _ = _field_text(attributes.get("actionDetails"))
    record.log_path, _ = _field_text(attributes.get("logPath"))
    record.test_environment, _ = _field_text(attributes.get("testEnvironment"))

    record.issue_type, record.issue_type_candidates = classify_issue_type(record.lab_review_result)

    for link in payload.get("linkedWorkItems") or []:
        target = (link or {}).get("target") or {}
        target_id = _short_id(target.get("id"))
        if not target_id:
            continue
        target_type = str((target.get("attributes") or {}).get("type") or "")
        (record.linked_srs if target_type == "srs" else record.linked_issues).append(target_id)

    record.occurred_versions = _version_ids(relationships.get("occurredVersion"))
    record.target_versions = _version_ids(relationships.get("targetVersion"))

    for comment in payload.get("comments") or []:
        attributes_ = (comment or {}).get("attributes") or {}
        text, _ = _field_text(attributes_.get("text"))
        record.comments.append(
            IssueComment(
                comment_id=str(attributes_.get("id") or ""),
                author=_short_id(((comment.get("relationships") or {}).get("author") or {}).get("data", {}).get("id") if isinstance(comment, dict) else ""),
                created=str(attributes_.get("created") or ""),
                text=text,
            )
        )

    record.attachments = [
        str((attachment or {}).get("attributes", {}).get("fileName") or _short_id((attachment or {}).get("id")))
        for attachment in payload.get("attachments") or []
    ]

    record.mentioned_work_items = [
        match for match in dict.fromkeys(WORK_ITEM_ID_RE.findall("\n".join([record.description, body_text, record.occurrence_cause, record.action_details])))
        if match != record.issue_id
    ]
    record.search_terms = build_search_terms(record)
    return record


def load_issue(path: Path) -> IssueRecord:
    """`<ISSUE-ID>/backup.json` 파일 하나를 읽는다."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return parse_issue_backup(payload, source_path=str(path))


def list_exported_issues(export_dir: Path) -> list[str]:
    """Export 폴더에 있는 Issue ID 목록. 폴더가 없으면 빈 목록 (오류 아님)."""
    if not export_dir.is_dir():
        return []
    return sorted(child.name for child in export_dir.iterdir() if child.is_dir() and (child / "backup.json").is_file())


def load_exported_issue(export_dir: Path, issue_id: str) -> IssueRecord | None:
    path = export_dir / issue_id / "backup.json"
    return load_issue(path) if path.is_file() else None


def normalize_version_label(version_id: str) -> str:
    """`VXvue_1_1_0_001` -> `1.1.0.001`. 사양서·TC 의 버전 표기와 맞추기 위한 변환이다."""
    match = VERSION_TAIL_RE.search(version_id or "")
    return match.group(1).replace("_", ".") if match else (version_id or "")

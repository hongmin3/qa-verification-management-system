"""분석 입력 만들기: 후보 압축 (SPEC REQ-QAINTEL-011~016).

AI 에 무엇을 보낼지는 여기서 코드로 정한다. 이슈 전체·TC 전체·사양 전체를 넘기지 않고, 기존
Exact → BM25 단계 검색(`app/retrieval/hybrid.py`)으로 후보를 골라 "왜 걸렸는지"와 함께 넘긴다.
여기서 만든 `KnownIds` 가 결과 검증(`evidence_validation.py`)의 기준이 된다 — 입력으로 준 것과 실제
스냅샷에 있는 것만 결과에 남는다.

분석 엔진은 제품을 모른다. 제품 차이는 `ProductProfile`(Regression 축, Checklist 파일 이름)로만 들어온다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.modules.daily_qa.change_events import (
    COMMENT,
    FIXED_ISSUE,
    NEW_ISSUE,
    SPEC_COVERAGE,
    SPEC_DECISION,
)
from app.modules.daily_qa.packages import Task, _chunks
from app.modules.daily_qa.product_adapter import RD_FIXED, RD_SPEC_LIKE, ProductProfile
from app.modules.daily_qa.tc_index import TcRow
from app.parsers.polarion_issue import COMMAND_RE, DICOM_TAG_RE, QUOTED_RE, WORK_ITEM_ID_RE, split_marked_sections
from app.retrieval.hybrid import HybridRetriever, RetrievalResult

#: 분석 종류 → Skill. 제품 이름이 들어가지 않는 공통 Skill 이다 (NFR-QAINTEL-002).
SKILL_NEW_ISSUE = "qa-new-issue-analysis"
SKILL_FIXED_ISSUE = "qa-fixed-issue-analysis"
SKILL_SPEC_DECISION = "qa-spec-decision-analysis"
SKILL_COMMENT = "qa-comment-analysis"
SKILL_SPEC_COVERAGE = "qa-spec-coverage-analysis"
ANALYSIS_SKILLS = {
    NEW_ISSUE: SKILL_NEW_ISSUE,
    FIXED_ISSUE: SKILL_FIXED_ISSUE,
    SPEC_DECISION: SKILL_SPEC_DECISION,
    COMMENT: SKILL_COMMENT,
    SPEC_COVERAGE: SKILL_SPEC_COVERAGE,
}
TASK_PREFIX = {NEW_ISSUE: "NEW", FIXED_ISSUE: "FIX", SPEC_DECISION: "SPC", COMMENT: "CMT", SPEC_COVERAGE: "COV"}

#: 공통 Regression 축. 제품 설정이 비어 있으면 이것을 쓴다 (GENERATOR 같은 장비 축은 제품이 더한다).
DEFAULT_AXES = ("DIRECT", "STATE", "DATA", "PERSISTENCE", "INTEGRATION", "PERMISSION", "PRIOR_ISSUE")

ERROR_CODE_RE = re.compile(r"\b(?:0x[0-9A-Fa-f]{2,8}|E\d{3,6}|ERR[-_]?\d{2,6})\b")
LEGACY_RE = re.compile(r"\b\d{2}-\d{1,3}(?:-\d{1,3})+\b")
TEXT_LIMIT = 3000
EXCERPT = 600


def _cut(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + " …(줄임)"


def extract_terms(*texts: str, extra: tuple[str, ...] = (), exclude: str = "") -> list[str]:
    """Exact 검색어를 정규식으로 뽑는다. LLM 에 검색어를 만들게 하지 않는다 (규칙 §9.1)."""
    haystack = "\n".join(text for text in texts if text)
    terms: list[str] = list(extra)
    terms += [match for match in WORK_ITEM_ID_RE.findall(haystack) if match != exclude]
    terms += LEGACY_RE.findall(haystack)
    terms += DICOM_TAG_RE.findall(haystack)
    terms += [f"Command {match}" for match in COMMAND_RE.findall(haystack)]
    terms += ERROR_CODE_RE.findall(haystack)
    terms += [match.strip() for match in QUOTED_RE.findall(haystack) if len(match.strip()) >= 2]
    seen: dict[str, None] = {}
    lowered: set[str] = set()
    for term in terms:
        cleaned = (term or "").strip()
        if cleaned and cleaned != exclude and cleaned.casefold() not in lowered:
            seen[cleaned] = None
            lowered.add(cleaned.casefold())
    return list(seen)


def issue_sections(issue: dict) -> dict:
    """재현 절차의 `[Step]`·`[Expected Result]` 같은 마커를 구획으로 나눈다 (기존 파서 재사용)."""
    sections, preamble = split_marked_sections(issue.get("reproduction_step") or "")
    return {
        "precondition": sections.get("precondition", []),
        "steps": sections.get("steps", []) or preamble,
        "expected": sections.get("expected", []),
        "actual": sections.get("actual", []),
        "log_data": sections.get("log_data", []),
    }


def issue_text(issue: dict) -> str:
    comments = " ".join(comment.get("text", "") for comment in issue.get("comments") or [])
    return "\n".join([issue.get("title", ""), issue.get("description", ""), issue.get("reproduction_step", ""),
                      issue.get("occurrence_cause", ""), issue.get("action_details", ""), comments])


def issue_view(issue: dict, comments: list[dict] | None = None) -> dict:
    """AI 에 넘기는 이슈 모양. 판정에 쓰는 필드만, 길이를 자른다."""
    view = {
        "id": issue["id"],
        "title": issue.get("title", ""),
        "status": issue.get("status", ""),
        "severity": issue.get("severity", ""),
        "rd_result": issue.get("rd_result", ""),
        "rd_result_raw": issue.get("rd_result_raw", ""),
        "created": issue.get("created", ""),
        "updated": issue.get("updated", ""),
        "description": _cut(issue.get("description", ""), TEXT_LIMIT),
        "reproduction": issue_sections(issue),
        "occurrence_cause": _cut(issue.get("occurrence_cause", ""), TEXT_LIMIT),
        "action_details": _cut(issue.get("action_details", ""), TEXT_LIMIT),
        "occurred_versions": issue.get("occurred_versions") or [],
        "target_versions": issue.get("target_versions") or [],
        "linked_ids": issue.get("linked_ids") or [],
    }
    if comments is not None:
        view["comments"] = comments
    return view


def issue_brief(issue: dict, reason: str, relation_hint: str = "") -> dict:
    """후보 이슈 요약. 과거 처리 결과(연구소 결과)를 함께 넘긴다."""
    return {
        "id": issue["id"],
        "title": issue.get("title", ""),
        "status": issue.get("status", ""),
        "rd_result": issue.get("rd_result", ""),
        "rd_result_raw": issue.get("rd_result_raw", ""),
        "occurred_versions": issue.get("occurred_versions") or [],
        "target_versions": issue.get("target_versions") or [],
        "reproduction_excerpt": _cut(issue.get("reproduction_step") or issue.get("description", ""), EXCERPT),
        "occurrence_cause": _cut(issue.get("occurrence_cause", ""), 300),
        "action_details": _cut(issue.get("action_details", ""), 300),
        "updated": issue.get("updated", ""),
        "match": reason,
        **({"relation_hint": relation_hint} if relation_hint else {}),
    }


@dataclass
class KnownIds:
    """결과 검증 기준 (REQ-QAINTEL-017)."""

    issue_ids: set[str] = field(default_factory=set)
    srs_ids: set[str] = field(default_factory=set)
    tc_ids: set[str] = field(default_factory=set)
    tc_locations: set[str] = field(default_factory=set)
    spec_refs: set[str] = field(default_factory=set)
    comment_ids: set[str] = field(default_factory=set)
    #: 이슈 번호 → 오늘 연구소 결과 (초안 규칙에 쓴다)
    issue_rd: dict[str, str] = field(default_factory=dict)


@dataclass
class Corpus:
    """한 실행에서 검색할 자료. 검색기는 처음 쓸 때 한 번만 만든다."""

    profile: ProductProfile
    issues: list[dict]
    srs: list[dict]
    tc_rows: list[TcRow]
    spec_chunks: list = field(default_factory=list)
    chunk_labels: dict[str, str] = field(default_factory=dict)
    manuals: dict[str, str] = field(default_factory=dict)
    unreadable_documents: list[str] = field(default_factory=list)
    _cache: dict = field(default_factory=dict)

    @property
    def issue_by_id(self) -> dict[str, dict]:
        if "issue_by_id" not in self._cache:
            self._cache["issue_by_id"] = {item["id"]: item for item in self.issues}
        return self._cache["issue_by_id"]

    @property
    def srs_by_id(self) -> dict[str, dict]:
        if "srs_by_id" not in self._cache:
            self._cache["srs_by_id"] = {item["id"]: item for item in self.srs}
        return self._cache["srs_by_id"]

    def _retriever(self, name: str) -> HybridRetriever:
        if name not in self._cache:
            if name == "issues":
                self._cache[name] = HybridRetriever(self.issues, text_getter=issue_text, key_getter=lambda item: item["id"])
            elif name == "srs":
                self._cache[name] = HybridRetriever(
                    self.srs, text_getter=lambda item: f"{item['id']} {item.get('old_id', '')} {item.get('title', '')}\n{item.get('text', '')}",
                    key_getter=lambda item: item["id"])
            elif name == "chunks":
                self._cache[name] = HybridRetriever(self.spec_chunks, text_getter=lambda chunk: f"{chunk.heading}\n{chunk.text}",
                                                    key_getter=lambda chunk: chunk.chunk_id)
            elif name == "tcs":
                self._cache[name] = HybridRetriever(
                    self.tc_rows,
                    text_getter=lambda row: " ".join([row.tc_id, " ".join(row.srs_refs), row.title, row.precondition, row.step, row.expected]),
                    key_getter=lambda row: row.location())
            elif name == "manuals":
                paragraphs = [(file, text) for file, body in self.manuals.items() for text in _paragraphs(body)]
                self._cache[name] = HybridRetriever(paragraphs, text_getter=lambda pair: pair[1], key_getter=lambda pair: f"{pair[0]}#{hash(pair[1])}")
        return self._cache[name]

    def known(self) -> KnownIds:
        known = KnownIds()
        known.issue_ids = set(self.issue_by_id)
        known.issue_rd = {item["id"]: item.get("rd_result", "") for item in self.issues}
        known.srs_ids = set(self.srs_by_id) | {item.get("old_id") for item in self.srs if item.get("old_id")}
        known.tc_ids = {row.tc_id for row in self.tc_rows if row.tc_id}
        known.tc_locations = {row.location() for row in self.tc_rows}
        return known

    def is_checklist(self, row: TcRow) -> bool:
        marker = (self.profile.checklist or {}).get("template_name_contains") or ""
        return bool(marker) and marker in row.workbook

    # -- 후보 --------------------------------------------------------------
    def similar_issues(self, issue: dict, limit: int) -> list[dict]:
        """비슷한 과거 이슈. 같은 발생·목표 버전 후보를 앞에 둔다 (REQ-QAINTEL-011 4번)."""
        terms = extract_terms(issue.get("title", ""), issue.get("description", ""), issue.get("reproduction_step", ""),
                              extra=tuple(issue.get("linked_ids") or ()), exclude=issue["id"])
        sections = issue_sections(issue)
        query = " ".join([issue.get("title", ""), *sections["steps"], *sections["expected"], *sections["actual"],
                          issue.get("description", "")[:500]])
        hits = self._retriever("issues").search(terms=terms, query=query, top_k=limit + 1, adaptive=False)
        versions = set(issue.get("occurred_versions") or []) | set(issue.get("target_versions") or [])
        picked = [(entry.item, entry.reason) for entry in hits.items if entry.item["id"] != issue["id"]][:limit]
        same = [pair for pair in picked if versions & (set(pair[0].get("occurred_versions") or []) | set(pair[0].get("target_versions") or []))]
        other = [pair for pair in picked if pair not in same]
        return [issue_brief(item, reason + (" · 같은 버전" if (item, reason) in same else "")) for item, reason in (*same, *other)]

    def spec_candidates(self, terms: list[str], query: str, srs_limit: int, doc_limit: int, known: KnownIds) -> dict:
        srs_hits = self._retriever("srs").search(terms=terms, query=query, top_k=srs_limit, adaptive=True)
        srs = [
            {"ref": f"srs:{entry.item['id']}", "id": entry.item["id"], "old_id": entry.item.get("old_id", ""),
             "title": entry.item.get("title", ""), "status": entry.item.get("status", ""),
             "text": _cut(entry.item.get("text", ""), 1500), "match": entry.reason}
            for entry in srs_hits.items
        ]
        docs = []
        if self.spec_chunks and doc_limit:
            chunk_hits = self._retriever("chunks").search(terms=terms, query=query, top_k=doc_limit, adaptive=True)
            for entry in chunk_hits.items:
                chunk = entry.item
                label = self.chunk_labels.get(chunk.document_id, chunk.document_id)
                ref = f"spec:{chunk.chunk_id}"
                known.spec_refs.add(ref)
                docs.append({"ref": ref, "location": f"{label} · p.{chunk.page} · {chunk.heading}".strip(" ·"),
                             "text": _cut(chunk.text, 1200), "match": entry.reason})
        return {"srs": srs, "spec_docs": docs, "search": {"terms": terms[:20], "srs_hits": srs_hits.summary()}}

    def tc_candidates(self, terms: list[str], query: str, limit: int, srs_ids: tuple[str, ...] = (),
                      related_issue_ids: tuple[str, ...] = ()) -> list[dict]:
        """TC 후보. 연결 번호 → Legacy 번호 → 관련 이슈 번호 → BM25 순서다 (REQ-QAINTEL-016 입력)."""
        tiers: list[tuple[str, list[TcRow]]] = []
        wanted = [value for value in srs_ids if value]
        tiers.append(("SRS 번호로 연결", [row for row in self.tc_rows if any(ref in row.srs_refs for ref in wanted[:1])]))
        tiers.append(("Legacy 번호로 연결", [row for row in self.tc_rows if any(ref in row.srs_refs for ref in wanted[1:])]))
        if related_issue_ids:
            pattern = re.compile(r"(?<![0-9A-Za-z-])(?:" + "|".join(re.escape(value) for value in related_issue_ids) + r")(?![0-9A-Za-z-])")
            tiers.append(("관련 이슈 번호가 적힘", [row for row in self.tc_rows
                                              if pattern.search(" ".join([row.tc_id, row.title, row.step, row.expected, row.precondition]))]))
        hits: RetrievalResult = self._retriever("tcs").search(terms=terms, query=query, top_k=limit, adaptive=False)
        tiers.append(("같은 기능(검색)", [entry.item for entry in hits.items]))
        reasons = {entry.item.location(): entry.reason for entry in hits.items}
        picked: list[dict] = []
        seen: set[str] = set()
        for label, rows in tiers:
            # 같은 순위 안에서는 영향성평가 Checklist 행을 앞에 둔다.
            for row in sorted(rows, key=lambda item: not self.is_checklist(item)):
                if row.location() in seen or len(picked) >= limit:
                    continue
                seen.add(row.location())
                data = row.as_dict()
                data.update({"match": label if label != "같은 기능(검색)" else f"{label} {reasons.get(row.location(), '')}".strip(),
                             "is_checklist": self.is_checklist(row)})
                for key in ("step", "expected", "precondition"):
                    data[key] = _cut(data.get(key, ""), 1200)
                picked.append(data)
        return picked

    def related_issues_for_srs(self, srs: dict, query: str, limit: int, recent_days: int) -> list[dict]:
        """SRS 변경과 관련된 과거 이슈 (REQ-QAINTEL-016 입력). 정확 일치 → BM25."""
        ids = [value for value in (srs.get("id"), srs.get("old_id")) if value]
        exact_pattern = re.compile(r"(?<![0-9A-Za-z-])(?:" + "|".join(re.escape(value) for value in ids) + r")(?![0-9A-Za-z-])") if ids else None
        picked: list[tuple[dict, str]] = []
        seen: set[str] = set()
        for issue in self.issues:
            if exact_pattern is None:
                break
            if srs.get("id") in (issue.get("linked_ids") or []):
                picked.append((issue, "연결 항목"))
                seen.add(issue["id"])
            elif exact_pattern.search(issue_text(issue)):
                picked.append((issue, f"'{ids[0]}' 정확 일치"))
                seen.add(issue["id"])
        if len(picked) < limit and query.strip():
            hits = self._retriever("issues").search(terms=[], query=query, top_k=limit, adaptive=False)
            for entry in hits.items:
                if entry.item["id"] not in seen and len(picked) < limit:
                    picked.append((entry.item, entry.reason))
                    seen.add(entry.item["id"])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=recent_days)).isoformat()
        result = []
        for issue, reason in picked[:limit]:
            rd = issue.get("rd_result", "")
            hint = "과거 FIXED" if rd == RD_FIXED else "과거 SPEC" if rd in RD_SPEC_LIKE else "기존 결함"
            if rd == RD_FIXED and str(issue.get("updated") or "") >= cutoff:
                hint += f" · 최근 {recent_days}일 안의 FIXED"
            result.append(issue_brief(issue, reason, hint))
        return result

    def manual_candidates(self, query: str, limit: int) -> list[dict]:
        if not self.manuals or not query.strip() or not limit:
            return []
        hits = self._retriever("manuals").search(terms=[], query=query, top_k=limit, adaptive=False)
        return [{"file": entry.item[0], "text": _cut(entry.item[1], 800), "match": entry.reason} for entry in hits.items]


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text or "") if len(part.strip()) >= 20]


# -- 작업 묶음 -------------------------------------------------------------------


@dataclass
class AnalysisTarget:
    analysis_type: str
    entity_id: str
    event_ids: list[int]
    events: list[dict]


def build_item(target: AnalysisTarget, corpus: Corpus, known: KnownIds, cfg, comments_loader=None) -> dict | None:
    """분석 대상 하나의 입력. 대상이 오늘 스냅샷에 없으면 None (이벤트는 포기로 바뀐다)."""
    intel = cfg.intelligence
    kind = target.analysis_type
    base = {"target": target.entity_id, "event_ids": target.event_ids,
            "events": [{"id": event.get("id"), "event_type": event["event_type"], "changed_fields": event.get("changed_fields") or [],
                        "before": event.get("before") or {}, "after": event.get("after") or {}} for event in target.events]}
    if kind == SPEC_COVERAGE:
        srs = corpus.srs_by_id.get(target.entity_id)
        if srs is None:
            return None
        after = {}
        for event in target.events:
            after.update(event.get("after") or {})
        changed_text = " ".join(after.get("added_sentences") or []) or srs.get("text", "")[:800]
        query = f"{srs.get('title', '')} {changed_text}"
        terms = extract_terms(srs.get("title", ""), changed_text, extra=tuple(value for value in (srs["id"], srs.get("old_id")) if value))
        related = corpus.related_issues_for_srs(srs, query, intel.issue_candidates, intel.recent_fixed_days)
        tcs = corpus.tc_candidates(terms, query, cfg.tc_candidate_limit, srs_ids=(srs["id"], srs.get("old_id", "")),
                                   related_issue_ids=tuple(item["id"] for item in related))
        spec = corpus.spec_candidates(terms, query, intel.spec_candidates, intel.spec_doc_candidates, known)
        return {**base, "srs": {"id": srs["id"], "old_id": srs.get("old_id", ""), "title": srs.get("title", ""),
                                "status": srs.get("status", ""), "text": _cut(srs.get("text", ""), TEXT_LIMIT)},
                "change": {"events": [event["event_type"] for event in target.events], "after": after,
                           "before": {key: value for event in target.events for key, value in (event.get("before") or {}).items()}},
                "candidates": {"issues": related, "tcs": tcs, "srs": spec["srs"], "spec_docs": spec["spec_docs"],
                               "manuals": corpus.manual_candidates(query, intel.manual_candidates)},
                "search": spec["search"]}

    issue = corpus.issue_by_id.get(target.entity_id)
    if issue is None:
        return None
    comments = None
    if kind in (FIXED_ISSUE, SPEC_DECISION) and comments_loader is not None:
        comments = comments_loader(issue["id"])
    if kind == COMMENT:
        fresh = [comment for event in target.events for comment in (event.get("after") or {}).get("comments") or []
                 if comment.get("id") in set((event.get("after") or {}).get("significant_ids") or [])]
        known.comment_ids.update(comment["id"] for comment in fresh if comment.get("id"))
        earlier = [comment for comment in issue.get("comments") or [] if comment.get("id") not in {item.get("id") for item in fresh}]
        return {**base, "issue": issue_view(issue), "new_comments": fresh, "earlier_comments": earlier[-5:]}
    if comments:
        known.comment_ids.update(comment["id"] for comment in comments if comment.get("id"))
    sections = issue_sections(issue)
    terms = extract_terms(issue.get("title", ""), issue.get("description", ""), issue.get("reproduction_step", ""),
                          issue.get("occurrence_cause", ""), issue.get("action_details", ""),
                          extra=tuple(issue.get("linked_ids") or ()), exclude=issue["id"])
    query = " ".join([issue.get("title", ""), *sections["steps"], *sections["expected"], *sections["actual"],
                      issue.get("occurrence_cause", ""), issue.get("action_details", "")])
    spec = corpus.spec_candidates(terms, query, intel.spec_candidates, intel.spec_doc_candidates, known)
    item = {**base, "issue": issue_view(issue, comments),
            "candidates": {"issues": corpus.similar_issues(issue, intel.issue_candidates), "srs": spec["srs"],
                           "spec_docs": spec["spec_docs"]},
            "search": spec["search"]}
    if kind == FIXED_ISSUE:
        linked = tuple(value for value in issue.get("linked_ids") or () if value in corpus.srs_by_id)
        item["candidates"]["tcs"] = corpus.tc_candidates(terms, query, cfg.tc_candidate_limit, srs_ids=linked[:1] + tuple(
            corpus.srs_by_id[value].get("old_id", "") for value in linked[:1]))
        item["regression_axes"] = list(cfg.product_profile.regression_axes or DEFAULT_AXES)
    elif kind == NEW_ISSUE:
        item["candidates"]["tcs"] = corpus.tc_candidates(terms, query, min(cfg.tc_candidate_limit, 8))
    return item


def build_tasks(kind: str, items: list[dict], batch_size: int, answers: list[dict] | None = None,
                unreadable: list[str] | None = None) -> list[Task]:
    skill = ANALYSIS_SKILLS[kind]
    prefix = TASK_PREFIX[kind]
    return [
        Task(task_id=f"{prefix}-{number:03d}", skill=skill,
             payload={"analysis_type": kind, "items": chunk, "answered_questions": answers or [],
                      "unreadable_documents": unreadable or []})
        for number, chunk in enumerate(_chunks(items, batch_size), start=1)
    ]

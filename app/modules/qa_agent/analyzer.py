"""QA Agent 파이프라인 (규칙 §54 가 정한 10단계 순서).

    Source 수집 → Source 구조화 → Skill Routing → Gate → Evidence 저장
    → Skill 결과 생성 → Cross-check → Human Review → QA 승인 → 최종 반영

이 파일이 1~7 단계를 담당한다. 8~10 은 화면과 QA 의 몫이다 (규칙 §55 가 자동 확정을 금지).

**LLM 호출은 정확히 1회, 그리고 Gate 가 막으면 0회다.**

    1 Issue 구조화        코드   (polarion_issue)
    2 지식 문서 로드       코드   (knowledge_documents, 파싱 캐시 재사용)
    3 규칙 로드           코드   (qa_rules)
    4 근거 검색           코드   (exact → BM25)
    5 Gate G1·G2·G4      코드   ← 여기서 막히면 API 호출 없이 종료
    6 의미 판단           LLM    (1회 구조화 호출)
    7 ID 교차검증          코드
    8 Gate G3·G5         코드
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.document_schemas import RevisionMark, SpecificationChunk, TestCase
from app.core.knowledge_documents import load_for_product
from app.core.logger import configure_logging
from app.core.model_router import estimate_from_usage, looks_like_integration, route_qa_analysis
from app.core.product_knowledge import resolve_config
from app.core.qa_rules import RuleSet, load_rule_set
from app.core.storage import Storage
from app.modules.qa_agent import gates
from app.modules.qa_agent.ai_client import QaAgentAIClient
from app.modules.qa_agent.evidence import (
    Claim,
    EvidenceStore,
    issue_evidence,
    specification_evidence,
    testcase_evidence,
)
from app.modules.qa_agent.schemas import (
    AXIS_LABELS,
    TC_JUDGMENT_LABELS,
    TC_JUDGMENTS_REQUIRING_APPROVAL,
    QaAgentDecision,
)
from app.modules.qa_agent.validation import ValidationReport, validate_decision
from app.parsers.polarion_issue import IssueRecord, load_exported_issue, load_issue
from app.retrieval.hybrid import HybridRetriever, RetrievalResult

ANALYSIS_STAGES = (
    "Issue 구조화",
    "지식 문서 로드",
    "QA 규칙 로드",
    "근거 검색 (Exact → BM25)",
    "Gate 판정 (G1·G2·G4)",
    "AI 의미 판단",
    "ID 교차검증",
    "Gate 판정 (G3·G5)",
)

MODULE_NAME = "qa_agent"

DEPRECATED_MARKS = frozenset({RevisionMark.STRIKETHROUGH_DETECTED})
DEPRECATED_WORDS = ("삭제", "취소", "폐지", "deprecated", "removed", "obsolete")


@dataclass
class QaAgentResult:
    analysis_id: str
    product: str
    issue_id: str
    created_at: str = ""
    status: str = ""

    issue: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)
    proceeded_to_ai: bool = False
    draft_only: bool = False
    blocking_reasons: list[str] = field(default_factory=list)

    evidence: dict = field(default_factory=dict)
    decision: dict = field(default_factory=dict)
    validation: dict = field(default_factory=dict)
    retrieval: dict = field(default_factory=dict)

    knowledge_documents: list[dict] = field(default_factory=list)
    rule_revisions: dict = field(default_factory=dict)
    token_usage: dict = field(default_factory=dict)
    request_count: int = 0
    cache_hit: bool = False
    prompt_version: int = 0
    ai_audit: dict = field(default_factory=dict)
    routing: dict = field(default_factory=dict)
    cost_estimate: dict = field(default_factory=dict)
    masking: dict = field(default_factory=dict)
    knowledge_failures: list[dict] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    scope_note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _looks_deprecated(chunk: SpecificationChunk) -> bool:
    """취소선 표시가 있거나 본문에 삭제·폐지 표현이 있는 Chunk (규칙 §11)."""
    if any(mark in DEPRECATED_MARKS for mark in chunk.revision_marks):
        return True
    lowered = chunk.text.casefold()
    return any(word in lowered for word in DEPRECATED_WORDS)


def _chunk_search_text(chunk: SpecificationChunk) -> str:
    return f"{chunk.heading}\n{chunk.text}"


class QaAgentAnalyzer:
    def __init__(self, ai_client: QaAgentAIClient | None = None, storage: Storage | None = None) -> None:
        self.settings = get_settings()
        self.ai_client = ai_client or QaAgentAIClient()
        self.storage = storage or Storage()
        self.logger = configure_logging()

    # --- Issue 입력 ----------------------------------------------------------

    def load_issue_for_product(self, product: str, issue_id: str) -> IssueRecord | None:
        """제품 설정의 Export 폴더에서 Issue 를 읽는다 (이 호스트에 폴더가 있을 때만)."""
        config = resolve_config(product)
        if config is None or not config.issue_source.export_dir:
            return None
        return load_exported_issue(Path(config.issue_source.export_dir), issue_id)

    def available_issue_ids(self, product: str) -> list[str]:
        from app.parsers.polarion_issue import list_exported_issues

        config = resolve_config(product)
        if config is None or not config.issue_source.export_dir:
            return []
        return list_exported_issues(Path(config.issue_source.export_dir))

    # --- 파이프라인 ----------------------------------------------------------

    def run(
        self,
        product: str,
        issue: IssueRecord | None = None,
        issue_id: str = "",
        issue_path: Path | None = None,
        context: gates.ExecutionContext | None = None,
        analysis_id: str | None = None,
        scope_note: str = "",
    ) -> QaAgentResult:
        started = time.monotonic()
        analysis_id = analysis_id or uuid.uuid4().hex[:12]
        context = context or gates.ExecutionContext()

        def stage(index: int) -> None:
            self.storage.update_stage(analysis_id, index, ANALYSIS_STAGES[index - 1], len(ANALYSIS_STAGES))

        try:
            stage(1)  # Issue 구조화
            if issue is None:
                issue = load_issue(issue_path) if issue_path else self.load_issue_for_product(product, issue_id)
            if issue is None:
                raise ValueError(f"Issue 를 찾을 수 없습니다: {issue_id or issue_path}")

            result = QaAgentResult(
                analysis_id=analysis_id,
                product=product,
                issue_id=issue.issue_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                scope_note=scope_note,
            )
            result.issue = self._issue_summary(issue)

            stage(2)  # 지식 문서 로드
            knowledge = load_for_product(product, storage=self.storage, require_both=False)
            result.knowledge_documents = knowledge.knowledge_documents()
            # 읽지 못한 문서는 조용히 빠지면 "사양 없음" 오판이 된다.
            result.knowledge_failures = knowledge.failures

            stage(3)  # QA 규칙 로드
            rule_set = load_rule_set(product)
            result.rule_revisions = rule_set.revisions()

            stage(4)  # 근거 검색
            spec_hits, tc_hits = self._retrieve(issue, knowledge.chunks, knowledge.cases)
            result.retrieval = {"specification": spec_hits.summary(), "testcase": tc_hits.summary()}

            stage(5)  # Gate 판정 (LLM 호출 전)
            spec_chunks = spec_hits.values
            deprecated_hits = sum(_looks_deprecated(chunk) for chunk in spec_chunks)
            preflight = gates.preflight(
                issue,
                context,
                specification_count=len(knowledge.specification_documents),
                testcase_count=len(knowledge.testcase_documents),
                rules_available=rule_set.available,
                evidence_count=len(spec_chunks),
                exact_evidence_count=spec_hits.exact_count,
                deprecated_hits=deprecated_hits,
                searched_document_count=len({chunk.document_id for chunk in spec_chunks}),
                total_document_count=len(knowledge.specification_documents),
            )
            result.gates = preflight.report.as_dict()
            result.draft_only = preflight.draft_only
            result.blocking_reasons = preflight.reasons

            if not preflight.proceed:
                # 여기서 끝난다. API 호출이 0회이고, 왜 멈췄는지가 결과에 남는다 (규칙 §55).
                result.status = "GATE_BLOCKED"
                result.elapsed_seconds = round(time.monotonic() - started, 3)
                self.logger.info(
                    "qa_agent_gate_blocked id=%s issue=%s reasons=%s llm_calls=0", analysis_id, issue.issue_id, len(preflight.reasons)
                )
                return result

            stage(6)  # AI 의미 판단 (1회)
            # 모델 등급은 입력에서 세어지는 신호로만 고른다 — 난이도 판정에 또 LLM 을 부르지 않는다.
            routing = route_qa_analysis(
                linked_srs_count=len(issue.linked_srs),
                exact_evidence_count=spec_hits.exact_count,
                evidence_document_count=len({chunk.document_id for chunk in spec_chunks}),
                candidate_tc_count=len(tc_hits.values),
                root_cause_known=bool(issue.occurrence_cause or issue.action_details),
                has_integration_terms=looks_like_integration(" ".join([issue.title, *issue.steps, *issue.search_terms])),
                force_tier=str(self.settings.get("qa_agent.force_model_tier", "") or ""),
            )
            result.routing = routing.as_dict()
            decision = self.ai_client.analyze(
                issue,
                spec_chunks,
                tc_hits.values,
                rule_set,
                context,
                scope_note=scope_note,
                rule_char_budget=int(self.settings.get("qa_agent.rule_char_budget", 5000)),
                routing=routing,
            )
            result.proceeded_to_ai = True

            stage(7)  # ID 교차검증
            validated, validation = validate_decision(
                decision,
                known_chunk_ids={chunk.chunk_id for chunk in spec_chunks},
                known_tc_ids={case.tc_id for case in tc_hits.values},
                issue_steps=issue.steps,
                issue_expected=issue.expected,
            )
            result.decision = validated.model_dump()
            result.validation = validation.as_dict()

            stage(8)  # Gate 판정 (LLM 호출 후)
            store = self._build_evidence(issue, validated, spec_hits, tc_hits)
            result.evidence = store.as_dict()
            claims = [claim.for_cross_check() for claim in store.claims]
            covering, link_only = self._coverage_split(validated)
            report = preflight.report
            report.set(gates.evaluate_g3(issue, candidate_count=len(tc_hits.values), covering_tc_ids=covering, link_only_tc_ids=link_only))
            report.set(gates.evaluate_g5(issue, claims))
            result.gates = report.as_dict()

            result.token_usage = dict(self.ai_client.token_usage)
            result.request_count = self.ai_client.request_count
            result.cache_hit = self.ai_client.cache_hit
            result.prompt_version = self.ai_client.prompt_version
            result.ai_audit = self.ai_client.audit_snapshot
            result.cost_estimate = estimate_from_usage(routing.model, result.token_usage)
            result.masking = (result.ai_audit or {}).get("masking") or {}
            result.status = "DONE"
            result.elapsed_seconds = round(time.monotonic() - started, 3)
            self.logger.info(
                "qa_agent_finished id=%s issue=%s llm_calls=%s cache_hit=%s tokens=%s elapsed=%.3f",
                analysis_id,
                issue.issue_id,
                self.ai_client.request_count,
                self.ai_client.cache_hit,
                self.ai_client.token_usage.get("total_tokens", 0),
                result.elapsed_seconds,
            )
            return result
        except Exception as exc:
            self.logger.exception("qa_agent_failed id=%s error_type=%s", analysis_id, type(exc).__name__)
            raise

    # --- 단계별 세부 ---------------------------------------------------------

    def _issue_summary(self, issue: IssueRecord) -> dict:
        return {
            "issue_id": issue.issue_id,
            "title": issue.title,
            "type": issue.issue_type,
            "type_label": issue.type_label,
            "type_candidates": list(issue.issue_type_candidates),
            "needs_type_confirmation": issue.needs_type_confirmation,
            "lab_review_result": issue.lab_review_result,
            "status": issue.status,
            "occurrence_frequency": issue.occurrence_frequency,
            "precondition": issue.precondition,
            "steps": issue.steps,
            "expected": issue.expected,
            "actual": issue.actual,
            "occurrence_cause": issue.occurrence_cause,
            "action_details": issue.action_details,
            "linked_srs": issue.linked_srs,
            "linked_issues": issue.linked_issues,
            "occurred_versions": issue.occurred_versions,
            "target_versions": issue.target_versions,
            "attachments": issue.attachments,
            "body_images": issue.body_images,
            "search_terms": issue.search_terms,
            "has_standard_body": issue.has_standard_body,
            "blocks_auto_runtime_tc": issue.blocks_auto_runtime_tc,
            "portal_url": issue.portal_url,
        }

    def _retrieve(
        self, issue: IssueRecord, chunks: list[SpecificationChunk], cases: list[TestCase]
    ) -> tuple[RetrievalResult[SpecificationChunk], RetrievalResult[TestCase]]:
        """사양 Chunk 와 TC 후보를 exact → BM25 로 찾는다.

        BM25 질의는 Issue 본문 전체가 아니라 Trigger 에 해당하는 부분만 쓴다 (제목·Step·
        Expected·발생원인). Comment 나 로그 경로가 질의에 들어가면 후보가 흐려진다.
        """
        query = " ".join(
            [issue.title, issue.title_en, *issue.steps, *issue.expected, issue.occurrence_cause, issue.action_details]
        ).strip()

        spec_retriever = HybridRetriever(chunks, text_getter=_chunk_search_text, key_getter=lambda chunk: chunk.chunk_id)
        spec_hits = spec_retriever.search(
            terms=issue.search_terms,
            query=query,
            top_k=int(self.settings.get("retrieval.specification_top_k", 8)),
            adaptive=True,
        )

        tc_retriever = HybridRetriever(cases, text_getter=lambda case: case.searchable_text(), key_getter=lambda case: case.tc_id)
        tc_hits = tc_retriever.search(
            terms=issue.search_terms,
            query=query,
            top_k=int(self.settings.get("qa_agent.tc_candidate_limit", 40)),
            adaptive=False,
        )
        return spec_hits, tc_hits

    def _build_evidence(
        self,
        issue: IssueRecord,
        decision: QaAgentDecision,
        spec_hits: RetrievalResult[SpecificationChunk],
        tc_hits: RetrievalResult[TestCase],
    ) -> EvidenceStore:
        """판정마다 근거를 붙인다. 근거 없는 판정은 그 사실이 남는다 (규칙 §55)."""
        store = EvidenceStore()
        chunk_by_id = {entry.item.chunk_id: entry for entry in spec_hits.items}
        case_by_id = {entry.item.tc_id: entry for entry in tc_hits.items}

        def spec_evidence_for(chunk_ids: list[str]):
            evidence = []
            for chunk_id in chunk_ids:
                entry = chunk_by_id.get(chunk_id)
                if entry is None:
                    continue
                chunk = entry.item
                evidence.append(
                    specification_evidence(
                        chunk_id=chunk.chunk_id,
                        document=chunk.document_id,
                        page=chunk.page,
                        heading=chunk.heading,
                        excerpt=chunk.text,
                        match_reason=entry.reason,
                    )
                )
            return evidence

        analysis = decision.issue_analysis
        issue_claim = Claim(
            kind="issue_analysis",
            label=issue.issue_id,
            text=" / ".join(filter(None, [analysis.root_cause_summary, analysis.change_target, analysis.after])),
            confidence=1.0 if issue.occurrence_cause else 0.0,
        )
        if issue.occurrence_cause:
            issue_claim.evidence.append(issue_evidence(issue.issue_id, "발생원인", issue.occurrence_cause))
        if issue.action_details:
            issue_claim.evidence.append(issue_evidence(issue.issue_id, "조치내역", issue.action_details))
        store.add(issue_claim)

        for entry in decision.specification_relevance:
            chunk_entry = chunk_by_id.get(entry.chunk_id)
            if chunk_entry is None:
                continue
            store.add(
                Claim(
                    kind="spec_trace",
                    label=entry.chunk_id,
                    text=f"{entry.relevance} — {entry.reason}",
                    evidence=spec_evidence_for([entry.chunk_id]),
                    confidence=1.0,
                )
            )

        for entry in decision.tc_coverage:
            case_entry = case_by_id.get(entry.tc_id)
            evidence = spec_evidence_for(entry.evidence_chunk_ids)
            if case_entry is not None:
                case = case_entry.item
                evidence.append(
                    testcase_evidence(
                        tc_id=case.tc_id,
                        workbook=case.workbook,
                        sheet=case.sheet,
                        row=case.row or "",
                        excerpt=f"{case.step}\n{case.expected_result}",
                        match_reason=case_entry.reason,
                    )
                )
            findings = [TC_JUDGMENT_LABELS.get(entry.judgment, entry.judgment)]
            if entry.judgment in TC_JUDGMENTS_REQUIRING_APPROVAL:
                findings.append("QA 승인 후 반영")
            store.add(
                Claim(
                    kind="tc_coverage",
                    label=entry.tc_id,
                    text=entry.reason,
                    evidence=evidence,
                    confidence=entry.confidence,
                    findings=findings,
                )
            )

        for entry in decision.regression_areas:
            if not entry.applicable:
                continue
            store.add(
                Claim(
                    kind="regression",
                    label=AXIS_LABELS.get(entry.axis, entry.axis),
                    text=f"{entry.trigger} → {entry.expected}".strip(" →"),
                    evidence=spec_evidence_for(entry.evidence_chunk_ids),
                    confidence=0.0,
                    findings=[entry.priority],
                )
            )
        return store

    def _coverage_split(self, decision: QaAgentDecision) -> tuple[list[str], list[str]]:
        """Root Cause 를 덮는 TC 와, Issue 번호만 일치하는 TC 를 나눈다 (G3 입력)."""
        covering = [entry.tc_id for entry in decision.tc_coverage if entry.covers_root_cause]
        link_only = [entry.tc_id for entry in decision.tc_coverage if entry.judgment == "ISSUE_LINK_UPDATE"]
        return covering, link_only


def validation_report_from(payload: dict) -> ValidationReport:
    """저장된 결과에서 검증 리포트를 복원한다 (화면 표시용)."""
    report = ValidationReport()
    for key, value in payload.items():
        if hasattr(report, key):
            setattr(report, key, list(value))
    return report

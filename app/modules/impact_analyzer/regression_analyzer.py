from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import get_settings
from app.core.knowledge_documents import load_for_product
from app.core.logger import configure_logging
from app.core.storage import Storage
from app.modules.impact_analyzer.ai_client import ImpactAnalysisAIClient
from app.modules.impact_analyzer.change_analyzer import analyze_change_rules, trim_by_relevance
from app.modules.impact_analyzer.html_report import create_html_report, create_xlsx_export
from app.modules.impact_analyzer.schemas import ANALYSIS_STAGES, AnalysisResult, SpecificationChunk, TestCase
from app.modules.impact_analyzer.tc_candidate_selector import select_candidates_with_scores
from app.modules.impact_analyzer.tc_draft import create_tc_draft_markdown
from app.modules.impact_analyzer.validation import attach_specification_references, validate_decisions, validate_draft_test_cases
from app.parsers.excel_parser import parse_testcases
from app.parsers.document_parser import extract_document_text, parse_document
from app.retrieval.bm25_retriever import BM25Retriever


class RegressionAnalyzer:
    def __init__(self, ai_client: ImpactAnalysisAIClient | None = None, storage: Storage | None = None) -> None:
        self.settings = get_settings()
        self.ai_client = ai_client or ImpactAnalysisAIClient()
        self.storage = storage or Storage()
        self.logger = configure_logging()

    def run(self, change_paths: list[Path], specification_path: Path, testcase_path: Path, analysis_id: str | None = None, user_notes: str = "") -> AnalysisResult:
        baseline_text = extract_document_text(specification_path)
        chunks = parse_document(specification_path, specification_path.stem)
        cases = parse_testcases(testcase_path)
        doc_labels = {specification_path.stem: specification_path.name}
        return self._execute(change_paths, chunks, cases, baseline_text, specification_path.name, testcase_path.name, analysis_id, user_notes, doc_labels)

    def run_for_product(self, change_paths: list[Path], product: str, analysis_id: str | None = None, user_notes: str = "") -> AnalysisResult:
        """개별 사양서/TC를 고르지 않고, 제품에 등록된 모든 사양서·TC 문서를 검색 대상으로 삼는다.

        사양서1~5처럼 같은 제품에 서로 다른 문서가 여러 개 등록될 수 있으므로, 새 문서가
        추가돼도 이전 문서를 제외하지 않고 전부 합쳐서 검색한다 (Storage.active_documents).
        """
        knowledge = load_for_product(product, storage=self.storage, with_text=True)
        return self._execute(
            change_paths,
            knowledge.chunks,
            knowledge.cases,
            "\n".join(knowledge.baseline_texts),
            knowledge.specification_label,
            knowledge.testcase_label,
            analysis_id,
            user_notes,
            knowledge.document_labels,
            product,
            knowledge.knowledge_documents(),
        )

    def _execute(
        self,
        change_paths: list[Path],
        chunks: list[SpecificationChunk],
        cases: list[TestCase],
        baseline_text: str,
        specification_label: str,
        testcase_label: str,
        analysis_id: str | None,
        user_notes: str = "",
        doc_labels: dict[str, str] | None = None,
        product: str | None = None,
        knowledge_documents: list[dict] | None = None,
    ) -> AnalysisResult:
        started = time.monotonic()
        analysis_id = analysis_id or uuid.uuid4().hex[:12]
        doc_labels = doc_labels or {}
        change_file_name = ", ".join(path.name for path in change_paths) if change_paths else "(문서 없음, 사용자 요청 텍스트만 사용)"
        self.logger.info("analysis_started id=%s change=%s spec=%s tc=%s model=%s", analysis_id, change_file_name, specification_label, testcase_label, self.settings.secrets.gemini_model)

        def stage(index: int) -> None:
            self.storage.update_stage(analysis_id, index, ANALYSIS_STAGES[index - 1], len(ANALYSIS_STAGES))
            self.logger.info("analysis_stage id=%s stage=%s/%s name=%s", analysis_id, index, len(ANALYSIS_STAGES), ANALYSIS_STAGES[index - 1])

        try:
            stage(1)  # 입력 문서 분석
            change_text = "\n\n".join(extract_document_text(path) for path in change_paths)
            if user_notes:
                # 문서가 크고 사용자가 구체적인 요청을 줬다면, 전체를 보내지 않고 관련 줄만 추려 토큰을 아낀다.
                change_text = trim_by_relevance(change_text, user_notes, int(self.settings.get("retrieval.change_text_top_lines", 60)))

            stage(2)  # 변경사항 추출
            change = analyze_change_rules(change_text, baseline_text=baseline_text, user_notes=user_notes)

            stage(3)  # 최신 사양서 조회
            query = " ".join(change.changed_features + change.risk_keywords + [change.purpose])
            relevant_chunks = [chunk for chunk, _ in BM25Retriever(chunks, lambda item: f"{item.heading} {item.text}").search(query, int(self.settings.get("retrieval.specification_top_k", 8)))]

            stage(4)  # TC 후보 검색
            ranked_candidates = select_candidates_with_scores(change, cases, int(self.settings.get("retrieval.candidate_limit", 150)))
            candidates = [case for case, _ in ranked_candidates]

            stage(5)  # AI 영향도 분석
            decisions = self.ai_client.analyze(change, candidates, relevant_chunks)
            change.change_items = self.ai_client.change_items

            stage(6)  # Regression TC 선정
            decisions = validate_decisions(decisions, cases, relevant_chunks, float(self.settings.get("analysis.recommended_confidence", .8)), float(self.settings.get("analysis.review_confidence", .6)))
            decisions = attach_specification_references(decisions, relevant_chunks, doc_labels)

            stage(7)  # 신규 TC 초안 검증
            drafts = validate_draft_test_cases(self.ai_client.draft_test_cases, relevant_chunks)

            stage(8)  # HTML 결과 생성
            result = AnalysisResult(analysis_id=analysis_id, created_at=datetime.now(timezone.utc), change_file=change_file_name, specification_file=specification_label, testcase_file=testcase_label, change=change, total_tc=len(cases), candidate_tc=len(candidates), decisions=decisions, draft_test_cases=drafts, token_usage=self.ai_client.token_usage, prompt_version=self.ai_client.prompt_version)
            result.knowledge_documents = knowledge_documents or []
            result.ai_audit = self.ai_client.audit_snapshot
            result.candidate_ranking = [
                {"rank": rank, "tc_id": case.tc_id, "bm25_score": round(score, 6)}
                for rank, (case, score) in enumerate(ranked_candidates, start=1)
            ]
            if product:
                result.spec_sync = self.storage.latest_sync(product, "specification")
                result.tc_sync = self.storage.latest_sync(product, "testcase")
            result.report_path = create_html_report(result)
            create_xlsx_export(result)
            result.draft_tc_path = create_tc_draft_markdown(result)

            self.logger.info("analysis_finished id=%s requests=%s prompt_tokens=%s candidate_tokens=%s total_tokens=%s manual_review=%s elapsed=%.3f", analysis_id, self.ai_client.request_count, self.ai_client.token_usage.get("prompt_tokens", 0), self.ai_client.token_usage.get("candidate_tokens", 0), self.ai_client.token_usage.get("total_tokens", 0), sum(item.manual_review_required for item in decisions), time.monotonic() - started)
            return result
        except Exception as exc:
            self.logger.exception("analysis_failed id=%s error_type=%s elapsed=%.3f", analysis_id, type(exc).__name__, time.monotonic() - started)
            raise

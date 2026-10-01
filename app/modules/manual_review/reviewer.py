"""매뉴얼 개정 검증 파이프라인 오케스트레이터. 단계별
구조(단계별 storage.update_stage로 SSE 진행 상태 갱신, 실패 시 예외 재전파)를 따른다."""

from __future__ import annotations

import time
from pathlib import Path

from app.core.config import get_settings
from app.core.logger import configure_logging
from app.core.storage import Storage
from app.modules.manual_review.ai_client import ManualReviewAIClient
from app.modules.manual_review.change_filter import is_functional_change
from app.modules.manual_review.cross_manual import find_cross_manual_impacts
from app.modules.manual_review.docx_track_changes import TrackedChange, extract_track_changes
from app.modules.manual_review.pdf_revision_diff import extract_pdf_revision_diff
from app.modules.manual_review.review_signals import apply_human_review_signals
from app.modules.manual_review.release_scope import extract_design_review_changes, extract_release_note_changes, match_release_changes
from app.modules.manual_review.srs_evidence import load_srs_chunks, search_candidates
from app.parsers.document_parser import extract_document_text

MANUAL_REVIEW_STAGES: tuple[str, ...] = (
    "문서 파싱 (Track Changes/PDF Diff 추출)",
    "SRS 근거 준비",
    "Release Scope 대조",
    "다른 Manual 영향 추적",
    "AI 판정",
    "결과 저장",
)


class ManualRevisionReviewer:
    def __init__(self, ai_client: ManualReviewAIClient | None = None, storage: Storage | None = None) -> None:
        self.settings = get_settings()
        self.storage = storage or Storage()
        self.ai_client = ai_client or ManualReviewAIClient(storage=self.storage)
        self.logger = configure_logging()

    def run(
        self,
        revision_path: Path,
        product: str,
        manual_name: str,
        revision_label: str,
        parent_revision_id: int | None = None,
        analysis_id: str | None = None,
        release_note_path: Path | None = None,
        design_review_path: Path | None = None,
        target_version: str = "",
    ) -> dict:
        started = time.monotonic()

        def stage(index: int) -> None:
            if analysis_id:
                self.storage.update_stage(analysis_id, index, MANUAL_REVIEW_STAGES[index - 1], len(MANUAL_REVIEW_STAGES))

        round_number = 0
        baseline_revision_id = None
        if parent_revision_id:
            parent = self.storage.get_manual_revision(parent_revision_id)
            if parent:
                round_number = int(parent["round_number"]) + 1
                baseline_revision_id = parent["baseline_revision_id"] or parent["id"]

        revision_id = self.storage.add_manual_revision(
            product,
            manual_name,
            revision_label,
            revision_path,
            round_number=round_number,
            parent_revision_id=parent_revision_id,
            baseline_revision_id=baseline_revision_id,
            analysis_id=analysis_id,
            status="ANALYZING",
            target_version=target_version,
        )
        self.logger.info("manual_review_started revision_id=%s product=%s manual=%s round=%s", revision_id, product, manual_name, round_number)

        try:
            stage(1)
            suffix = revision_path.suffix.lower()
            if suffix == ".pdf" and not parent_revision_id:
                extract_pdf_revision_diff(revision_path, revision_path)  # Baseline도 실제로 열리는 PDF인지 검증
                for stage_index in range(2, len(MANUAL_REVIEW_STAGES) + 1):
                    stage(stage_index)
                self.storage.update_manual_revision_status(revision_id, "BASELINE")
                return {
                    "revision_id": revision_id, "round_number": round_number,
                    "total_changes": 0, "functional_changes": 0, "decision_counts": {},
                    "prior_open_comments": [], "release_scope_total": 0,
                    "release_scope_missing_suspected": 0, "pdf_baseline": True,
                    "cross_manual_review_required": 0,
                    "token_usage": self.ai_client.token_usage, "request_count": self.ai_client.request_count,
                    "ai_audit": {"request_count": self.ai_client.request_count, "cache_hit_count": self.ai_client.cache_hit_count},
                }
            if suffix == ".pdf":
                parent = self.storage.get_manual_revision(parent_revision_id) if parent_revision_id else None
                if not parent or Path(parent["source_path"]).suffix.lower() != ".pdf":
                    raise ValueError("PDF 리비전은 이전 PDF 리비전을 비교 기준으로 선택해야 합니다.")
                track_result = extract_pdf_revision_diff(Path(parent["source_path"]), revision_path)
            else:
                track_result = extract_track_changes(revision_path)

            stage(2)
            chunks, _doc_labels = load_srs_chunks(self.storage, product)
            max_candidates = int(self.settings.get("manual_review.max_srs_candidates", 6))

            # 모든 변경을 저장하고, AI 판정 대상(functional)의 (change_id, TrackedChange) 쌍을 모은다.
            functional_pairs: list[tuple[int, TrackedChange]] = []
            for change in track_result.changes:
                functional = is_functional_change(change)
                change_id = self.storage.add_manual_change(
                    revision_id, change.kind, change.author, change.date, change.paragraph_index, change.text,
                    functional=functional, source_page=change.source_page, review_required=change.review_required,
                    change_index_in_paragraph=change.change_index_in_paragraph,
                )
                if functional:
                    functional_pairs.append((change_id, change))

            stage(3)
            missing_count, release_scope_total, release_context, release_changes = self._match_release_scope(revision_id, release_note_path, design_review_path, functional_pairs)

            stage(4)
            cross_impacts = find_cross_manual_impacts(self.storage, product, manual_name, release_changes)
            for impact in cross_impacts:
                self.storage.add_cross_manual_impact(
                    revision_id, impact.target_manual, impact.source_document,
                    impact.release_change.source_document, impact.release_change.category,
                    impact.release_change.title, impact.evidence_text, impact.relevance_score,
                )

            stage(5)
            decision_counts: dict[str, int] = {}
            for change_id, change in functional_pairs:
                candidates = search_candidates(chunks, change.text, max_candidates)
                change_release_context = release_context.get(change_id, [])
                judgment = self.ai_client.judge(change, candidates, change_release_context)
                apply_human_review_signals(judgment, change.kind, change.review_required, change_release_context)
                self.storage.update_manual_change_judgment(change_id, judgment.decision.value, judgment.confidence, judgment.model_dump(mode="json"))
                decision_counts[judgment.decision.value] = decision_counts.get(judgment.decision.value, 0) + 1

            stage(6)
            prior_open_comments = self.storage.list_open_comments_for_revision(parent_revision_id) if parent_revision_id else []
            self.storage.update_manual_revision_status(revision_id, "REVIEWED")

            result = {
                "revision_id": revision_id,
                "round_number": round_number,
                "total_changes": len(track_result.changes),
                "functional_changes": len(functional_pairs),
                "decision_counts": decision_counts,
                "prior_open_comments": prior_open_comments,
                "release_scope_total": release_scope_total,
                "release_scope_missing_suspected": missing_count,
                "cross_manual_review_required": len(cross_impacts),
                "token_usage": self.ai_client.token_usage,
                "request_count": self.ai_client.request_count,
                "ai_audit": {"request_count": self.ai_client.request_count, "cache_hit_count": self.ai_client.cache_hit_count},
            }
            self.logger.info(
                "manual_review_finished revision_id=%s requests=%s functional=%s missing_suspected=%s elapsed=%.3f",
                revision_id, self.ai_client.request_count, len(functional_pairs), missing_count, time.monotonic() - started,
            )
            return result
        except Exception:
            self.storage.update_manual_revision_status(revision_id, "FAILED")
            self.logger.exception("manual_review_failed revision_id=%s", revision_id)
            raise

    def _match_release_scope(
        self,
        revision_id: int,
        release_note_path: Path | None,
        design_review_path: Path | None,
        functional_pairs: list[tuple[int, TrackedChange]],
    ) -> tuple[int, int, dict[int, list[dict]], list]:
        """등록된(선택) Release Note/설계검토보고서에서 변경 Scope를 추출해 이번 리비전의
        functional 변경들과 BM25로 대조한다. 매칭되지 않으면 MISSING_SUSPECTED로 저장한다
        (스펙 §13 Reverse 검증). 반환값은 (누락 의심 건수, 전체 Release Scope 건수)."""
        release_changes = []
        if release_note_path and release_note_path.exists():
            release_changes.extend(extract_release_note_changes(extract_document_text(release_note_path), release_note_path.name))
        if design_review_path and design_review_path.exists():
            release_changes.extend(extract_design_review_changes(extract_document_text(design_review_path), design_review_path.name))
        if not release_changes:
            return 0, 0, {}, []

        functional_texts = [(change_id, change.text) for change_id, change in functional_pairs]
        matched = match_release_changes(release_changes, functional_texts)
        missing_count = 0
        context_by_change: dict[int, list[dict]] = {}
        for release_change, matched_change_id in matched:
            status = "FOUND" if matched_change_id else "MISSING_SUSPECTED"
            if status == "MISSING_SUSPECTED":
                missing_count += 1
            source = "release_note" if release_note_path and release_change.source_document == release_note_path.name else "design_review"
            self.storage.add_release_finding(
                revision_id, source, release_change.category, release_change.title, status, matched_change_id,
                description=release_change.description, result_status=release_change.result_status,
            )
            if matched_change_id:
                context_by_change.setdefault(matched_change_id, []).append({
                    "source": source, "category": release_change.category,
                    "title": release_change.title, "description": release_change.description,
                    "result_status": release_change.result_status,
                })
        return missing_count, len(release_changes), context_by_change, release_changes

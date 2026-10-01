"""manual_review 모듈의 2단계 AI 호출 wrapper (스펙 §18, §28-D).

1차(quick) 호출로 PASS가 나오면 2차(detail) 호출을 생략해 비용을 아낀다. 두 단계 모두
`core.gemini_client.GeminiClient.generate_structured`의 캐시(sha256(model+prompt_name+
version+prompt))를 그대로 활용하므로 같은 변경을 재검증해도 중복 호출이 없다.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.core.gemini_client import GeminiClient
from app.core.prompt_manager import load_prompt
from app.core.storage import Storage
from app.core.document_schemas import SpecificationChunk
from app.modules.manual_review.docx_track_changes import TrackedChange
from app.modules.manual_review.schemas import DetailJudgmentResponse, ManualChangeJudgment, ManualJudgment, QuickJudgmentResponse

QUICK_PROMPT_NAME = "manual_revision_quick"
DETAIL_PROMPT_NAME = "manual_revision_detail"


class ManualReviewAIClient:
    def __init__(self, storage: Storage | None = None, responder: Callable[[str], dict] | None = None) -> None:
        self._client = GeminiClient(storage=storage, responder=responder)

    @property
    def request_count(self) -> int:
        return self._client.request_count

    @property
    def cache_hit_count(self) -> int:
        return self._client.cache_hit_count

    @property
    def token_usage(self) -> dict:
        return self._client.token_usage

    def _payload(self, stage: str, change: TrackedChange, candidates: list[SpecificationChunk], release_context: list[dict] | None = None) -> str:
        payload = {
            "stage": stage,
            "manual_change": {
                "change_type": change.kind,
                "author": change.author,
                "text": change.text,
                "paragraph_index": change.paragraph_index,
                "source_page": change.source_page,
                "review_required": change.review_required,
            },
            "candidate_srs": [chunk.model_dump() for chunk in candidates],
            "release_scope_context": release_context or [],
        }
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)

    def judge(self, change: TrackedChange, candidates: list[SpecificationChunk], release_context: list[dict] | None = None) -> ManualChangeJudgment:
        quick_prompt = self._payload("quick", change, candidates, release_context)
        quick_raw = self._client.generate_structured(quick_prompt, prompt_name=QUICK_PROMPT_NAME, response_schema=QuickJudgmentResponse)
        quick = QuickJudgmentResponse.model_validate(quick_raw)

        if quick.decision == ManualJudgment.PASS and not quick.requires_detail_generation:
            return ManualChangeJudgment(
                decision=quick.decision,
                confidence=quick.confidence,
                reason_codes=quick.reason_codes,
                prompt_version=load_prompt(QUICK_PROMPT_NAME).version,
            )

        detail_prompt = self._payload("detail", change, candidates, release_context)
        detail_raw = self._client.generate_structured(detail_prompt, prompt_name=DETAIL_PROMPT_NAME, response_schema=DetailJudgmentResponse)
        detail = DetailJudgmentResponse.model_validate(detail_raw)
        return ManualChangeJudgment(
            decision=quick.decision,
            confidence=quick.confidence,
            reason_codes=quick.reason_codes,
            problem=detail.problem,
            recommended_manual_text=detail.recommended_manual_text,
            qa_comment=detail.qa_comment,
            evidence=detail.evidence,
            needs_human_review=detail.needs_human_review,
            prompt_version=load_prompt(DETAIL_PROMPT_NAME).version,
        )

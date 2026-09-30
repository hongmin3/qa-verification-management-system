from app.modules.impact_analyzer.schemas import DraftTestCase, EvidenceLevel, ImpactDecision, RevisionMark, SpecificationChunk, TestCase


def classify_confidence(decision: ImpactDecision, recommended: float = 0.8, review: float = 0.6) -> ImpactDecision:
    if decision.confidence >= recommended:
        decision.review_status = "AI_RECOMMENDATION_ACCEPTED"
    elif decision.confidence >= review:
        decision.review_status = "REVIEW_RECOMMENDED"
    else:
        decision.review_status = "MANUAL_REVIEW_REQUIRED"
        decision.manual_review_required = True
    return decision


def validate_decisions(decisions: list[ImpactDecision], cases: list[TestCase], chunks: list[SpecificationChunk], recommended: float = 0.8, review: float = 0.6) -> list[ImpactDecision]:
    """AI 판정을 걸러낸다. `cases` 는 AI에게 보낸 TC 후보, `chunks` 는 보낸 사양 조각이다.

    보내지 않은 TC·조각을 가리키는 값은 버린다. 추천 여부(`recommended`)는 AI 값을 그대로 둔다
    (OPEN_QUESTIONS 8-1). 신뢰도는 검토 상태와 사람 확인 필요 표시만 정한다.
    """
    valid_ids = {case.tc_id for case in cases}
    valid_refs = {chunk.chunk_id for chunk in chunks}
    result = []
    for decision in decisions:
        if decision.tc_id not in valid_ids:
            continue
        decision.relevant_specifications = [ref for ref in decision.relevant_specifications if ref in valid_refs]
        if not decision.relevant_specifications:
            decision.confidence = min(decision.confidence, 0.59)
            decision.manual_review_required = True
            if decision.evidence_level == EvidenceLevel.EXPLICIT:
                decision.evidence_level = EvidenceLevel.INFERRED
        result.append(classify_confidence(decision, recommended, review))
    return result


def attach_specification_references(decisions: list[ImpactDecision], chunks: list[SpecificationChunk], doc_labels: dict[str, str]) -> list[ImpactDecision]:
    """chunk_id 같은 내부 참조를 사람이 읽을 수 있는 사양서 설명으로 바꾼다.

    Gemini에게 맡기지 않고 실제 chunk 메타데이터로만 조립하므로 환각 위험이 없다.
    """
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    for decision in decisions:
        decision.specification_reference = ""
        for ref in decision.relevant_specifications:
            chunk = chunk_by_id.get(ref)
            if not chunk:
                continue
            parts = [doc_labels.get(chunk.document_id, chunk.document_id)]
            heading = chunk.heading.strip()
            if heading:
                parts.append(heading[:60])
            parts.append(f"p.{chunk.page}")
            decision.specification_reference = " · ".join(parts)
            if RevisionMark.STRIKETHROUGH_DETECTED in chunk.revision_marks:
                decision.revision_mark = RevisionMark.STRIKETHROUGH_DETECTED
                # 삭제된 사양이 근거면 사람이 확인해야 한다. 검토 상태도 같이 바꿔
                # "AI 추천 채택"이면서 "확인 필요"인 판정이 생기지 않게 한다.
                decision.manual_review_required = True
                decision.review_status = "MANUAL_REVIEW_REQUIRED"
            elif RevisionMark.UNDERLINE_DETECTED in chunk.revision_marks:
                decision.revision_mark = RevisionMark.UNDERLINE_DETECTED
            break
    return decisions


def validate_draft_test_cases(drafts: list[DraftTestCase], chunks: list[SpecificationChunk], limit: int = 20) -> list[DraftTestCase]:
    valid_refs = {chunk.chunk_id for chunk in chunks}
    for draft in drafts:
        draft.evidence_chunk_ids = [ref for ref in draft.evidence_chunk_ids if ref in valid_refs]
    return drafts[:limit]

"""qa_agent 모듈의 AI 호출 wrapper.

`core.GeminiClient` 를 감싸서 payload 조립과 응답 파싱만 담당한다. 호출은 분석 1건당 1회다.

**보내는 것을 좁히는 것이 이 파일의 주 관심사다.** 사양서·TC·규칙 전문을 보내지 않는다.

- 사양: 검색된 Chunk 만, 본문은 `chunk_chars` 로 잘라서.
- TC: 후보만, 그리고 판정에 실제로 쓰는 필드만 (`result`/`remark` 는 보내지 않는다).
- Issue: 구조화된 필드만. 원본 HTML 은 보내지 않는다.
- 규칙: 그 Skill 절만 `system_suffix` 로 (전문 30KB → 2~5KB).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from app.core.document_schemas import SpecificationChunk, TestCase
from app.core.gemini_client import GeminiClient
from app.core.model_router import RoutingDecision, estimate_from_usage, model_for, TIER_STANDARD
from app.core.prompt_manager import load_prompt
from app.core.qa_rules import RuleSet
from app.core.storage import Storage
from app.modules.qa_agent.schemas import AXIS_DESCRIPTIONS, AXIS_LABELS, QaAgentDecision
from app.parsers.polarion_issue import IssueRecord

PROMPT_NAME = "qa_agent_issue_impact"

#: 이 Skill 들의 규칙 절을 system_suffix 로 넣는다 (한 번의 호출이 네 Skill 을 함께 판단한다).
PROMPT_SKILLS = ("S01", "S02", "S03", "S05")


def issue_payload(issue: IssueRecord, max_comments: int = 3, comment_chars: int = 400) -> dict:
    """Issue 를 판정에 필요한 최소 형태로 만든다.

    Comment 는 전부 보내지 않는다 — 규칙 §7 에서 연구소 Comment 는 탐색 단서 수준이고, 실측
    Issue 는 Comment 가 5건 이상 붙는 경우가 흔해 그대로 보내면 입력의 절반이 Comment 가 된다.
    """
    return {
        "issue_id": issue.issue_id,
        "title": issue.title,
        "issue_type": issue.issue_type,
        "issue_type_confirmed": not issue.needs_type_confirmation,
        "lab_review_result": issue.lab_review_result,
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
        "has_body_images": bool(issue.body_images),
        "recent_comments": [comment.text[:comment_chars] for comment in issue.comments[-max_comments:] if comment.text],
    }


def specification_payload(chunks: list[SpecificationChunk], chunk_chars: int = 1200) -> list[dict]:
    return [
        {
            "chunk_id": chunk.chunk_id,
            "document": chunk.document_id,
            "page": chunk.page,
            "heading": chunk.heading,
            "text": chunk.text[:chunk_chars],
            "revision_marks": [mark.value for mark in chunk.revision_marks],
        }
        for chunk in chunks
    ]


def testcase_payload(cases: list[TestCase], field_chars: int = 500) -> list[dict]:
    """판정에 쓰는 필드만. `result`(버전별 수행 결과)와 `remark` 는 보내지 않는다."""
    return [
        {
            "tc_id": case.tc_id,
            "category": case.category,
            "feature": case.feature,
            "precondition": case.precondition[:field_chars],
            "step": case.step[:field_chars],
            "expected_result": case.expected_result[:field_chars],
        }
        for case in cases
    ]


def execution_payload(context) -> dict:
    """QA 가 입력한 환경 사실. 모델이 실제 수행 가능 범위를 넘어선 Expected 를 쓰지 않게 한다."""
    return {
        "can_execute": context.can_execute,
        "can_expose_xray": context.can_expose,
        "has_dose_table": context.has_dose_table,
        "observation_means": list(context.observation_means),
        "draft_mode": context.draft_only if hasattr(context, "draft_only") else context.draft_allowed,
    }


class QaAgentAIClient:
    def __init__(self, storage: Storage | None = None, responder: Callable[[str], dict] | None = None) -> None:
        self._client = GeminiClient(storage=storage, responder=responder)
        self.last_prompt = ""
        self.last_system_suffix = ""
        self.last_response: dict = {}
        self.routing: RoutingDecision | None = None

    @property
    def request_count(self) -> int:
        return self._client.request_count

    @property
    def token_usage(self) -> dict:
        return self._client.token_usage

    @property
    def cache_hit(self) -> bool:
        return self._client.last_cache_hit

    @property
    def prompt_version(self) -> int:
        return load_prompt(PROMPT_NAME).version

    def build_rule_suffix(self, rule_set: RuleSet, char_budget: int = 5000) -> str:
        """이 호출에 필요한 규칙 절만 모아 system_instruction 뒤에 붙일 문자열을 만든다.

        네 Skill 이 한 번에 판단되므로 Skill 별 예산을 나눠 쓴다. 규칙 전문(약 30KB)을 그대로
        보내면 호출 하나에 그것만으로 1만 토큰이 넘고, 대부분은 이 요청과 무관하다.
        """
        if not rule_set.available:
            return ""
        per_skill = max(char_budget // len(PROMPT_SKILLS), 400)
        blocks: list[str] = []
        seen: set[str] = set()
        for skill in PROMPT_SKILLS:
            sections = [section for section in rule_set.slice(skill=skill, char_budget=per_skill) if section.anchor not in seen]
            if not sections:
                continue
            seen.update(section.anchor for section in sections)
            blocks.append(f"## {skill} 관련 규칙\n" + "\n\n".join(section.render() for section in sections))
        if not blocks:
            return ""
        header = (
            f"[{rule_set.product} QA 규칙 {rule_set.revision or '(Rev 미표기)'}] 아래는 이 판단에 해당하는 절만 발췌한 것이다.\n"
            "발췌되지 않은 절이 있으므로 여기 없는 규칙을 지어내지 않는다."
        )
        return "\n\n".join([header, *blocks])

    def analyze(
        self,
        issue: IssueRecord,
        chunks: list[SpecificationChunk],
        cases: list[TestCase],
        rule_set: RuleSet,
        context,
        scope_note: str = "",
        rule_char_budget: int = 5000,
        routing: RoutingDecision | None = None,
    ) -> QaAgentDecision:
        payload = {
            "issue": issue_payload(issue),
            "specifications": specification_payload(chunks),
            "test_cases": testcase_payload(cases),
            # 축은 코드가 고정한다. 모델이 축을 만들면 실행마다 달라져 결과를 비교할 수 없다.
            "regression_axes": [{"axis": axis, "label": AXIS_LABELS[axis], "description": AXIS_DESCRIPTIONS[axis]} for axis in AXIS_LABELS],
            "execution": execution_payload(context),
        }
        if scope_note:
            payload["scope_note"] = scope_note
        prompt = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        suffix = self.build_rule_suffix(rule_set, char_budget=rule_char_budget)
        self.last_prompt = prompt
        self.last_system_suffix = suffix
        self.routing = routing
        raw = self._client.generate_structured(
            prompt,
            prompt_name=PROMPT_NAME,
            response_schema=QaAgentDecision,
            system_suffix=suffix,
            model=routing.model if routing else model_for(TIER_STANDARD),
        )
        self.last_response = raw
        return QaAgentDecision.model_validate({key: value for key, value in raw.items() if key != "token_usage"})

    @property
    def audit_snapshot(self) -> dict:
        """무엇을 실제로 보냈는지 그대로 남긴다. 추정이 아니라 payload 원문을 본다."""
        config = load_prompt(PROMPT_NAME)
        return {
            "prompt_name": config.name,
            "prompt_version": config.version,
            "model": self._client.last_model,
            "routing": self.routing.as_dict() if self.routing else None,
            "cost_estimate": estimate_from_usage(self._client.last_model, self._client.token_usage),
            # 감사 화면은 마스킹까지 끝난 **실제 전송본**을 보여준다.
            "system_instruction": self._client.last_sent_system_instruction or config.system_instruction,
            "system_suffix": self.last_system_suffix,
            "system_suffix_chars": len(self.last_system_suffix),
            "user_prompt": self._client.last_sent_prompt or self.last_prompt,
            "user_prompt_chars": len(self._client.last_sent_prompt or self.last_prompt),
            "masking": self._client.last_mask_report.as_dict(),
            "model_fallback": self._client.model_fallback,
            "response": self.last_response,
            "cache_hit": self._client.last_cache_hit,
            "generation": {
                "temperature": config.temperature,
                "max_output_tokens": config.max_output_tokens,
                "thinking_budget": config.thinking_budget,
            },
        }

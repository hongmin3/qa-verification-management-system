"""같은 Issue·같은 입력으로 여러 모델을 비교한다.

"어느 모델이 나은가"는 의견이 아니라 측정으로 답할 문제다. 이 스크립트는 파이프라인이
조립한 **동일한 Evidence Pack** 을 여러 모델에 보내 결과를 나란히 보여준다.

    python scripts/compare_models.py --product VXvue --issue VP-1234
    python scripts/compare_models.py --product VXvue --issue VP-1234 \\
        --model gemini-2.5-flash --model gemini-3.5-flash

**실제 API 를 호출하므로 토큰이 소모된다.** 모델 수만큼 과금된다.

비교할 때 중요한 것은 "그럴듯한 문장"이 아니라 다음이다.

- 근거가 붙은 판정 비율 — 근거 없이 KEEP 으로 단정하는 모델은 위험하다.
- thought 토큰 — thinking 을 끌 수 없는 모델은 이 값이 출력보다 클 수 있다.
- 판정 건수 — 무관한 TC 까지 판정하는 것은 좋은 신호가 아니다.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.core.model_router import estimate_cost_usd  # noqa: E402
from app.core.prompt_manager import load_prompt  # noqa: E402
from app.core.storage import Storage  # noqa: E402
from app.modules.qa_agent.ai_client import PROMPT_NAME, QaAgentAIClient  # noqa: E402
from app.modules.qa_agent.analyzer import QaAgentAnalyzer  # noqa: E402
from app.modules.qa_agent.gates import ExecutionContext  # noqa: E402
from app.modules.qa_agent.schemas import QaAgentDecision  # noqa: E402


def build_payload(product: str, issue_id: str) -> tuple[str, str]:
    """파이프라인을 mock responder 로 돌려 **실제로 보낼 payload** 를 얻는다.

    비교의 전제는 입력이 완전히 같다는 것이다. 모델별로 파이프라인을 다시 돌리면 검색
    결과가 같더라도 조립 시점이 달라질 수 있어, 한 번 만든 것을 그대로 재사용한다.
    """
    empty = {"issue_analysis": {}, "specification_relevance": [], "tc_coverage": [], "regression_areas": [], "unresolved_questions": []}
    storage = Storage()
    client = QaAgentAIClient(storage=storage, responder=lambda prompt: empty)
    analyzer = QaAgentAnalyzer(ai_client=client, storage=storage)
    result = analyzer.run(
        product=product,
        issue_id=issue_id,
        context=ExecutionContext(can_execute=True, test_data_ready=True, observation_means=("ui", "log")),
    )
    if result.status != "DONE":
        raise SystemExit(f"Gate 에서 멈춰 비교할 수 없습니다: {result.blocking_reasons}")
    return client.last_prompt, client.last_system_suffix


def run_model(model: str, payload: str, suffix: str) -> dict:
    config = load_prompt(PROMPT_NAME)
    generation = types.GenerateContentConfig(
        system_instruction=f"{config.system_instruction}\n\n{suffix}".strip(),
        response_mime_type="application/json",
        response_schema=QaAgentDecision,
        temperature=config.temperature,
        max_output_tokens=config.max_output_tokens,
        thinking_config=types.ThinkingConfig(thinking_budget=config.thinking_budget),
    )
    client = genai.Client(api_key=get_settings().secrets.gemini_api_key)
    started = time.monotonic()
    try:
        response = client.models.generate_content(model=model, contents=payload, config=generation)
    except Exception as exc:
        if "budget 0 is invalid" not in str(exc).casefold() and "thinking mode" not in str(exc).casefold():
            return {"model": model, "error": str(exc)[:200]}
        # thinking 을 끌 수 없는 모델. 켜서 다시 부르고 그 사실을 결과에 남긴다.
        generation.thinking_config = None
        started = time.monotonic()
        try:
            response = client.models.generate_content(model=model, contents=payload, config=generation)
        except Exception as inner:
            return {"model": model, "error": str(inner)[:200]}
        thinking_forced = True
    else:
        thinking_forced = False

    usage = response.usage_metadata
    thought = int(getattr(usage, "thoughts_token_count", 0) or 0)
    decision = QaAgentDecision.model_validate_json(response.text)
    judgments: dict[str, int] = {}
    for entry in decision.tc_coverage:
        judgments[entry.judgment] = judgments.get(entry.judgment, 0) + 1
    with_evidence = sum(1 for entry in decision.tc_coverage if entry.evidence_chunk_ids)
    relevance: dict[str, int] = {}
    for entry in decision.specification_relevance:
        relevance[entry.relevance] = relevance.get(entry.relevance, 0) + 1
    return {
        "model": model,
        "seconds": round(time.monotonic() - started, 1),
        "thinking_forced": thinking_forced,
        "prompt_tokens": int(usage.prompt_token_count or 0),
        "output_tokens": int(usage.candidates_token_count or 0),
        "thought_tokens": thought,
        # thought 토큰은 출력으로 과금된다.
        "usd": estimate_cost_usd(model, int(usage.prompt_token_count or 0), int(usage.candidates_token_count or 0) + thought),
        "root_cause": (decision.issue_analysis.root_cause_summary or "")[:80],
        "relevance": relevance,
        "judgments": judgments,
        "tc_total": len(decision.tc_coverage),
        "tc_with_evidence": with_evidence,
        "axes": [entry.axis for entry in decision.regression_areas if entry.applicable],
        "questions": len(decision.unresolved_questions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="같은 입력으로 모델 비교 (실제 API 호출)")
    parser.add_argument("--product", required=True)
    parser.add_argument("--issue", required=True, help="Issue ID (Export 폴더에 있어야 한다)")
    parser.add_argument(
        "--model", action="append", default=[],
        help="비교할 모델. 없으면 config.yaml 의 standard / complex 를 쓴다",
    )
    args = parser.parse_args()

    settings = get_settings()
    models = args.model or [
        str(settings.get("models.standard", "gemini-2.5-flash")),
        str(settings.get("models.complex", "gemini-3.5-flash")),
    ]
    models = list(dict.fromkeys(models))

    print(f"Issue {args.issue} ({args.product}) — 동일 입력으로 {len(models)}개 모델 비교")
    payload, suffix = build_payload(args.product, args.issue)
    print(f"입력 {len(payload):,}자 + 규칙 발췌 {len(suffix):,}자\n")

    rows = [run_model(model, payload, suffix) for model in models]

    for row in rows:
        print(f"=== {row['model']} ===")
        if "error" in row:
            print(f"  실패: {row['error']}\n")
            continue
        forced = " (thinking 강제)" if row["thinking_forced"] else ""
        print(f"  시간   {row['seconds']}초{forced}")
        print(f"  토큰   in {row['prompt_tokens']:,} / out {row['output_tokens']:,} / thought {row['thought_tokens']:,}")
        print(f"  비용   ${row['usd']:.4f} (추정)")
        print(f"  S01    {row['root_cause']}")
        print(f"  S02    {row['relevance']}")
        print(f"  S03    {row['judgments']} — 근거 붙은 판정 {row['tc_with_evidence']}/{row['tc_total']}건")
        print(f"  S05    {row['axes']}")
        print(f"  질문   {row['questions']}건\n")

    usable = [row for row in rows if "error" not in row]
    if len(usable) >= 2:
        print("=== 비교 ===")
        print(f"{'모델':26} {'초':>6} {'out':>8} {'thought':>9} {'USD':>9} {'근거비율':>9}")
        for row in usable:
            ratio = f"{row['tc_with_evidence']}/{row['tc_total']}" if row["tc_total"] else "-"
            print(f"{row['model']:26} {row['seconds']:>6} {row['output_tokens']:>8,} {row['thought_tokens']:>9,} {row['usd']:>9.4f} {ratio:>9}")
        print()
        print("판단 기준: 근거가 붙은 판정 비율이 높고, thought 토큰이 적고, 무관한 TC 까지")
        print("판정하지 않는 모델이 이 업무에 맞습니다. 문장이 그럴듯한 것은 기준이 아닙니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

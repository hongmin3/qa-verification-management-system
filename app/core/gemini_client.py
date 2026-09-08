from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import get_settings
from app.core.prompt_manager import load_prompt
from app.core.security_filter import MaskReport, mask_text
from app.core.storage import Storage


class GeminiClient:
    """도메인 무관 Gemini 구조화 호출 클라이언트. 어떤 모듈의 Pydantic 스키마인지, 어떤
    system_instruction을 쓰는지는 전혀 모른다 — 호출자가 prompt_name(→prompts/*.yaml)과
    response_schema를 그때그때 넘긴다. 캐시/재시도/토큰 사용량 추적만 여기서 담당한다."""

    def __init__(self, storage: Storage | None = None, responder: Callable[[str], dict] | None = None) -> None:
        self.settings = get_settings()
        self.storage = storage or Storage()
        self.responder = responder
        self.request_count = 0
        self.cache_hit_count = 0
        self.token_usage: dict[str, int] = {}
        self.last_cache_hit = False
        #: 직전 호출에서 마스킹된 항목 요약. 감사 화면·로그에 남긴다 (값 자체는 담기지 않는다).
        self.last_mask_report = MaskReport()
        #: 마스킹까지 끝나 **실제로 전송된** 문자열. 감사 화면은 이것을 보여줘야 한다 —
        #: 호출자가 만든 원본을 보여주면 "무엇이 나갔는지"를 확인하는 의미가 없다.
        self.last_sent_prompt = ""
        self.last_sent_system_instruction = ""
        #: 직전 호출에 실제로 쓴 모델 (호출별로 다를 수 있다 — app/core/model_router.py).
        self.last_model = self.settings.secrets.gemini_model

    @retry(retry=retry_if_exception_type((TimeoutError, ConnectionError)), stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True)
    def _request(self, prompt: str, *, system_instruction: str, response_schema: type[BaseModel], temperature: float, max_output_tokens: int, thinking_budget: int, model: str) -> dict:
        self.request_count += 1
        if self.responder:
            return self.responder(prompt)
        if not self.settings.secrets.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY가 설정되지 않았습니다.")
        client = genai.Client(api_key=self.settings.secrets.gemini_api_key)
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=response_schema,
                temperature=temperature,
                # 후보가 많으면 응답 JSON이 커서 기본 한도에 잘릴 수 있어 명시적으로 올린다.
                max_output_tokens=max_output_tokens,
                # Gemini 2.5의 내부 thinking 토큰이 max_output_tokens 예산을 함께 소비해 JSON이 잘리는
                # 문제가 있었다. 구조화된 추출 작업이라 별도 추론 과정이 필요 없으므로 비활성화한다.
                thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget),
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        token_usage = {
            "prompt_tokens": int(getattr(usage, "prompt_token_count", 0) or 0),
            "candidate_tokens": int(getattr(usage, "candidates_token_count", 0) or 0),
            "total_tokens": int(getattr(usage, "total_token_count", 0) or 0),
        }
        finish_reason = getattr(getattr(response, "candidates", [None])[0], "finish_reason", None)
        try:
            payload = json.loads(response.text or "{}")
        except json.JSONDecodeError as exc:
            hint = " (MAX_TOKENS로 잘렸을 가능성이 높습니다 — retrieval.candidate_limit을 낮춰보세요.)" if str(finish_reason) == "MAX_TOKENS" else ""
            raise RuntimeError(f"Gemini 응답이 완전한 JSON이 아닙니다{hint}: {exc}") from exc
        payload["token_usage"] = token_usage
        return payload

    def generate_structured(self, prompt: str, *, prompt_name: str, response_schema: type[BaseModel], system_suffix: str = "", model: str = "") -> dict:
        """prompt_name(prompts/{prompt_name}.yaml)의 system_instruction/생성 설정으로 Gemini를
        호출하고, 응답 JSON 전체를 dict로 반환한다 (도메인 파싱은 호출자 책임).

        `system_suffix`는 YAML에 담을 수 없는 **실행 시점에 결정되는 지시**를 system_instruction
        뒤에 덧붙인다 (QA Agent가 제품별 규칙 문서에서 그 Skill에 해당하는 절만 잘라 넣는 데
        쓴다 — 규칙 전문을 매 호출에 보내지 않기 위함이다). 캐시 키에 포함되므로 규칙이 바뀌면
        캐시가 자동으로 갈린다.

        `token_usage`는 이 클라이언트 인스턴스가 실제로 과금된(=캐시 미스) 호출의 누적 합계다.
        매뉴얼 개정 검증처럼 한 인스턴스로 변경 건마다 여러 번 호출하는 경우 마지막 호출값만
        남으면 전체 비용을 과소집계하게 되어 누적 방식으로 두되, 캐시 Hit는 실제 비용이 0이므로
        (README의 "동일 입력 재분석 시 캐시로 비용 없음") 합산하지 않는다 — 이전에는 캐시로
        재사용해도 원래 호출의 토큰 수가 다시 집계되어 일일 토큰 한도를 실제보다 과다 소모한
        것처럼 보이게 하는 문제가 있었다."""
        prompt_cfg = load_prompt(prompt_name)
        # 외부로 나가는 모든 문자열이 지나는 단일 통로다. 여기서 마스킹하면 기능별로
        # 빠뜨릴 여지가 없다 (`security.mask_outbound=false` 로 끌 수 있지만 기본은 켜짐).
        if self.settings.get("security.mask_outbound", True):
            prompt, self.last_mask_report = mask_text(prompt)
            if system_suffix:
                system_suffix, suffix_report = mask_text(system_suffix)
                for code, count in suffix_report.counts.items():
                    self.last_mask_report.counts[code] = self.last_mask_report.counts.get(code, 0) + count
        else:
            self.last_mask_report = MaskReport(chars_before=len(prompt), chars_after=len(prompt))

        system_instruction = f"{prompt_cfg.system_instruction}\n\n{system_suffix}".strip() if system_suffix else prompt_cfg.system_instruction
        self.last_sent_prompt = prompt
        self.last_sent_system_instruction = system_instruction
        self.last_model = model or self.settings.secrets.gemini_model
        cache_key = hashlib.sha256(
            (self.last_model + prompt_name + str(prompt_cfg.version) + system_suffix + prompt).encode()
        ).hexdigest()
        cached = self.storage.cache_get(cache_key) if self.settings.get("analysis.cache_enabled", True) else None
        self.last_cache_hit = cached is not None
        if self.last_cache_hit:
            self.cache_hit_count += 1
            return cached
        raw = self._request(
            prompt,
            system_instruction=system_instruction,
            response_schema=response_schema,
            temperature=prompt_cfg.temperature,
            max_output_tokens=prompt_cfg.max_output_tokens,
            thinking_budget=prompt_cfg.thinking_budget,
            model=self.last_model,
        )
        self.storage.cache_set(cache_key, raw)
        for key, value in raw.get("token_usage", {}).items():
            self.token_usage[key] = self.token_usage.get(key, 0) + int(value)
        return raw

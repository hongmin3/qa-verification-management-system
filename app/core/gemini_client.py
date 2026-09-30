from __future__ import annotations

import hashlib
import json
from collections.abc import Callable

from google import genai
from google.genai import types
from pydantic import BaseModel
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core import claude_cli
from app.core.config import get_settings
from app.core.model_router import PROVIDER_CLAUDE, TIER_STANDARD, ai_provider, model_for
from app.core.notifier import notify_for_error
from app.core.prompt_manager import load_prompt
from app.core.security_filter import MaskReport, mask_text
from app.core.storage import Storage


# 모델이 계정에서 제공되지 않을 때 API 가 내려주는 신호. 정확한 문구는 바뀔 수 있어
# 상태 코드(404/NOT_FOUND)와 대표 표현을 함께 본다.
_MODEL_UNAVAILABLE_HINTS = ("not_found", "no longer available", "is not found", "not supported for", "does not exist")

# Pro 계열은 thinking 을 끌 수 없다 (실측: `Budget 0 is invalid. This model only works in
# thinking mode`). 이 프로젝트는 `thinking_budget=0` 이 전제이므로 그 모델을 설정하면 모든
# 호출이 400 으로 실패한다. 설정 실수로 전체가 멈추지 않게 thinking 을 켜서 한 번 다시 부른다.
_THINKING_REQUIRED_HINTS = ("budget 0 is invalid", "only works in thinking mode", "thinking_budget")


def _is_model_unavailable(exc: Exception) -> bool:
    message = str(exc).casefold()
    return "404" in message or any(hint in message for hint in _MODEL_UNAVAILABLE_HINTS)


def _requires_thinking(exc: Exception) -> bool:
    message = str(exc).casefold()
    return "400" in message and any(hint in message for hint in _THINKING_REQUIRED_HINTS)


class GeminiClient:
    """도메인 무관 AI 구조화 호출 클라이언트 (SPEC REQ-AICALL-001). 어떤 모듈의 Pydantic 스키마인지,
    어떤 system_instruction을 쓰는지는 전혀 모른다 — 호출자가 prompt_name(→prompts/*.yaml)과
    response_schema를 그때그때 넘긴다. 캐시/재시도/토큰 사용량 추적만 여기서 담당한다.

    이름은 옛 이름이다. 실제로 부르는 AI 는 `ai.provider` 가 정한다 — 기본 `claude_cli` 는
    Claude CLI(`app/core/claude_cli.py`, REQ-AICALL-005), `gemini` 는 Gemini API 다."""

    def __init__(self, storage: Storage | None = None, responder: Callable[[str], dict] | None = None,
                 claude_runner: claude_cli.Runner | None = None) -> None:
        self.settings = get_settings()
        self.storage = storage or Storage()
        self.responder = responder
        #: 테스트가 `subprocess.run` 대신 끼우는 실행기. 비면 실제 CLI 를 부른다.
        self.claude_runner = claude_runner
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
        #: 직전 호출의 AI 제공자와 모델 (호출별로 다를 수 있다 — app/core/model_router.py).
        self.last_provider = ai_provider(self.settings)
        self.last_model = self._default_model(self.last_provider)
        #: 상위 등급 모델을 쓰지 못해 기본 모델로 물러난 경우의 기록. 조용히 바꾸지 않는다.
        self.model_fallback: dict | None = None
        #: thinking 을 끌 수 없는 모델이라 켜서 호출한 경우의 기록 (토큰이 늘어난다).
        self.thinking_override: dict | None = None

    def _default_model(self, provider: str) -> str:
        if provider == PROVIDER_CLAUDE:
            return model_for(TIER_STANDARD)
        return self.settings.secrets.gemini_model

    def _retry_policy(self) -> Retrying:
        """네트워크 오류(시간 초과·연결 끊김) 재시도 규칙. `config.yaml` 의 `analysis.max_retries`·
        `retry_min_seconds`·`retry_max_seconds` 를 호출할 때마다 읽는다 (값이 없으면 3회, 1~10초)."""
        attempts = max(1, int(self.settings.get("analysis.max_retries", 3) or 1))
        wait_min = max(0.0, float(self.settings.get("analysis.retry_min_seconds", 1) or 0))
        wait_max = max(wait_min, float(self.settings.get("analysis.retry_max_seconds", 10) or 0))
        return Retrying(
            retry=retry_if_exception_type((TimeoutError, ConnectionError)),
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(min=wait_min, max=wait_max),
            reraise=True,
        )

    def _request(self, prompt: str, **kwargs) -> dict:
        return self._retry_policy()(self._request_once, prompt, **kwargs)

    def _request_once(self, prompt: str, *, system_instruction: str, response_schema: type[BaseModel], temperature: float, max_output_tokens: int, thinking_budget: int | None, model: str) -> dict:
        self.request_count += 1
        if self.responder:
            return self.responder(prompt)
        if self.last_provider == PROVIDER_CLAUDE:
            # 생성 온도·출력 상한·추론 예산은 CLI 인자에 없어 쓰지 않는다 (REQ-AICALL-001).
            return claude_cli.request_structured(
                prompt,
                system_prompt=system_instruction,
                schema=response_schema.model_json_schema(),
                model=model,
                settings=self.settings,
                runner=self.claude_runner,
            )
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
                # `None` 이면 이 설정을 보내지 않는다 — thinking 을 끌 수 없는 모델(Pro 계열)용 폴백.
                thinking_config=types.ThinkingConfig(thinking_budget=thinking_budget) if thinking_budget is not None else None,
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
        self.last_provider = ai_provider(self.settings)
        self.last_model = model or self._default_model(self.last_provider)
        cache_key = hashlib.sha256(
            (self.last_model + prompt_name + str(prompt_cfg.version) + system_suffix + prompt).encode()
        ).hexdigest()
        cached = self.storage.cache_get(cache_key) if self.settings.get("analysis.cache_enabled", True) else None
        self.last_cache_hit = cached is not None
        if self.last_cache_hit:
            self.cache_hit_count += 1
            return self._restore_served(cached)
        try:
            raw = self._request(
                prompt,
                system_instruction=system_instruction,
                response_schema=response_schema,
                temperature=prompt_cfg.temperature,
                max_output_tokens=prompt_cfg.max_output_tokens,
                thinking_budget=prompt_cfg.thinking_budget,
                model=self.last_model,
            )
        except Exception as exc:
            # 앱이 스스로 복구할 수 없는 오류(할당량 소진·모델 사용 불가)는 사람이 조치해야
            # 끝난다. 화면을 보고 있지 않으면 알 방법이 없으므로 메일로 알린다.
            # 알림 실패가 원래 오류를 가리지 않는다 (notifier 가 예외를 올리지 않는다).
            notify_for_error(
                str(exc),
                context={"모델": self.last_model, "프롬프트": f"{prompt_name} v{prompt_cfg.version}"},
                storage=self.storage,
            )
            if self.last_provider == PROVIDER_CLAUDE:
                # Claude CLI 는 추론 켜기·대신 쓸 모델이 없다. 시간 초과도 다시 하지 않는다 (REQ-AICALL-003).
                raise
            if _requires_thinking(exc):
                # Pro 계열은 thinking 을 끌 수 없다. 설정 실수로 전체가 멈추지 않게 thinking 을
                # 켜서 다시 부르되, 그 사실을 남긴다 — thinking 토큰이 과금되고 응답이
                # max_output_tokens 를 함께 소비하므로 조용히 넘길 변화가 아니다.
                self.thinking_override = {
                    "model": self.last_model,
                    "reason": "이 모델은 thinking 을 끌 수 없습니다 (thinking_budget=0 거부)",
                }
                raw = self._request(
                    prompt,
                    system_instruction=system_instruction,
                    response_schema=response_schema,
                    temperature=prompt_cfg.temperature,
                    max_output_tokens=prompt_cfg.max_output_tokens,
                    thinking_budget=None,
                    model=self.last_model,
                )
                self._cache_with_served(cache_key, raw, thinking_override=self.thinking_override)
                for key, value in raw.get("token_usage", {}).items():
                    self.token_usage[key] = self.token_usage.get(key, 0) + int(value)
                return raw

            # 모델이 계정에서 막혀 있는 경우가 실제로 있다 (`gemini-2.5-pro`는 "no longer
            # available to new users"). 환경 문제로 분석 전체를 실패시키지 않고 한 번 물러난다.
            #
            # 후보를 두 단계로 둔다 — `models.standard` 자체가 막힌 모델일 수 있으므로
            # `secrets.gemini_model`(가장 오래 검증된 값)까지 훑는다. 물러났다는 사실은
            # audit 에 남는다.
            candidates = [
                str(self.settings.get("models.standard", "") or ""),
                str(self.settings.secrets.gemini_model or ""),
            ]
            fallback = next((name for name in candidates if name and name != self.last_model), "")
            if not _is_model_unavailable(exc) or not fallback:
                raise
            self.model_fallback = {"requested": self.last_model, "used": fallback, "reason": str(exc)[:300]}
            self.last_model = fallback
            raw = self._request(
                prompt,
                system_instruction=system_instruction,
                response_schema=response_schema,
                temperature=prompt_cfg.temperature,
                max_output_tokens=prompt_cfg.max_output_tokens,
                thinking_budget=prompt_cfg.thinking_budget,
                model=fallback,
            )
            self._cache_with_served(cache_key, raw, model_fallback=self.model_fallback)
        else:
            self.storage.cache_set(cache_key, raw)
        for key, value in raw.get("token_usage", {}).items():
            self.token_usage[key] = self.token_usage.get(key, 0) + int(value)
        return raw

    #: 저장본(ai_cache)에 "실제로 답한 모델" 기록을 함께 담는 키. 호출자에게는 돌려주지 않는다.
    _SERVED_KEY = "_served"

    def _cache_with_served(self, cache_key: str, raw: dict, *, model_fallback: dict | None = None, thinking_override: dict | None = None) -> None:
        """대신 쓴 모델이나 켠 추론 기록을 응답과 함께 저장한다.

        저장본 키는 처음 요청한 모델 이름으로 만든다. 기록 없이 저장하면 다음 같은 요청이
        저장본에서 끝나면서 "요청한 모델로 답했다"고 보이게 된다 (docs/SPEC_CODE_MISMATCH.md 5절 8번).
        """
        served = {"model": self.last_model}
        if model_fallback:
            served["model_fallback"] = dict(model_fallback)
        if thinking_override:
            served["thinking_override"] = dict(thinking_override)
        self.storage.cache_set(cache_key, {**raw, self._SERVED_KEY: served})

    def _restore_served(self, cached: dict) -> dict:
        served = cached.pop(self._SERVED_KEY, None)
        if isinstance(served, dict):
            if served.get("model"):
                self.last_model = str(served["model"])
            if served.get("model_fallback"):
                self.model_fallback = dict(served["model_fallback"])
            if served.get("thinking_override"):
                self.thinking_override = dict(served["thinking_override"])
        return cached

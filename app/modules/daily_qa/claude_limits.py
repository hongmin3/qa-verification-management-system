"""Claude 사용량 한도·인증 실패 감지 (SPEC REQ-QAINTEL-025).

Claude CLI 는 한도에 걸리면 결과 문장에 한도 종류와 초기화 시각을 적는다. 모양이 CLI 판마다 달라
알려진 모양을 모두 읽고, 못 읽으면 종류만 `UNKNOWN` 으로 둔다.

    Claude AI usage limit reached|1759300000          (옛 판: 초기화 시각이 유닉스 초)
    5-hour limit reached ∙ resets 3pm                  (세션 한도)
    Weekly limit reached ∙ resets Oct 6, 9am           (주간 한도)
    You've hit your session limit · resets 7pm         (2026-09-28 서버에서 실제로 본 문장)
    You've hit your limit · resets 7pm (Asia/Seoul)
    Your limit will reset at 3pm (Asia/Seoul).

한도에 걸리면 그 실행의 남은 AI 작업을 멈추고 이벤트를 `pending` 으로 남긴다(실패 횟수는 올리지
않는다 — 이벤트 탓이 아니다). 세션 한도는 초기화 뒤 한 번 다시 돈다(catch-up). 주간 한도는 변경
감지만 계속하고 초기화 뒤 첫 실행에서 밀린 분석을 한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KIND_SESSION = "SESSION"
KIND_WEEKLY = "WEEKLY"
KIND_UNKNOWN = "UNKNOWN"
KIND_AUTH = "AUTH"
KIND_CLI_MISSING = "CLI_MISSING"
KIND_LABELS = {
    KIND_SESSION: "Claude 세션(5시간) 사용량 한도",
    KIND_WEEKLY: "Claude 주간 사용량 한도",
    KIND_UNKNOWN: "Claude 사용량 한도",
    KIND_AUTH: "Claude 인증 실패",
    KIND_CLI_MISSING: "Claude CLI 없음",
}
KST = ZoneInfo("Asia/Seoul")

EPOCH_RE = re.compile(r"usage limit reached\|(\d{9,11})", re.IGNORECASE)
LIMIT_RE = re.compile(r"(usage limit|limit reached|hit your (?:[\w-]+ )?limit|rate limit|limit will reset|out of extra usage)", re.IGNORECASE)
WEEKLY_RE = re.compile(r"weekly|7-day|opus limit", re.IGNORECASE)
SESSION_RE = re.compile(r"5-hour|five-hour|session limit|5h limit", re.IGNORECASE)
RESET_RE = re.compile(
    r"reset[s]?\s*(?:at\s*)?(?P<when>(?:(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2}),?\s*)?(?:(?P<wday>Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s+)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?)\s*(?:\((?P<tz>[A-Za-z_]+/[A-Za-z_]+|UTC)\))?",
    re.IGNORECASE,
)
#: Claude CLI 가 쓰는 인증 실패 문구만 본다. 작업 결과에 "HTTP 401" 같은 말이 들어 있어도(DICOM·HTTP 이슈)
#: 인증 실패로 보지 않는다. 인증 실패는 토큰이 바뀔 때까지 AI 를 막으므로 잘못 잡으면 오래 멈춘다.
AUTH_RE = re.compile(
    r"(invalid api key|oauth token has expired|token (?:is )?expired|authentication[_ ]error|please run /login|"
    r"invalid bearer token|api error:\s*401\b|^\s*401\s+unauthori[sz]ed)",
    re.IGNORECASE | re.MULTILINE,
)
MONTHS = {name: index for index, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass
class LimitInfo:
    kind: str
    reset_at: str = ""          # UTC ISO. 비면 모른다
    message: str = ""
    detected_at: str = ""
    run_id: str = ""
    token_fingerprint: str = ""

    @property
    def label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def reset_kst(self) -> str:
        if not self.reset_at:
            return ""
        return datetime.fromisoformat(self.reset_at).astimezone(KST).strftime("%Y-%m-%d %H:%M")

    def active(self, now: datetime | None = None, token: str = "") -> bool:
        now = now or datetime.now(timezone.utc)
        if self.kind in (KIND_AUTH, KIND_CLI_MISSING):
            # 토큰을 바꾸면(서버 secrets 갱신) 다시 시도한다.
            return self.token_fingerprint == token_fingerprint(token)
        if not self.reset_at:
            return False  # 초기화 시각을 모르면 막지 않는다. 다음 실행이 한 번 시도해 본다.
        return datetime.fromisoformat(self.reset_at) > now

    def describe(self) -> str:
        if self.kind == KIND_AUTH:
            return f"{self.label}. 서버 secrets.txt 의 CLAUDE_CODE_OAUTH_TOKEN 을 다시 발급해 넣으세요(claude setup-token)."
        if self.kind == KIND_CLI_MISSING:
            return f"{self.label}. 서버에 Claude CLI 를 설치하거나 daily_qa.claude_command 를 확인하세요."
        when = self.reset_kst()
        return f"{self.label} 초과. " + (f"{when}(한국 시간)에 초기화됩니다." if when else "초기화 시각을 알 수 없습니다.")

    def as_dict(self) -> dict:
        data = asdict(self)
        data.update({"label": self.label, "reset_kst": self.reset_kst(), "description": self.describe()})
        return data


def token_fingerprint(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()[:12]


def _zone(name: str | None):
    if not name:
        return datetime.now().astimezone().tzinfo
    if name.upper() == "UTC":
        return timezone.utc
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return datetime.now().astimezone().tzinfo


def parse_reset(text: str, now: datetime) -> datetime | None:
    epoch = EPOCH_RE.search(text or "")
    if epoch:
        return datetime.fromtimestamp(int(epoch.group(1)), tz=timezone.utc)
    match = RESET_RE.search(text or "")
    if not match:
        return None
    zone = _zone(match.group("tz"))
    local_now = now.astimezone(zone)
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    ampm = (match.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    if match.group("month"):
        month = MONTHS.get(match.group("month")[:3].lower())
        if not month:
            return None
        candidate = local_now.replace(month=month, day=int(match.group("day")), hour=hour, minute=minute, second=0, microsecond=0)
        if candidate < local_now - timedelta(days=1):
            candidate = candidate.replace(year=candidate.year + 1)
    else:
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if match.group("wday"):
            target = WEEKDAYS.index(match.group("wday")[:3].lower())
            candidate += timedelta(days=(target - local_now.weekday()) % 7)
        if candidate <= local_now:
            candidate += timedelta(days=7 if match.group("wday") else 1)
    return candidate.astimezone(timezone.utc)


def classify(text: str, now: datetime | None = None, token: str = "", run_id: str = "") -> LimitInfo | None:
    """실패 문장이 한도·인증 문제면 LimitInfo, 아니면 None (보통 실패로 다룬다)."""
    now = now or datetime.now(timezone.utc)
    text = text or ""
    detected = now.isoformat(timespec="seconds")
    if AUTH_RE.search(text) and not LIMIT_RE.search(text):
        return LimitInfo(KIND_AUTH, "", text[:300], detected, run_id, token_fingerprint(token))
    if not (LIMIT_RE.search(text) or EPOCH_RE.search(text)):
        return None
    reset = parse_reset(text, now)
    if WEEKLY_RE.search(text):
        kind = KIND_WEEKLY
    elif SESSION_RE.search(text):
        kind = KIND_SESSION
    elif reset is not None:
        # 옛 판은 종류를 적지 않는다. 6시간 안에 풀리면 세션 한도로 본다.
        kind = KIND_SESSION if reset - now <= timedelta(hours=6) else KIND_WEEKLY
    else:
        kind = KIND_UNKNOWN
    return LimitInfo(kind, reset.isoformat(timespec="seconds") if reset else "", text[:300], detected, run_id)


def load(raw: str) -> LimitInfo | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    try:
        return LimitInfo(**{key: data.get(key, "") for key in LimitInfo.__dataclass_fields__})
    except TypeError:
        return None


def dump(info: LimitInfo) -> str:
    return json.dumps(asdict(info), ensure_ascii=False)

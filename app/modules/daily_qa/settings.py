"""`config.yaml` 의 `daily_qa` 절, 제품 설정, 비밀 설정을 한 곳에서 읽는다.

제품마다 달라지는 값(Polarion 프로젝트·조회식·필드 이름·규칙 판)은 제품 설정
(`config/products/<slug>.yaml`)에서 읽어 `ProductProfile` 로 넘긴다 (SPEC REQ-QAINTEL-002).
경로·잠금·상태 키는 제품 slug 로 나눈다 (REQ-QAINTEL-023).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings, get_settings
from app.modules.daily_qa.product_adapter import ProductProfile, build_profile, load_profile, slug_of

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True)
class PolarionSettings:
    host: str
    token: str
    project_id: str
    srs_query: str
    issue_query: str
    verify_ssl: bool = True
    timeout_seconds: int = 90
    page_size: int = 100
    request_interval_seconds: float = 0.15

    @property
    def configured(self) -> bool:
        return bool(self.host and self.token and self.project_id)


@dataclass(frozen=True)
class IntelligenceSettings:
    """후보 압축·댓글·재시도 상한 (`daily_qa.intelligence`, SPEC REQ-QAINTEL-004·006·011)."""

    issue_candidates: int = 8
    spec_candidates: int = 6
    spec_doc_candidates: int = 4
    manual_candidates: int = 3
    comment_fetch_limit: int = 300
    comment_min_chars: int = 15
    issue_drop_ratio: float = 0.5
    event_max_attempts: int = 3
    recent_fixed_days: int = 90
    #: 세션 한도 초기화가 이 시간 안이면 초기화 뒤 한 번 다시 돈다 (REQ-QAINTEL-025).
    catchup_max_hours: int = 12
    catchup_delay_minutes: int = 5
    #: Knowledge 에 등록된 사양서 조각도 검색할지. 끄면 SRS 스냅샷만 검색한다.
    use_knowledge_documents: bool = True
    #: 현재 상태 기준 이슈 점검의 한 작업 이슈 수와 모델 (REQ-QAINTEL-030). 모델을 비우면 ai.claude.models.light.
    audit_batch_size: int = 10
    audit_model: str = ""


@dataclass(frozen=True)
class DailyQaSettings:
    root: Path
    product: str
    workspace_dir: Path
    claude_command: str
    claude_token: str
    task_timeout_seconds: int
    max_tasks_per_run: int
    batch_size: int
    tc_candidate_limit: int
    weekly_day: str
    polarion: PolarionSettings
    email_to: tuple[str, ...]
    review_base_url: str
    enabled: bool = True
    extra: dict = field(default_factory=dict)
    profile: ProductProfile | None = None
    #: 개편 전 데이터(제품 열이 빈 기록, 옛 스냅샷 위치, 전역 상태 키)의 주인. `daily_qa.product` 다.
    legacy_product: str = ""
    intelligence: IntelligenceSettings = field(default_factory=IntelligenceSettings)

    @property
    def product_profile(self) -> ProductProfile:
        if self.profile is not None:
            return self.profile
        fallback = {"project_id": self.polarion.project_id, "srs_query": self.polarion.srs_query,
                    "issue_query": self.polarion.issue_query}
        return build_profile(None, self.product, self.root, fallback)

    @property
    def slug(self) -> str:
        return self.product_profile.slug

    @property
    def is_legacy_product(self) -> bool:
        return slug_of(self.legacy_product or self.product) == self.slug

    @property
    def data_dir(self) -> Path:
        return self.root / "data" / "daily_qa"

    @property
    def product_data_dir(self) -> Path:
        return self.data_dir / self.slug

    @property
    def lock_path(self) -> Path:
        return self.product_data_dir / "run.lock"

    @property
    def output_dir(self) -> Path:
        return self.root / "output" / "daily_qa"

    @property
    def snapshot_dir(self) -> Path:
        """제품의 스냅샷 뿌리. SRS 는 `srs/`, 이슈는 `issues/` 아래에 있다."""
        return self.data_dir / "snapshots" / self.slug

    @property
    def srs_snapshot_dir(self) -> Path:
        return self.snapshot_dir / "srs"

    @property
    def issue_snapshot_dir(self) -> Path:
        return self.snapshot_dir / "issues"

    @property
    def legacy_snapshot_dir(self) -> Path | None:
        """개편 전 SRS 스냅샷 위치(`data/daily_qa/snapshots/*.json`). 옛 제품에만 있다."""
        return self.data_dir / "snapshots" if self.is_legacy_product else None

    @property
    def product_workspace_dir(self) -> Path:
        return self.workspace_dir / self.slug

    @property
    def db_path(self) -> Path:
        return self.root / "data" / "app.db"

    def state_key(self, name: str) -> str:
        return f"{self.slug}:{name}"


def _split_recipients(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.replace(";", ",").split(",") if part.strip())


def configured_products(settings: Settings | None = None) -> list[str]:
    """예약·대시보드가 다루는 제품 이름 목록 (`daily_qa.products`, 없으면 `daily_qa.product`)."""
    settings = settings or get_settings()
    raw = settings.get("daily_qa.products", None)
    if isinstance(raw, str):
        raw = [raw]
    products = [str(item).strip() for item in (raw or []) if str(item).strip()]
    return products or [str(settings.get("daily_qa.product", "VXvue") or "VXvue")]


def _intelligence(get) -> IntelligenceSettings:
    defaults = IntelligenceSettings()
    values = {}
    for name, default in defaults.__dict__.items():
        raw = get(f"daily_qa.intelligence.{name}", default)
        values[name] = type(default)(raw if raw is not None else default)
    if not values["audit_model"]:
        values["audit_model"] = str(get("ai.claude.models.light", "") or "")
    values["audit_batch_size"] = max(1, values["audit_batch_size"])
    return IntelligenceSettings(**values)


def load(settings: Settings | None = None, product: str | None = None) -> DailyQaSettings:
    settings = settings or get_settings()
    secrets = settings.secrets
    get = settings.get
    workspace = str(get("daily_qa.workspace_dir", "") or "").strip()
    weekly_day = str(get("daily_qa.weekly_day", "mon") or "mon").strip().lower()[:3]
    if weekly_day not in WEEKDAYS:
        raise ValueError(f"daily_qa.weekly_day 는 {', '.join(WEEKDAYS)} 중 하나여야 합니다: {weekly_day}")
    recipients = _split_recipients(str(secrets.daily_qa_email_to or "")) or _split_recipients(
        str(secrets.notify_email_to or "")
    )
    legacy = str(get("daily_qa.product", "VXvue") or "VXvue")
    chosen = product or configured_products(settings)[0]
    fallback = {
        "project_id": str(get("daily_qa.polarion.project_id", "") or ""),
        "srs_query": str(get("daily_qa.polarion.srs_query", "type:srs") or "type:srs"),
        "issue_query": str(get("daily_qa.polarion.issue_query", "type:issue") or "type:issue"),
    }
    profile = load_profile(chosen, settings.root, fallback)
    # 메일을 받는 사람의 브라우저가 여는 주소다. 앱의 루프백 주소(app_self_url)는 쓸 수 없다.
    review_base = str(get("daily_qa.review_base_url", "") or "").strip()
    return DailyQaSettings(
        root=settings.root,
        enabled=bool(get("daily_qa.enabled", True)) and profile.enabled,
        product=profile.product,
        workspace_dir=Path(workspace).expanduser() if workspace else Path.home() / ".qa-daily-workspace",
        claude_command=str(get("daily_qa.claude_command", "claude") or "claude"),
        claude_token=str(secrets.claude_code_oauth_token or ""),
        task_timeout_seconds=int(get("daily_qa.task_timeout_seconds", 900) or 900),
        max_tasks_per_run=int(get("daily_qa.max_tasks_per_run", 30) or 30),
        batch_size=max(1, int(get("daily_qa.batch_size", 5) or 5)),
        tc_candidate_limit=max(1, int(get("daily_qa.tc_candidate_limit", 15) or 15)),
        weekly_day=weekly_day,
        polarion=PolarionSettings(
            host=str(secrets.polarion_host or "").rstrip("/"),
            token=str(secrets.polarion_token or ""),
            project_id=profile.project_id,
            srs_query=profile.srs_query,
            issue_query=profile.issue_query,
            verify_ssl=bool(get("daily_qa.polarion.verify_ssl", True)),
            timeout_seconds=int(get("daily_qa.polarion.timeout_seconds", 90) or 90),
            page_size=int(get("daily_qa.polarion.page_size", 100) or 100),
            request_interval_seconds=float(get("daily_qa.polarion.request_interval_seconds", 0.15) or 0),
        ),
        email_to=recipients,
        review_base_url=review_base.rstrip("/"),
        profile=profile,
        legacy_product=legacy,
        intelligence=_intelligence(get),
    )

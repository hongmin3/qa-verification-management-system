"""`config.yaml` 의 `daily_qa` 절과 비밀 설정을 한 곳에서 읽는다."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings, get_settings

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

    @property
    def data_dir(self) -> Path:
        return self.root / "data" / "daily_qa"

    @property
    def output_dir(self) -> Path:
        return self.root / "output" / "daily_qa"

    @property
    def snapshot_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def db_path(self) -> Path:
        return self.root / "data" / "app.db"


def _split_recipients(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.replace(";", ",").split(",") if part.strip())


def load(settings: Settings | None = None) -> DailyQaSettings:
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
    # 메일을 받는 사람의 브라우저가 여는 주소다. 앱의 루프백 주소(app_self_url)는 쓸 수 없다.
    review_base = str(get("daily_qa.review_base_url", "") or "").strip()
    return DailyQaSettings(
        root=settings.root,
        enabled=bool(get("daily_qa.enabled", True)),
        product=str(get("daily_qa.product", "VXvue") or "VXvue"),
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
            project_id=str(get("daily_qa.polarion.project_id", "") or ""),
            srs_query=str(get("daily_qa.polarion.srs_query", "type:srs") or "type:srs"),
            issue_query=str(get("daily_qa.polarion.issue_query", "type:issue") or "type:issue"),
            verify_ssl=bool(get("daily_qa.polarion.verify_ssl", True)),
            timeout_seconds=int(get("daily_qa.polarion.timeout_seconds", 90) or 90),
            page_size=int(get("daily_qa.polarion.page_size", 100) or 100),
            request_interval_seconds=float(get("daily_qa.polarion.request_interval_seconds", 0.15) or 0),
        ),
        email_to=recipients,
        review_base_url=review_base.rstrip("/"),
    )

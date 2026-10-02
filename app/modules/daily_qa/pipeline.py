"""QA Intelligence 점검 한 번의 실행 순서 (SPEC REQ-DAILY-001, specs/qa-intelligence.md).

    잠금 → 사전 점검 → SRS 수집 → 이슈 수집 → TC 색인 → 스냅샷 저장 + 변경 이벤트 저장
    → AI 없는 계산(삭제 SRS 참조, 주 1회 사양–TC 연결 점검)
    → 분석 대상 고르기 → 후보 압축 → Claude Skill → 근거 검증 → Finding 저장
    → (주 1회) 매뉴얼 누락 후보 점검 → 초안 Excel → 실행 결과 → 메일

변경이 없으면 AI 를 부르지 않는다(NFR-QAINTEL-001). 수집이 믿을 수 없으면 스냅샷 기준을 옮기지
않는다(REQ-QAINTEL-009). AI 만 실패하면 이벤트를 남겨 다음 실행에서 다시 분석한다(REQ-QAINTEL-006).
Claude 사용량 한도에 걸리면 남은 AI 작업을 멈추고 초기화 시각을 기록한다(REQ-QAINTEL-025).

외부와 닿는 부분(Polarion, Claude, 메일, 규칙 확인, 지식 파일 위치, 사양서 조각)은 모두 인자로 바꿔
끼울 수 있다. 테스트는 가짜를 넣고, 운영은 `scripts/run_daily_qa.py` 가 기본값으로 부른다.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.core.claude_cli import login_status as claude_login_status
from app.modules.daily_qa import claude_limits, issue_audit, packages, report, rules, snapshots
from app.modules.daily_qa.agent_runner import ClaudeRunner, FakeRunner
from app.modules.daily_qa.change_events import (
    ANALYSIS_ORDER,
    ENTITY_SRS,
    FIXED_ISSUE,
    ISSUE_AUDIT,
    ISSUE_AUDIT_TARGET,
    SPEC_COVERAGE,
    SRS_CREATED,
    SRS_REMOVED,
    SRS_UPDATED,
    TC_CHECK,
    TC_DRAFT,
    ChangeEvent,
    detect_issue_events,
    detect_srs_events,
    group_targets,
)
from app.modules.daily_qa.checklist_xlsx import coverage_rows_from_finding, draft_rows_from_finding, write_draft
from app.modules.daily_qa.collector import CollectionError, collect_issues, collect_srs, fetch_all_comments
from app.modules.daily_qa.evidence_validation import draft_key, validate_finding
from app.modules.daily_qa.intelligence import ANALYSIS_SKILLS, ON_DEMAND_SKILLS, AnalysisTarget, Corpus, build_item, build_tasks
from app.modules.daily_qa.polarion import PolarionError, ReadOnlyPolarionClient
from app.modules.daily_qa.schema import (
    ANALYSIS_SKILL_NAMES,
    SKILL_ALIASES,
    SKILL_COVERAGE,
    SKILL_E,
    SKILL_F,
    SKILL_TC_DRAFT,
    ResultFileError,
    parse_result,
)
from app.modules.daily_qa.settings import WEEKDAYS, DailyQaSettings
from app.modules.daily_qa.srs_snapshot import diff_snapshots
from app.modules.daily_qa.store import RUN_STATUSES, DailyQaStore
from app.modules.daily_qa.tc_index import build_index
from app.modules.daily_qa.workspace import WorkspaceError, prepare, write_context, write_task_input

logger = logging.getLogger("regression_analyzer")

LOCK_STALE_SECONDS = 6 * 3600
#: 상태 키 (제품 slug 가 앞에 붙는다: `vxvue:claude_limit`, REQ-QAINTEL-023).
#: `manual_check_due` 는 매뉴얼 점검이 자동이던 때의 키다. 이제 읽지도 쓰지도 않는다 (REQ-QAINTEL-034).
STATE_MANUAL_DUE = "manual_check_due"
STATE_CLAUDE_LIMIT = "claude_limit"
STATE_CATCHUP_AFTER = "catchup_after"
#: 개편 전 전역 상태 키. 옛 제품만 한 번 읽는다.
LEGACY_STATE_B_PENDING = "spec_change_pending"
#: 사람이 버튼으로 요청하는 실행 (REQ-QAINTEL-016·032·034). `--on-demand` 값이다.
ON_DEMAND_TC_CHECK = "tc-check"
ON_DEMAND_TC_DRAFT = "tc-draft"
ON_DEMAND_MANUAL = "manual-check"
ON_DEMAND_KINDS = (ON_DEMAND_TC_CHECK, ON_DEMAND_TC_DRAFT, ON_DEMAND_MANUAL)
ON_DEMAND_LABELS = {ON_DEMAND_TC_CHECK: "TC 점검", ON_DEMAND_TC_DRAFT: "검증 TC 초안", ON_DEMAND_MANUAL: "매뉴얼 점검"}
ON_DEMAND_REFUSALS = {
    ON_DEMAND_TC_CHECK: "사양 변경 분석에서만 TC 점검을 할 수 있습니다.",
    ON_DEMAND_TC_DRAFT: "수정 완료 이슈 분석에서만 검증 TC 초안을 만들 수 있습니다.",
}
#: 요청 실행이 저장하는 Finding 의 분석 종류.
ON_DEMAND_STORED_KIND = {ON_DEMAND_TC_CHECK: TC_CHECK, ON_DEMAND_TC_DRAFT: TC_DRAFT}
#: AI 단계 키 (단계 상태에 쓴다). 분석 종류 이름을 그대로 쓴다.
AI_STAGES = (*ANALYSIS_ORDER, "F")
SRS_REMOVED_ANALYSIS = "SRS_REMOVED"


@dataclass
class Inputs:
    """지식 폴더에서 읽을 파일. 기본값은 수집된 지식 사본(`data/product_knowledge`)이다."""

    tc_paths: list[Path] = field(default_factory=list)
    manuals: dict[str, str] = field(default_factory=dict)
    checklist_template: Path | None = None


class RunLocked(RuntimeError):
    pass


class _Lock:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if lock_is_stale(self.path):
            self.path.unlink(missing_ok=True)
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RunLocked(f"다른 QA Agent 점검이 실행 중입니다 ({self.path}).") from exc
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        return self

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)


def lock_is_stale(path: Path) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS


def is_running(cfg: DailyQaSettings) -> bool:
    """실행 잠금이 있고 6시간이 지나지 않았는가 (REQ-QAINTEL-021)."""
    return cfg.lock_path.exists() and not lock_is_stale(cfg.lock_path)


def default_inputs(product: str, root: Path, checklist_marker: str = "") -> Inputs:
    from app.core.product_knowledge import KIND_MANUAL, KIND_TESTCASE, collected_assets, collected_path

    tc_paths = [collected_path(product, asset, root) for asset in collected_assets(product, KIND_TESTCASE, root)]
    template = next((path for path in tc_paths if checklist_marker and checklist_marker in path.name), None)
    manuals: dict[str, str] = {}
    for asset in collected_assets(product, KIND_MANUAL, root):
        normalized = asset.get("normalized_path") or ""
        path = (root / normalized) if normalized and not Path(normalized).is_absolute() else Path(normalized)
        if normalized and not path.is_file():
            from app.core.product_knowledge import product_dir

            path = product_dir(product, root) / normalized
        if normalized and path.is_file():
            manuals[f"{Path(asset['file_name']).stem}.txt"] = path.read_text(encoding="utf-8", errors="replace")
    return Inputs(tc_paths=[path for path in tc_paths if path.is_file()], manuals=manuals, checklist_template=template)


def default_spec_chunks(product: str, cfg: DailyQaSettings) -> tuple[list, dict, list[str]]:
    """Knowledge 에 등록한 사양서 조각 (기존 문서 로더 재사용, REQ-QAINTEL-011). 실패는 이름만 남긴다."""
    from app.core.knowledge_documents import load_specification_chunks
    from app.core.storage import Storage

    failures: list[dict] = []
    documents = Storage(db_path=cfg.db_path).active_documents("specification", product)
    chunks, _, labels = load_specification_chunks(documents, failures=failures)
    return chunks, labels, [str(item.get("name") or item.get("id") or "") for item in failures]


def _stage(status: str, note: str = "", **counts) -> dict:
    return {"status": status, "note": note, **({"counts": counts} if counts else {})}


def _is_weekly(today: datetime, weekly_day: str) -> bool:
    return WEEKDAYS[today.weekday()] == weekly_day


def run_daily(
    cfg: DailyQaSettings,
    *,
    today: datetime | None = None,
    dry_run: bool = False,
    force_weekly: bool = False,
    polarion_factory: Callable[[], ReadOnlyPolarionClient] | None = None,
    runner=None,
    inputs: Inputs | None = None,
    rules_state: rules.RulesState | None = None,
    send_email: Callable[[str, str, str, tuple[str, ...]], dict] | None = None,
    store: DailyQaStore | None = None,
    spec_chunks_loader: Callable[[], tuple[list, dict, list[str]]] | None = None,
    trigger: str = "scheduled",
    since: date | None = None,
    until: date | None = None,
    issue_audit: bool = False,
    on_demand: str = "",
    finding_id: int | None = None,
) -> dict:
    """`since`·`until` 을 주면 그 기간의 변경을 분석한다 (REQ-QAINTEL-027).

    기준은 `since` 날짜(또는 그 전 가장 가까운) 스냅샷이다. `until` 이 오늘이면 Polarion 을 새로 읽고,
    지난 날이면 그날(또는 그 전 가장 가까운) 저장 스냅샷을 쓴다. 지난 날 기간은 스냅샷을 저장하지 않는다.
    """
    today = today or datetime.now(timezone.utc).astimezone()
    store = store or DailyQaStore(cfg.db_path)
    with _Lock(cfg.lock_path):
        run_id = f"{today.strftime('%Y%m%d-%H%M%S')}-{cfg.slug}"
        store.create_run(run_id, dry_run, cfg.slug)
        run = None
        try:
            run = _Run(cfg, store, run_id, today, dry_run, force_weekly, polarion_factory, runner, inputs,
                       rules_state, spec_chunks_loader, trigger, since, until, issue_audit=issue_audit,
                       on_demand=on_demand, finding_id=finding_id)
            outcome = run.execute()
        except Exception as exc:  # 예상 못 한 오류도 실행 기록과 메일에 남긴다
            logger.exception("daily_qa_crashed run=%s", run_id)
            summary = report.summarize([], 0)
            if run is not None:
                # 멈추기 전에 쓴 Claude 사용량도 비용 대시보드에 남긴다 (REQ-QAINTEL-026).
                summary.update({"product": cfg.slug, "trigger": trigger, "claude_calls": run.claude_calls,
                                "token_usage": run.token_usage})
            outcome = {"stages": {"preflight": _stage("failed", f"예상 못 한 오류: {type(exc).__name__}")},
                       "status": "FAILED", "summary": summary, "rules_warning": ""}
        store.finish_run(run_id, outcome["status"], outcome["stages"], outcome["summary"])
        if on_demand:
            # 버튼으로 요청한 실행은 누른 사람이 화면에서 결과를 본다. 메일을 보내지 않는다.
            store.set_email_status(run_id, "skipped")
            outcome.update({"run_id": run_id, "email": {"status": "skipped"}})
            return outcome
        base = cfg.review_base_url
        review_url = f"{base}/qa-agent/runs/{run_id}" if base else f"/qa-agent/runs/{run_id}"
        subject, text, body = report.build_email(
            run_id, RUN_STATUSES[outcome["status"]], outcome["stages"], outcome["summary"], review_url, outcome["rules_warning"],
            product=cfg.product,
        )
        sender = send_email or _default_sender
        email = sender(subject, text, body, cfg.email_to)
        store.set_email_status(run_id, str(email.get("status", "")))
        outcome.update({"run_id": run_id, "email": email})
        return outcome


def _default_sender(subject: str, text: str, body: str, recipients: tuple[str, ...]) -> dict:
    from app.core.notifier import send_report

    return send_report(subject, text, body, recipients)


def _load_json_state(store: DailyQaStore, key: str, default):
    raw = store.get_state(key)
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except ValueError:
        logger.warning("daily_qa_state_unreadable key=%s", key)
        return default
    return value if isinstance(value, type(default)) else default


def active_limit(store: DailyQaStore, cfg: DailyQaSettings, now: datetime | None = None) -> claude_limits.LimitInfo | None:
    info = claude_limits.load(store.get_state(cfg.state_key(STATE_CLAUDE_LIMIT)))
    return info if info and info.active(now, cfg.claude_token) else None


def _tc_key(tc_ref: dict | None) -> tuple:
    if not tc_ref:
        return ()
    workbook, sheet = tc_ref.get("workbook", ""), tc_ref.get("sheet", "")
    return (workbook, sheet, tc_ref.get("tc_id")) if tc_ref.get("tc_id") else (workbook, sheet, int(tc_ref.get("row") or 0))


def _finding_key(item: dict) -> tuple:
    key = (item["subject"], item["verdict"], _tc_key(item.get("tc_ref")), draft_key(item))
    if item.get("analysis_type") in (TC_CHECK, TC_DRAFT):
        return (*key, item["analysis_type"], (item.get("sections") or {}).get("source_finding"))
    return key


class _OpenFindings:
    """Finding 저장 창구. 같은 기록인지 가리는 기준은 점검·대상·판정·대상 TC 위치·초안 제목이다 (REQ-DAILY-018).

    대상 TC 를 기준에 넣어, 같은 SRS 를 가리키는 TC 가 여럿이면 TC 마다 Finding 이 남는다
    (MISMATCH 4-5). 이름을 바꾼 Skill 은 옛 이름의 Finding 과도 겹침을 본다(`SKILL_ALIASES`).
    열린 Finding 목록은 점검마다 한 번만 읽는다. 시험 실행은 저장하지 않는다.
    """

    OPEN = ("PENDING", "APPROVED", "NEED_EVIDENCE")

    def __init__(self, store: DailyQaStore, dry_run: bool = False, product: str = "", include_legacy: bool = False) -> None:
        self.store = store
        self.dry_run = dry_run
        self.product = product
        self.include_legacy = include_legacy
        self._keys: dict[str, set[tuple]] = {}

    def _open_keys(self, skill: str) -> set[tuple]:
        if skill not in self._keys:
            keys: set[tuple] = set()
            for name in (skill, *SKILL_ALIASES.get(skill, ())):
                for status in self.OPEN:
                    for item in self.store.list_findings(skill=name, review_status=status, limit=1_000_000,
                                                         product=self.product or None, include_legacy=self.include_legacy):
                        keys.add(_finding_key(item))
            self._keys[skill] = keys
        return self._keys[skill]

    def save(self, run_id: str, skill: str, task_id: str, finding: dict, dedupe: bool = True) -> int:
        """저장한 Finding 번호. 저장하지 않았으면 0."""
        if self.dry_run:
            return 0
        if dedupe:
            key = _finding_key(finding)
            keys = self._open_keys(skill)
            if key in keys:
                return 0
            keys.add(key)
        return self.store.add_finding(run_id, skill, task_id, finding, self.product)


def _failure_reason(entry: dict) -> str:
    for attempt in ("attempt2", "attempt1"):
        info = entry.get(attempt) or {}
        text = " ".join(str(info.get(name) or "") for name in ("error", "result_error")).strip()
        if text:
            return " ".join(text.split())[:120]
    return ""


@dataclass
class _TaskOutcome:
    task: packages.Task
    ok: bool
    finding_ids: dict[str, list[int]] = field(default_factory=dict)
    error: str = ""
    limit: claude_limits.LimitInfo | None = None


class _Run:
    def __init__(self, cfg, store, run_id, today, dry_run, force_weekly, polarion_factory, runner, inputs, rules_state,
                 spec_chunks_loader, trigger, since=None, until=None, issue_audit=False, on_demand="", finding_id=None):
        self.cfg: DailyQaSettings = cfg
        self.store: DailyQaStore = store
        self.run_id = run_id
        self.today = today
        self.dry_run = dry_run
        self.force_weekly = force_weekly
        self.polarion_factory = polarion_factory
        self.runner = runner
        self.inputs = inputs
        self.rules_state = rules_state
        self.spec_chunks_loader = spec_chunks_loader
        self.trigger = trigger
        self.profile = cfg.product_profile
        self.stages: dict[str, dict] = {}
        self.all_findings: list[dict] = []
        self.question_count = 0
        self.claude_calls = 0
        #: Claude CLI 가 돌려준 사용량 합계. 비용 대시보드·하루 토큰 한도가 읽는다 (REQ-QAINTEL-026).
        self.token_usage: dict = {"input_tokens": 0, "output_tokens": 0, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
        self.out_dir = cfg.output_dir / run_id
        self.run_date = today.strftime("%Y-%m-%d")
        self.audit: dict = {"run_id": run_id, "product": cfg.slug, "dry_run": dry_run, "trigger": trigger, "tasks": []}
        self.snapshots = snapshots.for_settings(cfg)
        self.findings_box = _OpenFindings(store, dry_run=dry_run, product=cfg.slug, include_legacy=cfg.is_legacy_product)
        self.limit: claude_limits.LimitInfo | None = None
        self.workspace = None
        self.context_written = False
        self.baseline_created = False
        #: 이번 실행이 저장한 새 이벤트 (이미 있던 이벤트는 빼고 센다).
        self.new_events: list[ChangeEvent] = []
        #: 이번 실행이 찾은 이벤트 전부 (`change_events.json`).
        self.detected_events: list[ChangeEvent] = []
        #: 이번 실행이 저장한 이벤트 번호 (지난 날 기간 실행은 이것만 분석한다).
        self.stored_event_ids: list[int] = []
        #: 종류별 비교 기준 스냅샷 날짜 (같은 전이가 다른 날 또 일어나면 새 이벤트, REQ-QAINTEL-006).
        self.basis: dict[str, str] = {}
        self.targets_found = 0
        #: 이번 실행에 남은 AI 작업 수. 분석과 매뉴얼 점검이 나눠 쓴다 (NFR-DAILY-001).
        self.budget = cfg.max_tasks_per_run
        local_today = today.astimezone(claude_limits.KST).date() if today.tzinfo else today.date()
        #: 기간 분석 (REQ-QAINTEL-027). 종료일이 지난 날이면 새로 수집하지 않고 저장 스냅샷끼리 비교한다.
        self.since: date | None = since
        self.until: date | None = until if until and until < local_today else None
        self.period = since is not None or self.until is not None
        #: 현재 상태 기준 이슈 점검 (REQ-QAINTEL-030). 점검 작업은 따로 정한 모델로 부른다.
        self.issue_audit = issue_audit
        self.audit_runner = None
        self.audit_summary: dict | None = None
        self._audit_left: list[dict] | None = None
        #: 사람이 버튼으로 요청한 실행. 이때만 TC·매뉴얼을 AI 에 보낸다 (REQ-QAINTEL-016·032·034).
        self.on_demand = on_demand
        self.finding_id = finding_id
        self.on_demand_events: list[dict] | None = None
        self.only_kind: str | None = None
        self.source_finding: dict | None = None
        if self.period:
            self.audit["period"] = {"since": since.isoformat() if since else "", "until": (until or local_today).isoformat()}

    # -- 흐름 --------------------------------------------------------------
    def execute(self) -> dict:
        if self.on_demand:
            return self._execute_on_demand()
        if self.issue_audit:
            return self._execute_audit()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._preflight()
        client = self._client() if self.until is None else self._stored_only()
        srs = self._collect_srs(client)
        issues = self._collect_issues(client)
        tc_rows = self._tc_index()
        self._persist(srs, issues)
        srs_items = srs.items if srs else self._saved_srs()
        issue_items = issues.items if issues else self._saved_issues()
        self._deterministic(srs, srs_items, tc_rows)
        self._analyses(client, srs_items, issue_items, tc_rows)
        self.stages["F"] = _stage("not_due", "매뉴얼 점검은 자동으로 돌지 않습니다. 사람이 요청할 때만 돕니다(`--on-demand manual-check`).")
        self._draft_excel()
        return self._finish()

    def _preflight(self) -> None:
        cfg = self.cfg
        self.rules_state = self.rules_state or rules.check(cfg.product, cfg.root, self.profile.supported_rules_rev)
        marker = (self.profile.checklist or {}).get("template_name_contains") or ""
        self.inputs = self.inputs or default_inputs(cfg.product, cfg.root, marker)
        self.ai_block = ""
        if not self.rules_state.ok:
            self.ai_block = "rules"
        self.claude_login = None
        if not self.dry_run and not cfg.claude_token and self.runner is None:
            # 토큰이 없으면 이 PC 의 Claude CLI 로그인을 쓴다(담당자 PC). 서버처럼 로그인이 없으면 AI 를 막는다.
            login = claude_login_status(cfg.claude_command)
            if login["logged_in"]:
                self.claude_login = login
                self.audit["claude_auth"] = {"mode": "login", "method": login["method"], "subscription": login["subscription"]}
            else:
                self.ai_block = self.ai_block or "token"
        if not self.dry_run:
            self.limit = active_limit(self.store, cfg, self.today)
            if self.limit is not None and self.limit.kind == claude_limits.KIND_AUTH and self.claude_login:
                # 로그인 방식은 토큰 지문이 늘 같아 인증 실패 기록이 풀리지 않는다. 로그인이 다시 확인되면 푼다.
                self.store.set_state(cfg.state_key(STATE_CLAUDE_LIMIT), "")
                self.limit = None
            if self.limit is not None:
                self.ai_block = self.ai_block or "limit"
        try:
            self.workspace = prepare(cfg.product_workspace_dir, cfg.root, self.run_id, self.rules_state.guide_path,
                                     self.rules_state.prompt_path, cfg.product, self.profile.skills_dir, self.profile.product_skill)
        except WorkspaceError as exc:
            self.ai_block = self.ai_block or "workspace"
            self.stages["preflight"] = _stage("failed", str(exc))
        if "preflight" not in self.stages:
            notes = {"rules": self.rules_state.reason,
                     "token": "CLAUDE_CODE_OAUTH_TOKEN 이 없고 Claude CLI 도 로그인돼 있지 않아 AI 단계를 건너뜁니다.",
                     "limit": self.limit.describe() + (" 한도가 풀린 뒤 다시 요청하세요." if self.on_demand else
                                                       " AI 분석은 대기로 남깁니다.") if self.limit else ""}
            note = notes.get(self.ai_block, "")
            if not self.ai_block and self.claude_login:
                note = f"Claude CLI 로그인({self.claude_login['method'] or '로그인'})으로 부릅니다."
            self.stages["preflight"] = _stage("ok" if not self.ai_block else "partial", note)
        if self.runner is None:
            self.runner = FakeRunner() if self.dry_run else ClaudeRunner(cfg.claude_command, cfg.claude_token, cfg.task_timeout_seconds)
            if not self.dry_run:
                # 이슈 정합성 점검은 요약 카드 판정이라 가벼운 모델로 부른다 (REQ-QAINTEL-030 순서 6).
                self.audit_runner = ClaudeRunner(cfg.claude_command, cfg.claude_token, cfg.task_timeout_seconds,
                                                 model=cfg.intelligence.audit_model)
                self.audit["audit_model"] = cfg.intelligence.audit_model

    def _client(self):
        cfg = self.cfg
        if not (cfg.polarion.configured or self.polarion_factory):
            reason = "POLARION_HOST / POLARION_TOKEN 이 설정되지 않았습니다."
            self.stages["collect_srs"] = _stage("skipped", reason)
            self.stages["collect_issues"] = _stage("skipped", "Polarion 연결이 없어 건너뜁니다.")
            return None
        try:
            return self.polarion_factory() if self.polarion_factory else ReadOnlyPolarionClient(cfg.polarion)
        except PolarionError as exc:
            self.stages["collect_srs"] = _stage("failed", str(exc))
            self.stages["collect_issues"] = _stage("failed", str(exc))
            return None

    def _stored_only(self):
        note = f"기간 분석: {self.until.isoformat()} 저장 스냅샷을 씁니다(Polarion 을 새로 읽지 않음)"
        self.stages["collect_srs"] = _stage("ok", note)
        self.stages["collect_issues"] = _stage("ok", note)
        return None

    def _baseline(self, kind: str) -> Path | None:
        """비교 기준 스냅샷. 기간 분석이면 시작일(또는 그 전 가장 가까운) 스냅샷이다."""
        if self.since is not None:
            current_date = (self.until.isoformat() if self.until else self.run_date)
            found = self.snapshots.at_or_before(kind, self.since.isoformat())
            if found is None:
                # 시작일 전 스냅샷이 없으면 가장 오래된 스냅샷을 기준으로 삼는다(그보다 앞선 변경은 알 수 없다).
                return self.snapshots.oldest_before(kind, current_date)
            return found if found.stem < current_date else self.snapshots.previous(kind, current_date)
        return self.snapshots.previous(kind, self.run_date)

    def _stored_collection(self, kind: str):
        """종료일이 지난 날인 기간 분석: 그날 저장 스냅샷을 오늘 수집처럼 쓴다."""
        from app.modules.daily_qa.collector import IssueCollection, SrsCollection

        current = self.snapshots.at_or_before(kind, self.until.isoformat())
        if current is None:
            key = "collect_srs" if kind == snapshots.KIND_SRS else "collect_issues"
            self.stages[key] = _stage("failed", f"{self.until.isoformat()} 이전 저장 스냅샷이 없습니다.")
            return None
        items = self.snapshots.load(current) or []
        base_path = (self.snapshots.at_or_before(kind, self.since.isoformat()) or self.snapshots.oldest_before(kind, current.stem))             if self.since else self.snapshots.previous(kind, current.stem)
        if base_path is not None and base_path.stem >= current.stem:
            base_path = self.snapshots.previous(kind, current.stem)
        previous = self.snapshots.load(base_path)
        self.basis[kind] = base_path.stem if base_path is not None else ""
        if kind == snapshots.KIND_SRS:
            return SrsCollection(items=items, previous=previous, diff=diff_snapshots(previous, items))
        return IssueCollection(items=items, previous=previous, previous_collected_at=self.snapshots.meta(base_path).get("collected_at", ""))

    def _collect_srs(self, client):
        if self.until is not None:
            return self._stored_collection(snapshots.KIND_SRS)
        if client is None:
            return None
        previous_path = self._baseline(snapshots.KIND_SRS)
        self.basis[snapshots.KIND_SRS] = previous_path.stem if previous_path is not None else ""
        try:
            result = collect_srs(client, self.profile, self.snapshots.load(previous_path))
        except (PolarionError, CollectionError) as exc:
            self.stages["collect_srs"] = _stage("failed", str(exc))
            return None
        diff = result.diff
        (self.out_dir / "srs_diff.json").write_text(json.dumps(diff.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        note = "기준 스냅샷만 저장(비교 대상 없음)" if diff.baseline_only else ""
        if self.dry_run:
            note = "dry-run: 스냅샷을 저장하지 않았습니다"
        self.stages["collect_srs"] = _stage("ok", note, total=len(result.items), added=len(diff.added),
                                            removed=len(diff.removed), modified=len(diff.modified))
        return result

    def _collect_issues(self, client):
        if self.until is not None:
            return self._stored_collection(snapshots.KIND_ISSUES)
        if client is None:
            return None
        intel = self.cfg.intelligence
        previous_path = self._baseline(snapshots.KIND_ISSUES)
        self.basis[snapshots.KIND_ISSUES] = previous_path.stem if previous_path is not None else ""
        try:
            result = collect_issues(client, self.profile, self.snapshots.load(previous_path), self.snapshots.meta(previous_path),
                                    comment_fetch_limit=intel.comment_fetch_limit, drop_ratio=intel.issue_drop_ratio)
        except (PolarionError, CollectionError) as exc:
            self.stages["collect_issues"] = _stage("failed", str(exc))
            return None
        notes = []
        if result.baseline_only:
            notes.append("기준 스냅샷만 저장(비교 대상 없음)")
        if result.comments_failed:
            notes.append(f"댓글 읽기 실패 {len(result.comments_failed)}건(다음 실행에서 다시 읽음)")
        if result.comments_deferred:
            notes.append(f"댓글 읽기 상한으로 {result.comments_deferred}건 다음 실행으로 미룸")
        if self.dry_run:
            notes.append("dry-run: 스냅샷을 저장하지 않았습니다")
        self.stages["collect_issues"] = _stage("ok", " · ".join(notes), total=len(result.items),
                                               comments_fetched=result.comments_fetched, comments_failed=len(result.comments_failed))
        return result

    def _tc_index(self):
        tc_rows, tc_errors = build_index(self.inputs.tc_paths)
        if not self.inputs.tc_paths:
            self.stages["tc_index"] = _stage("failed", "수집된 TC 파일이 없습니다. 지식 폴더 동기화를 확인하세요.")
        else:
            self.stages["tc_index"] = _stage("partial" if tc_errors else "ok", "; ".join(tc_errors),
                                             files=len(self.inputs.tc_paths), rows=len(tc_rows))
        return tc_rows

    def _saved(self, kind: str):
        """수집하지 못했을 때 대신 쓸 저장 스냅샷. 지난 날 기간 실행은 종료일 뒤 스냅샷을 쓰지 않는다."""
        if self.until is not None:
            return self.snapshots.at_or_before(kind, self.until.isoformat())
        return self.snapshots.latest(kind)

    def _saved_srs(self) -> list[dict]:
        latest = self._saved(snapshots.KIND_SRS)
        if latest and "collect_srs" in self.stages:
            note = self.stages["collect_srs"].get("note", "")
            self.stages["collect_srs"]["note"] = (note + f" · 저장된 {latest.stem} 스냅샷 사용").strip(" ·")
        return self.snapshots.load(latest) or []

    def _saved_issues(self) -> list[dict]:
        return self.snapshots.load(self._saved(snapshots.KIND_ISSUES)) or []

    # -- 스냅샷·이벤트 (REQ-QAINTEL-006) ------------------------------------------
    def _persist(self, srs, issues) -> None:
        intel = self.cfg.intelligence
        events: dict[str, list[ChangeEvent]] = {}
        if srs is not None:
            events[snapshots.KIND_SRS] = detect_srs_events(srs.diff)
            self.baseline_created |= srs.baseline_only
        if issues is not None:
            events[snapshots.KIND_ISSUES] = detect_issue_events(
                issues.previous, issues.items, previous_collected_at=issues.previous_collected_at,
                comment_min_chars=intel.comment_min_chars, noise_patterns=self.profile.comment_noise_patterns)
            self.baseline_created |= issues.baseline_only
        for kind, kind_events in events.items():
            for event in kind_events:
                event.basis = self.basis.get(kind, "")
        collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stored: dict[str, int] = {}
        for kind, result in ((snapshots.KIND_SRS, srs), (snapshots.KIND_ISSUES, issues)):
            if result is None:
                continue
            kind_events = events.get(kind, [])
            if self.dry_run:
                self.detected_events.extend(kind_events)
                self.new_events.extend(kind_events)
                continue
            stage_key = "collect_srs" if kind == snapshots.KIND_SRS else "collect_issues"
            try:
                if self.until is None:
                    self.snapshots.save(kind, self.run_date, result.items, collected_at)
            except OSError as exc:
                self.stages[stage_key] = _stage("failed", f"스냅샷을 저장하지 못했습니다: {type(exc).__name__}")
                continue
            rows = [event.as_dict() for event in kind_events]
            try:
                ids = self.store.add_events(self.cfg.slug, self.run_id, rows, skip_known_after=self.period)
            except Exception as exc:  # 이벤트를 못 남기면 스냅샷을 되돌려 다음 실행이 같은 변경을 다시 찾게 한다
                if self.until is None:
                    self.snapshots.discard(kind, self.run_date)
                self.stages[stage_key] = _stage("failed", f"변경 이벤트를 저장하지 못해 스냅샷을 되돌렸습니다: {type(exc).__name__}")
                continue
            # 스냅샷·이벤트 저장이 성공한 종류만 이번 실행이 찾은 이벤트로 남긴다(실패하면 다음 실행이 다시 찾는다).
            self.detected_events.extend(kind_events)
            stored[kind] = len(ids)
            self.stored_event_ids.extend(ids)
            # 이미 있던 이벤트(같은 기준으로 다시 돈 실행, 기간 실행이 이미 아는 끝 상태)는 새 이벤트로 세지 않는다.
            self.new_events.extend(event for event, row in zip(kind_events, rows) if row.get("id"))
        if not self.dry_run:
            self._import_legacy_pending()
        (self.out_dir / "change_events.json").write_text(
            json.dumps([event.as_dict() for event in self.detected_events], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        required = sum(event.analysis_required for event in self.new_events)
        by_type: dict[str, int] = {}
        for event in self.new_events:
            by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
        note = "" if required else ("AI 분석이 필요한 변경이 없습니다" if self.new_events else "")
        known = len(self.detected_events) - len(self.new_events) if not self.dry_run else 0
        if known:
            note = (note + f" · 이미 저장된 변경 {known}건은 다시 넣지 않았습니다").strip(" ·")
        self.stages["events"] = _stage("ok", note, total=len(self.new_events), analysis_required=required,
                                       stored=sum(stored.values()), detected=len(self.detected_events),
                                       **{f"type_{key}": value for key, value in by_type.items()})
        self.audit["events"] = by_type

    def _import_legacy_pending(self) -> None:
        """개편 전에 미뤄 둔 사양 변경(`spec_change_pending`)을 대기 이벤트로 바꾼다 (REQ-QAINTEL-006 참고)."""
        if not self.cfg.is_legacy_product:
            return
        pending = _load_json_state(self.store, LEGACY_STATE_B_PENDING, [])
        if not pending:
            return
        events = []
        for change in pending:
            srs = change.get("srs") or {}
            if not srs.get("id"):
                continue
            kind = SRS_CREATED if change.get("change") == "added" else SRS_UPDATED
            events.append(ChangeEvent(ENTITY_SRS, srs["id"], kind, dict(change.get("before") or {}),
                                      dict(change.get("after") or {}), list(change.get("fields") or []), True,
                                      "개편 전에 미뤄 둔 사양 변경", [SPEC_COVERAGE]).as_dict())
        self.store.add_events(self.cfg.slug, self.run_id, events)
        self.store.set_state(LEGACY_STATE_B_PENDING, "[]")

    # -- AI 없는 계산 ------------------------------------------------------------
    def _deterministic(self, srs, srs_items, tc_rows) -> None:
        if srs is not None and tc_rows:
            removed_ids = {event.entity_id: event for event in self.new_events if event.event_type == SRS_REMOVED}
            for finding in packages.removed_srs_findings(srs.diff, tc_rows):
                data = finding.as_dict()
                data.update({"verdict": "UPDATE_EXISTING", "analysis_type": SRS_REMOVED_ANALYSIS,
                             "summary": data["summary"], "related_ids": {"srs": [data["subject"]], "tcs": [data["tc_ref"]["tc_id"] or ""]}})
                if data["subject"] in removed_ids:
                    data["sections"] = {"change": {"summary": f"{data['subject']} 가 오늘 SRS 에 없습니다."}}
                if self.findings_box.save(self.run_id, SKILL_COVERAGE, "COV-removed", data):
                    self.all_findings.append({"skill": SKILL_COVERAGE, "verdict": data["verdict"]})
        weekly = self.force_weekly or _is_weekly(self.today, self.cfg.weekly_day)
        if not weekly:
            self.stages["E"] = _stage("not_due", f"매주 {self.cfg.weekly_day} 에만 돕니다.")
        elif not srs_items or not tc_rows:
            self.stages["E"] = _stage("skipped", "SRS 스냅샷 또는 TC 색인이 없습니다.")
        else:
            gaps = packages.trace_gaps(srs_items, tc_rows)
            saved = 0
            for finding in gaps.findings:
                if self.findings_box.save(self.run_id, SKILL_E, "E-weekly", finding.as_dict()):
                    saved += 1
                    self.all_findings.append({"skill": SKILL_E, "verdict": finding.verdict})
            note = f"구 번호로 확인 불가 {gaps.legacy_unmatched}종" + (" · dry-run: Finding 을 저장하지 않았습니다" if self.dry_run else "")
            self.stages["E"] = _stage("ok", note, srs_seen=gaps.srs_seen, srs_examined=gaps.srs_examined,
                                      tc_rows=gaps.tc_rows_seen, found=len(gaps.findings), new=saved)

    # -- AI 분석 -----------------------------------------------------------------
    def _target_events(self) -> list[dict]:
        if self.on_demand_events is not None:
            return self.on_demand_events
        if self.dry_run:
            return [{"id": None, **event.as_dict()} for event in self.new_events]
        if self.issue_audit:
            # 이번에 만든 점검 대상과, 앞선 점검이 한도에 걸려 남긴 대상을 함께 이어 간다.
            events = self.store.list_events(ids=self.stored_event_ids) if self.stored_event_ids else []
            seen = {event["id"] for event in events}
            events += [event for event in self._remaining_audit(refresh=True) if event["id"] not in seen]
            return events
        if self.until is not None:
            # 지난 날 기간 실행은 재시도 대기열을 다루지 않는다. 지난 스냅샷으로 오늘의 대기 이벤트를
            # 판단하면 최신 이슈가 포기로 바뀐다. 대기열은 다음 매일 실행이 오늘 스냅샷으로 처리한다.
            return self.store.list_events(ids=self.stored_event_ids) if self.stored_event_ids else []
        return self.store.list_events(product=self.cfg.slug, statuses=("pending", "failed"))

    def _analyses(self, client, srs_items, issue_items, tc_rows) -> None:
        events = self._target_events()
        grouped = group_targets(events)
        self.targets_found = sum(len(targets) for targets in grouped.values())
        if not self.targets_found:
            for kind in ANALYSIS_ORDER:
                self.stages[kind] = _stage("skipped", "AI 분석이 필요한 변경이 없습니다.")
            return
        corpus = self._corpus(srs_items, issue_items, tc_rows)
        known = corpus.known()
        comment_cache: dict[str, list[dict]] = {}
        issues_by_id = {item.get("id"): item for item in issue_items}

        def comments_loader(issue_id: str) -> list[dict]:
            """Polarion 에서 댓글 전체를 읽는다. 못 읽으면(지난 날 기간 실행, 읽기 실패) 스냅샷의 댓글을 쓴다."""
            if issue_id not in comment_cache:
                comments, ok = fetch_all_comments(client, issue_id) if client is not None else ([], False)
                if not ok:
                    saved = (issues_by_id.get(issue_id) or {}).get("comments") or []
                    comments = [comment for comment in saved if comment.get("text")][-20:]
                comment_cache[issue_id] = comments
            return comment_cache[issue_id]

        answers = {kind: self._answers(self._skill(kind)) for kind in ANALYSIS_ORDER}
        for kind in ANALYSIS_ORDER:
            targets = grouped[kind] if self.only_kind in (None, kind) else {}
            if not targets:
                self.stages[kind] = _stage("skipped", "대상 없음")
                continue
            items, item_events = [], {}
            for entity_id, target_events in targets.items():
                event_ids = [event["id"] for event in target_events if event.get("id")]
                item = build_item(AnalysisTarget(kind, entity_id, event_ids, target_events), corpus, known, self.cfg,
                                  comments_loader=comments_loader, with_tc=bool(self.on_demand))
                if item is None:
                    if not self.dry_run and self.until is None and not self.on_demand:
                        self.store.update_events(event_ids, "abandoned", self.run_id, error="대상이 오늘 스냅샷에 없습니다")
                    continue
                items.append(item)
                item_events[entity_id] = event_ids
            if not items:
                self.stages[kind] = _stage("skipped", "대상이 오늘 스냅샷에 없어 분석을 포기했습니다.")
                continue
            if self.ai_block or self.limit is not None:
                self._blocked_stage(kind, len(items))
                continue
            if kind == ISSUE_AUDIT:
                tasks = issue_audit.build_tasks(items, corpus, self.cfg.intelligence.audit_batch_size, answers[kind],
                                                corpus.unreadable_documents)
            else:
                tasks = build_tasks(kind, items, self.cfg.batch_size, answers[kind], corpus.unreadable_documents,
                                    skill=self._skill(kind))
            selected = tasks[:max(self.budget, 0)]
            self.budget -= len(selected)
            overflow = len(tasks) - len(selected)
            if self.on_demand:
                self._write_context(srs_items, tc_rows, self.inputs.manuals)
            else:
                self._write_context(srs_items, [], {})
            outcomes = [self._run_task(task, known, kind) for task in self._until_limit(selected)]
            self._record_events(kind, outcomes, item_events)
            self.stages[kind] = self._stage_result(kind, selected, outcomes, overflow)
        if corpus.unreadable_documents:
            # 사양서 없이 SRS 후보만으로 진행했다는 사실을 대상이 있던 분석 단계 비고에 남긴다 (REQ-QAINTEL-011 6번)
            names = ", ".join(corpus.unreadable_documents)
            for kind in ANALYSIS_ORDER:
                if grouped[kind]:
                    stage = self.stages[kind]
                    stage["note"] = (str(stage.get("note") or "") + f" · 읽지 못한 사양서: {names}").strip(" ·")

    def _skill(self, kind: str) -> str:
        """자동 실행은 가벼운 Skill, 버튼 요청은 TC 를 보는 Skill 이다."""
        return ON_DEMAND_SKILLS[kind] if self.on_demand and kind in ON_DEMAND_SKILLS else ANALYSIS_SKILLS[kind]

    def _answers(self, skill: str) -> list[dict]:
        """이 제품 질문의 답변. 이름을 바꾼 Skill 의 옛 답변도 넣는다 (REQ-DAILY-009 4번, REQ-QAINTEL-023)."""
        return self.store.answered_questions(skill, product=self.cfg.slug, include_legacy=self.cfg.is_legacy_product,
                                             aliases=SKILL_ALIASES.get(skill, ()))

    def _until_limit(self, tasks):
        for task in tasks:
            if self.limit is not None:
                return
            yield task

    def _blocked_stage(self, kind: str, count: int) -> None:
        if self.ai_block == "rules":
            self.stages[kind] = _stage("rules", self.rules_state.reason, targets=count)
        elif self.limit is not None:
            note = " 한도가 풀린 뒤 다시 요청하세요." if self.on_demand else " 분석 대상은 대기로 남깁니다."
            self.stages[kind] = _stage("limit", self.limit.describe() + note, targets=count)
        else:
            self.stages[kind] = _stage("skipped", self.stages["preflight"]["note"], targets=count)

    def _corpus(self, srs_items, issue_items, tc_rows) -> Corpus:
        chunks, labels, unreadable = [], {}, []
        if self.cfg.intelligence.use_knowledge_documents:
            try:
                loader = self.spec_chunks_loader or (lambda: default_spec_chunks(self.cfg.product, self.cfg))
                chunks, labels, unreadable = loader()
            except Exception as exc:  # 사양서 조각을 못 읽으면 SRS 후보만으로 진행한다 (REQ-QAINTEL-011 6번)
                unreadable = [f"사양서 문서 목록: {type(exc).__name__}"]
        if unreadable:
            self.audit["unreadable_documents"] = unreadable
        return Corpus(self.profile, issue_items, srs_items, tc_rows, chunks, labels, self.inputs.manuals, unreadable)

    def _write_context(self, srs_items, tc_rows, manuals: dict[str, str]) -> None:
        if self.workspace is not None and not self.context_written:
            self.audit["context"] = write_context(self.workspace, srs_items, [row.as_dict() for row in tc_rows], manuals)
            self.context_written = True

    def _run_task(self, task: packages.Task, known, kind: str | None) -> _TaskOutcome:
        """작업 하나. 형식 오류면 한 번 더 돈다. 사용량 한도면 다시 돌지 않고 멈춘다."""
        workspace, out_dir = self.workspace, self.out_dir
        entry = {"task_id": task.task_id, "skill": task.skill}
        input_path, masking = write_task_input(workspace, task.task_id, task.payload)
        (out_dir / "sent").mkdir(exist_ok=True)
        shutil.copy2(input_path, out_dir / "sent" / input_path.name)
        entry["masking"] = masking
        result_path = workspace.out_dir / f"{task.task_id}.json"
        checked = None
        limit = None
        for attempt in (1, 2):
            result_path.unlink(missing_ok=True)
            runner = self.audit_runner if kind == ISSUE_AUDIT and self.audit_runner is not None else self.runner
            outcome = runner.run(task, workspace)
            self.claude_calls += 0 if self.dry_run else 1
            self._add_usage(outcome.meta)
            entry[f"attempt{attempt}"] = {"ok": outcome.ok, "seconds": round(outcome.seconds, 1), "error": outcome.error[:300], "meta": outcome.meta}
            # Claude 가 읽고 검색한 기록(도구 호출)을 보낸 입력과 같은 실행 폴더에 모은다 (NFR-SEC-001).
            claude_log = workspace.run_dir / "logs" / f"{task.task_id}.claude.json"
            if claude_log.is_file():
                (out_dir / "claude_logs").mkdir(exist_ok=True)
                shutil.copy2(claude_log, out_dir / "claude_logs" / f"{task.task_id}.attempt{attempt}.claude.json")
            if not outcome.ok and outcome.limit is not None and not self.dry_run:
                limit = outcome.limit
                entry["limit"] = limit.as_dict()
                break
            if not outcome.ok and self.dry_run:
                break
            try:
                checked = parse_result(result_path, task.skill, task.task_id)
                break
            except ResultFileError as exc:
                entry[f"attempt{attempt}"]["result_error"] = str(exc)
        if limit is not None:
            self._hit_limit(limit)
            entry["status"] = "limit"
            self.audit["tasks"].append(entry)
            return _TaskOutcome(task, False, error=limit.describe(), limit=limit)
        if checked is None:
            entry["status"] = "failed"
            self.audit["tasks"].append(entry)
            return _TaskOutcome(task, False, error=_failure_reason(entry))
        shutil.copy2(result_path, out_dir / result_path.name)
        saved: dict[str, list[int]] = {}
        rejected = list(checked.rejected)
        not_significant = 0
        targets = {item.get("target") for item in task.payload.get("items") or []}
        for finding in checked.accepted:
            data = finding.model_dump()
            if task.skill in ANALYSIS_SKILL_NAMES and kind:
                validated, reason = validate_finding(data, kind, known, targets, tuple(self.profile.regression_axes),
                                                     tc_mode=bool(self.on_demand))
                if validated is None:
                    if reason == "not_significant":
                        not_significant += 1
                    else:
                        rejected.append({"subject": data.get("subject"), "verdict": data.get("verdict"), "reason": reason})
                    continue
                data = validated
                data["analysis_type"] = ON_DEMAND_STORED_KIND.get(self.on_demand, kind)
                if self.source_finding is not None:
                    data["sections"]["source_finding"] = self.source_finding["id"]
                data["event_ids"] = next((item.get("event_ids") or [] for item in task.payload["items"]
                                          if item.get("target") == data["subject"]), [])
            dedupe = task.skill in (SKILL_COVERAGE, SKILL_TC_DRAFT) or task.skill not in ANALYSIS_SKILL_NAMES
            finding_id = self.findings_box.save(self.run_id, task.skill, task.task_id, data, dedupe=dedupe)
            if finding_id:
                saved.setdefault(data["subject"], []).append(finding_id)
                self.all_findings.append({"skill": task.skill, "verdict": data["verdict"], "analysis_type": data.get("analysis_type", "")})
        for question in checked.result.open_questions:
            if not self.dry_run:
                self.store.add_question(self.run_id, task.skill, question.subject, question.question, question.why, self.cfg.slug)
            self.question_count += 1
        entry.update({"status": "ok", "accepted": sum(len(ids) for ids in saved.values()), "rejected": rejected,
                      "not_significant": not_significant, "gate_status": checked.result.gate_status})
        self.audit["tasks"].append(entry)
        outcome = _TaskOutcome(task, True, finding_ids=saved)
        outcome.rejected = len(rejected)  # type: ignore[attr-defined]
        return outcome

    def _add_usage(self, meta: dict | None) -> None:
        """작업 한 번의 Claude 사용량을 더한다. 실패한 시도도 이미 쓴 토큰은 센다."""
        usage = (meta or {}).get("usage") or {}
        if not isinstance(usage, dict):
            return
        total = 0
        for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            value = int(usage.get(key) or 0)
            self.token_usage[key] += value
            total += value
        self.token_usage["total_tokens"] += total
        self.token_usage["cost_usd"] = round(self.token_usage["cost_usd"] + float((meta or {}).get("total_cost_usd") or 0), 6)

    def _hit_limit(self, limit: claude_limits.LimitInfo) -> None:
        """한도를 기록하고, 세션 한도면 초기화 뒤 한 번 다시 돌도록 적는다 (REQ-QAINTEL-025)."""
        self.limit = limit
        limit.run_id = self.run_id
        self.store.set_state(self.cfg.state_key(STATE_CLAUDE_LIMIT), claude_limits.dump(limit))
        logger.warning("qa_agent_claude_limit product=%s kind=%s reset=%s", self.cfg.slug, limit.kind, limit.reset_at or "-")
        intel = self.cfg.intelligence
        if limit.kind == claude_limits.KIND_SESSION and limit.reset_at:
            reset = datetime.fromisoformat(limit.reset_at)
            if reset - self.today.astimezone(timezone.utc) <= timedelta(hours=intel.catchup_max_hours):
                after = reset + timedelta(minutes=intel.catchup_delay_minutes)
                self.store.set_state(self.cfg.state_key(STATE_CATCHUP_AFTER), after.isoformat(timespec="seconds"))

    def _record_events(self, kind: str, outcomes: list[_TaskOutcome], item_events: dict[str, list[int]]) -> None:
        """분석 하나의 결과를 이벤트에 적는다. 한 이벤트의 분석이 여럿이면 분석마다 따로 끝난다 (REQ-QAINTEL-006).

        버튼 요청은 이미 끝난 분석을 한 번 더 보는 것이라 이벤트 상태를 바꾸지 않는다.
        """
        if self.dry_run or self.on_demand:
            return
        max_attempts = self.cfg.intelligence.event_max_attempts
        for outcome in outcomes:
            for item in outcome.task.payload.get("items") or []:
                target = item.get("target")
                ids = item_events.get(target) or []
                if outcome.ok:
                    self.store.mark_analysis(ids, kind, "done", self.run_id, finding_ids=outcome.finding_ids.get(target, []))
                elif outcome.limit is not None:
                    # 한도는 이벤트 탓이 아니다. 실패 횟수를 올리지 않고 대기로 남긴다.
                    self.store.mark_analysis(ids, kind, "limit", error=outcome.error)
                else:
                    self.store.mark_analysis(ids, kind, "failed", self.run_id, error=outcome.error, max_attempts=max_attempts)

    def _stage_result(self, kind: str, selected: list, outcomes: list[_TaskOutcome], overflow: int) -> dict:
        done = sum(outcome.ok for outcome in outcomes)
        failed = sum(not outcome.ok and outcome.limit is None for outcome in outcomes)
        limited = sum(outcome.limit is not None for outcome in outcomes)
        not_run = len(selected) - len(outcomes)
        accepted = sum(len(ids) for outcome in outcomes for ids in outcome.finding_ids.values())
        rejected = sum(getattr(outcome, "rejected", 0) for outcome in outcomes)
        if self.dry_run:
            status, note = "skipped", "dry-run: 입력 묶음만 만들고 AI 는 부르지 않았습니다"
        elif limited or not_run:
            status, note = "limit", (self.limit.describe() if self.limit else "") + " 남은 분석은 대기로 남깁니다."
        elif failed and not done:
            status, note = "failed", f"{failed}개 작업 모두 실패 (audit.json 참고)"
        elif failed:
            status, note = "partial", f"{failed}개 작업 실패"
        else:
            status, note = "ok", ""
        first = next((outcome.error for outcome in outcomes if not outcome.ok and outcome.limit is None and outcome.error), "")
        if failed and first and not self.dry_run:
            note += f" · 첫 실패 이유: {first}"
        if overflow:
            note = (note + f" · 상한 초과로 {overflow}개 묶음 다음 실행으로 미룸").strip(" ·")
        if rejected:
            note = (note + f" · 규칙 위반으로 버린 Finding {rejected}건").strip(" ·")
        return _stage(status, note.strip(), tasks=len(selected), done=done, failed=failed, accepted=accepted, rejected=rejected)

    # -- 매뉴얼 누락 후보 점검 (REQ-DAILY-006, REQ-QAINTEL-034) --------------------------
    def _manual_check(self, srs_items) -> None:
        """사람이 [매뉴얼 점검]을 눌렀을 때만 돈다. 매뉴얼은 사람이 넣는 자료라 자동으로 돌리지 않는다."""
        cfg = self.cfg
        manuals = self.inputs.manuals
        if not srs_items:
            self.stages["F"] = _stage("skipped", "SRS 스냅샷이 없습니다.")
            return
        week_ago = self.snapshots.previous(snapshots.KIND_SRS, (self.today - timedelta(days=6)).strftime("%Y-%m-%d"))             or self.snapshots.oldest_before(snapshots.KIND_SRS, self.run_date)
        weekly_diff = diff_snapshots(self.snapshots.load(week_ago), srs_items)
        by_id = {item["id"]: item for item in srs_items}
        changed = [
            {"id": item["id"], "old_id": item.get("old_id", ""), "title": item.get("title", ""),
             "text": by_id.get(item["id"], {}).get("text", ""), "fields": item.get("fields", ["added"])}
            for item in (*weekly_diff.modified, *weekly_diff.added)
        ]
        tasks = packages.build_f_tasks(changed, sorted(manuals), cfg.batch_size, self._answers(SKILL_F))
        if not tasks:
            self.stages["F"] = _stage("skipped", "입력이 없습니다 (최근 7일 변경 SRS 또는 매뉴얼 없음).")
            return
        if self.ai_block or self.limit is not None:
            self._blocked_stage("F", len(tasks))
            return
        selected = tasks[: max(self.budget, 0)]
        self.budget -= len(selected)
        self._write_context(srs_items, [], manuals)
        outcomes = [self._run_task(task, None, None) for task in self._until_limit(selected)]
        self.stages["F"] = self._stage_result("F", selected, outcomes, max(len(tasks) - len(selected), 0))

    # -- 사람이 버튼으로 요청한 실행 (REQ-QAINTEL-016·032·034) ---------------------------
    def _execute_on_demand(self) -> dict:
        """저장 스냅샷으로 한 가지만 돈다. 이벤트 상태·스냅샷은 바꾸지 않고 메일도 보내지 않는다."""
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._preflight()
        note = "요청 실행: 저장된 최신 스냅샷을 씁니다(Polarion 을 새로 읽지 않음)"
        self.stages["collect_srs"] = _stage("ok", note)
        self.stages["collect_issues"] = _stage("ok", note)
        srs_items, issue_items = self._saved_srs(), self._saved_issues()
        if self.on_demand == ON_DEMAND_MANUAL:
            self._manual_check(srs_items)
            return self._finish()
        tc_rows = self._tc_index()
        source = self.store.get_finding(self.finding_id) if self.finding_id else None
        wanted = {ON_DEMAND_TC_CHECK: SPEC_COVERAGE, ON_DEMAND_TC_DRAFT: FIXED_ISSUE}.get(self.on_demand)
        refusal = ON_DEMAND_REFUSALS.get(self.on_demand, "알 수 없는 요청입니다.")
        if source is None or wanted is None or source.get("analysis_type") != wanted:
            self.stages["on_demand"] = _stage("failed", refusal)
            return self._finish()
        events = self.store.list_events(ids=source.get("event_ids") or []) if source.get("event_ids") else []
        if not events:
            self.stages["on_demand"] = _stage("failed", "이 분석을 만든 변경 기록이 없어 다시 돌릴 수 없습니다.")
            return self._finish()
        self.source_finding = source
        self.only_kind = wanted
        # 이미 끝난 분석이라도 다시 고르도록 남은 분석을 이 한 가지로 정한다.
        self.on_demand_events = [{**event, "analysis_required": True, "analyses": [wanted], "remaining_analyses": [wanted]}
                                 for event in events]
        self._analyses(None, srs_items, issue_items, tc_rows)
        self.stages["on_demand"] = _stage(self.stages.get(wanted, {}).get("status", "skipped"),
                                          f"{source['subject']} 의 {ON_DEMAND_LABELS[self.on_demand]}")
        self._draft_excel()
        return self._finish()

    # -- 초안 Excel (REQ-QAINTEL-018) ---------------------------------------------
    def _draft_excel(self) -> None:
        if self.dry_run:
            return
        findings = list(reversed(self.store.list_findings(run_id=self.run_id, limit=10_000)))
        checklist = self.profile.checklist or {}
        rows, coverage_rows, review_rows = [], [], []
        for item in findings:
            # TC 비교 결과는 [TC 점검] 요청(또는 개편 전 Coverage 분석)에만 있다 (REQ-QAINTEL-016·031).
            coverage = item.get("analysis_type") == TC_CHECK or item.get("skill") == SKILL_COVERAGE
            if item["draft_tcs"]:
                rows.extend(draft_rows_from_finding(item, checklist=checklist))
            if coverage:
                coverage_rows.extend(coverage_rows_from_finding(item))
            if item["draft_tcs"] or coverage:
                review_rows.append({
                    "대상": item["subject"], "대상 제목": item["subject_title"], "분석": item.get("analysis_type", ""),
                    "판정": item["verdict"], "요약": item["summary"],
                    "근거 위치": "\n".join(evidence.get("location", "") for evidence in item["evidence"]),
                    "신뢰도": item["confidence"], "Finding 번호": item["id"],
                })
        if rows or coverage_rows:
            self.audit["checklist_draft"] = write_draft(self.out_dir / "impact_checklist_draft.xlsx", rows, review_rows,
                                                        self.inputs.checklist_template, coverage_rows, checklist)

    # -- 마무리 -------------------------------------------------------------------
    def _collected(self) -> bool:
        """SRS·이슈 수집이 모두 성공했는가. 건너뛴 수집은 변경을 확인하지 못한 것이다 (REQ-QAINTEL-007 순서 2)."""
        return all(self.stages.get(key, {}).get("status") == "ok" for key in ("collect_srs", "collect_issues"))

    def _remaining_audit(self, refresh: bool = False) -> list[dict]:
        """아직 끝나지 않은 점검 대상 (REQ-QAINTEL-030 순서 5)."""
        if self.dry_run:
            return []
        if refresh or self._audit_left is None:
            self._audit_left = self.store.list_events(product=self.cfg.slug, statuses=("pending", "failed"),
                                                      event_types=(ISSUE_AUDIT_TARGET,))
        return self._audit_left

    # -- 현재 상태 기준 이슈 점검 (REQ-QAINTEL-030) ------------------------------------
    def _execute_audit(self) -> dict:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._preflight()
        client = self._client() if self.until is None else self._stored_only()
        srs = self._collect_srs(client)
        issues = self._collect_issues(client)
        tc_rows = self._tc_index()
        collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if not self.dry_run and self.until is None:
            for kind, result in ((snapshots.KIND_SRS, srs), (snapshots.KIND_ISSUES, issues)):
                if result is not None:
                    self.snapshots.save(kind, self.run_date, result.items, collected_at)
        srs_items = srs.items if srs else self._saved_srs()
        issue_items = issues.items if issues else self._saved_issues()
        if not issue_items:
            self.stages["events"] = _stage("failed", "점검할 이슈 스냅샷이 없습니다.")
            return self._finish()
        base_path = self._baseline(snapshots.KIND_SRS)
        diff = srs.diff if srs is not None else diff_snapshots(self.snapshots.load(base_path), srs_items)
        local_today = self.today.astimezone(claude_limits.KST).date() if self.today.tzinfo else self.today.date()
        start = self.since or local_today
        end = self.until or local_today
        plan = issue_audit.classify(issue_items, srs_items, diff, start, end)
        self.audit_summary = plan.summary(start, end)
        rows = [event.as_dict() for event in plan.events]
        if not self.dry_run:
            self.stored_event_ids = self.store.add_events(self.cfg.slug, self.run_id, rows)
        self.new_events = [event for event, row in zip(plan.events, rows) if row.get("id") or self.dry_run]
        self.detected_events = list(plan.events)
        (self.out_dir / "change_events.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        known = len(plan.events) - len(self.new_events)
        note = plan.note() + (f" 이미 점검한 상태 {known}건은 다시 넣지 않았습니다." if known else "")
        self.stages["events"] = _stage("ok", note, total=len(self.new_events), analysis_required=len(self.new_events),
                                       detected=len(plan.events), **{f"type_{ISSUE_AUDIT_TARGET}": len(self.new_events)})
        self.audit["events"] = {ISSUE_AUDIT_TARGET: len(self.new_events)}
        self._analyses(client, srs_items, issue_items, tc_rows)
        self._draft_excel()
        return self._finish()

    def _finish(self) -> dict:
        statuses = [stage["status"] for stage in self.stages.values()]
        counted = [status for status in statuses if status not in ("not_due", "skipped")]
        if (self.stages.get("on_demand") or {}).get("status") == "failed" or (counted and all(status == "failed" for status in counted)):
            status = "FAILED"
        elif any(status in ("failed", "partial", "rules", "limit") for status in statuses) or not self._collected():
            status = "PARTIAL"
        elif not self.new_events and not self.targets_found:
            status = "BASELINE" if self.baseline_created else "NO_CHANGE"
        else:
            status = "SUCCESS"
        summary = report.summarize(self.all_findings, self.question_count)
        summary["rules"] = self.rules_state.as_dict()
        summary["product"] = self.cfg.slug
        summary["events"] = self.audit.get("events", {})
        summary["claude_calls"] = self.claude_calls
        summary["token_usage"] = self.token_usage
        summary["trigger"] = self.trigger
        if self.limit is not None:
            summary["claude_limit"] = self.limit.as_dict()
        if self.audit_summary is not None:
            summary["issue_audit"] = self.audit_summary
        remaining = self._remaining_audit(refresh=True)
        if self.issue_audit or remaining:
            summary["issue_audit_remaining"] = len(remaining)
        (self.out_dir / "audit.json").write_text(json.dumps(self.audit, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return {"stages": self.stages, "status": status, "summary": summary,
                "rules_warning": "" if self.rules_state.ok else self.rules_state.reason}

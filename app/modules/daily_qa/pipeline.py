"""일일 점검 한 번의 실행 순서 (SPEC REQ-DAILY-001).

외부와 닿는 부분(Polarion, Claude, 메일, 규칙 확인, 지식 파일 위치)은 모두 인자로 바꿔 끼울 수
있다. 테스트는 가짜를 넣고, 운영은 `scripts/run_daily_qa.py` 가 기본값으로 부른다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.modules.daily_qa import packages, report, rules
from app.modules.daily_qa.agent_runner import ClaudeRunner, FakeRunner
from app.modules.daily_qa.checklist_xlsx import draft_rows_from_finding, write_draft
from app.modules.daily_qa.polarion import PolarionError, ReadOnlyPolarionClient, normalize_issue, normalize_srs
from app.modules.daily_qa.schema import SKILL_B, SKILL_C, SKILL_E, SKILL_F, ResultFileError, parse_result
from app.modules.daily_qa.settings import WEEKDAYS, DailyQaSettings
from app.modules.daily_qa.srs_snapshot import diff_snapshots, load_snapshot, previous_snapshot, save_snapshot
from app.modules.daily_qa.store import RUN_STATUSES, DailyQaStore
from app.modules.daily_qa.tc_index import build_index
from app.modules.daily_qa.workspace import WorkspaceError, prepare, write_context, write_task_input

logger = logging.getLogger("regression_analyzer")

LOCK_STALE_SECONDS = 6 * 3600
STATE_ISSUES_SINCE = "issues_last_success_at"
STATE_MANUAL_HASH = "manual_hash"
#: 상한 때문에 미뤘거나 작업이 실패해 아직 검토하지 못한 SRS 변경 (JSON 목록, MISMATCH 4-3).
STATE_B_PENDING = "spec_change_pending"
#: 이슈 기준 시각 뒤에 읽어 처리를 끝낸 이슈 {번호: 수정 시각} (JSON, MISMATCH 4-4).
STATE_ISSUES_DONE = "issues_processed"
AI_STAGES = ("B", "C", "F")


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
        if self.path.exists() and time.time() - self.path.stat().st_mtime > LOCK_STALE_SECONDS:
            self.path.unlink()
        try:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RunLocked(f"다른 일일 점검이 실행 중입니다 ({self.path}).") from exc
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        return self

    def __exit__(self, *_):
        self.path.unlink(missing_ok=True)


def default_inputs(product: str, root: Path) -> Inputs:
    from app.core.product_knowledge import KIND_MANUAL, KIND_TESTCASE, collected_assets, collected_path

    tc_paths = [collected_path(product, asset, root) for asset in collected_assets(product, KIND_TESTCASE, root)]
    template = next((path for path in tc_paths if "영향성평가" in path.name), None)
    manuals: dict[str, str] = {}
    for asset in collected_assets(product, KIND_MANUAL, root):
        normalized = asset.get("normalized_path") or ""
        path = (root / normalized) if normalized and not Path(normalized).is_absolute() else Path(normalized)
        if normalized and path.is_file():
            manuals[f"{Path(asset['file_name']).stem}.txt"] = path.read_text(encoding="utf-8", errors="replace")
    return Inputs(tc_paths=[path for path in tc_paths if path.is_file()], manuals=manuals, checklist_template=template)


def _stage(status: str, note: str = "", **counts) -> dict:
    return {"status": status, "note": note, **({"counts": counts} if counts else {})}


def _is_weekly(today: datetime, weekly_day: str) -> bool:
    return WEEKDAYS[today.weekday()] == weekly_day


def _manual_hash(manuals: dict[str, str]) -> str:
    digest = hashlib.sha256()
    for name in sorted(manuals):
        digest.update(name.encode())
        digest.update(hashlib.sha256(manuals[name].encode("utf-8", "replace")).digest())
    return digest.hexdigest()


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
) -> dict:
    today = today or datetime.now(timezone.utc).astimezone()
    store = store or DailyQaStore(cfg.db_path)
    with _Lock(cfg.data_dir / "run.lock"):
        run_id = today.strftime("%Y%m%d-%H%M%S")
        store.create_run(run_id, dry_run)
        try:
            outcome = _run(cfg, store, run_id, today, dry_run, force_weekly, polarion_factory, runner, inputs, rules_state)
        except Exception as exc:  # 예상 못 한 오류도 실행 기록과 메일에 남긴다
            logger.exception("daily_qa_crashed run=%s", run_id)
            outcome = {"stages": {"preflight": _stage("failed", f"예상 못 한 오류: {type(exc).__name__}")},
                       "status": "FAILED", "summary": report.summarize([], 0), "rules_warning": ""}
        store.finish_run(run_id, outcome["status"], outcome["stages"], outcome["summary"])
        review_url = f"{cfg.review_base_url}/daily-qa/runs/{run_id}" if cfg.review_base_url else f"/daily-qa/runs/{run_id}"
        subject, text, body = report.build_email(
            run_id, RUN_STATUSES[outcome["status"]], outcome["stages"], outcome["summary"], review_url, outcome["rules_warning"]
        )
        sender = send_email or _default_sender
        email = sender(subject, text, body, cfg.email_to)
        store.set_email_status(run_id, str(email.get("status", "")))
        outcome.update({"run_id": run_id, "email": email})
        return outcome


def _default_sender(subject: str, text: str, body: str, recipients: tuple[str, ...]) -> dict:
    from app.core.notifier import send_report

    return send_report(subject, text, body, recipients)


def _run(cfg, store, run_id, today, dry_run, force_weekly, polarion_factory, runner, inputs, rules_state) -> dict:
    stages: dict[str, dict] = {}
    all_findings: list[dict] = []
    question_count = 0
    out_dir = cfg.output_dir / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    run_date = today.strftime("%Y-%m-%d")
    weekly = force_weekly or _is_weekly(today, cfg.weekly_day)
    audit: dict = {"run_id": run_id, "dry_run": dry_run, "tasks": []}

    # 1. 사전 점검 ---------------------------------------------------------
    rules_state = rules_state or rules.check(cfg.product, cfg.root)
    inputs = inputs or default_inputs(cfg.product, cfg.root)
    ai_block = ""
    if not rules_state.ok:
        ai_block = "rules"
    if not dry_run and not cfg.claude_token and runner is None:
        ai_block = ai_block or "token"
    workspace = None
    try:
        workspace = prepare(cfg.workspace_dir, cfg.root, run_id, rules_state.guide_path, rules_state.prompt_path)
    except WorkspaceError as exc:
        ai_block = ai_block or "workspace"
        stages["preflight"] = _stage("failed", str(exc))
    if "preflight" not in stages:
        notes = {"rules": rules_state.reason, "token": "CLAUDE_CODE_OAUTH_TOKEN 이 없어 AI 단계를 건너뜁니다."}
        stages["preflight"] = _stage("ok" if not ai_block else "partial", notes.get(ai_block, ""))
    if runner is None:
        runner = FakeRunner() if dry_run else ClaudeRunner(cfg.claude_command, cfg.claude_token, cfg.task_timeout_seconds)

    # 2. SRS 수집 -------------------------------------------------------------
    client = None
    if cfg.polarion.configured or polarion_factory:
        try:
            client = polarion_factory() if polarion_factory else ReadOnlyPolarionClient(cfg.polarion)
        except PolarionError as exc:
            stages["collect_srs"] = _stage("failed", str(exc))
    srs_items: list[dict] = []
    diff = diff_snapshots(None, [])
    if client is not None:
        try:
            srs_items = [normalize_srs(item) for item in client.iter_workitems(cfg.polarion.srs_query)]
            if not srs_items:
                # 빈 결과를 스냅샷으로 저장하면 다음 날 모든 SRS 가 '삭제'로 보인다.
                raise PolarionError("SRS 조회 결과가 0건입니다. 조회식·권한을 확인하세요.")
            previous = previous_snapshot(cfg.snapshot_dir, run_date)
            diff = diff_snapshots(load_snapshot(previous) if previous else None, srs_items)
            if dry_run:
                # 시험 실행이 스냅샷을 남기면 다음 정식 실행이 오늘 변경을 보지 못한다 (REQ-DAILY-013).
                srs_note = "dry-run: 스냅샷을 저장하지 않았습니다"
            else:
                save_snapshot(cfg.snapshot_dir, run_date, srs_items)
                srs_note = "기준 스냅샷만 저장(비교 대상 없음)" if diff.baseline_only else ""
            stages["collect_srs"] = _stage(
                "ok", srs_note,
                total=len(srs_items), added=len(diff.added), removed=len(diff.removed), modified=len(diff.modified),
            )
            (out_dir / "srs_diff.json").write_text(json.dumps(diff.as_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
        except PolarionError as exc:
            stages["collect_srs"] = _stage("failed", str(exc))
            srs_items = []
    elif "collect_srs" not in stages:
        stages["collect_srs"] = _stage("skipped", "POLARION_HOST / POLARION_TOKEN 이 설정되지 않았습니다.")
    if not srs_items:
        latest = previous_snapshot(cfg.snapshot_dir, "9999-99-99")
        if latest:
            srs_items = load_snapshot(latest)
            stages["collect_srs"]["note"] = (stages["collect_srs"].get("note", "") + f" · E 는 저장된 {latest.stem} 스냅샷 사용").strip(" ·")
    srs_by_id = {item["id"]: item for item in srs_items}

    # 3. 이슈 수집 -----------------------------------------------------------
    new_issues: list[dict] = []
    newest_issue_update = ""
    since = ""
    issues_done = _load_json_state(store, STATE_ISSUES_DONE, {})
    if client is not None:
        try:
            since = store.get_state(STATE_ISSUES_SINCE) or (today - timedelta(days=1)).astimezone(timezone.utc).isoformat()
            for raw in client.iter_workitems(cfg.polarion.issue_query):
                issue = normalize_issue(raw)
                newest_issue_update = max(newest_issue_update, issue["updated"])
                # 앞 실행에서 이미 처리한 이슈는 그 뒤로 다시 바뀌지 않았으면 또 보내지 않는다.
                if issue["updated"] and _after(issue["updated"], since) and issues_done.get(issue["id"]) != issue["updated"]:
                    new_issues.append(issue)
            for issue in new_issues[: cfg.batch_size * cfg.max_tasks_per_run]:
                issue["comments"] = [
                    _comment_text(comment) for comment in client.get_comments(issue["id"])
                ][:20]
            stages["collect_issues"] = _stage("ok", f"기준 시각 {since[:19]}", new=len(new_issues))
        except PolarionError as exc:
            stages["collect_issues"] = _stage("failed", str(exc))
    else:
        stages["collect_issues"] = _stage("skipped", "Polarion 연결이 없어 건너뜁니다.")

    # 4. TC 색인 -------------------------------------------------------------
    tc_rows, tc_errors = build_index(inputs.tc_paths)
    if not inputs.tc_paths:
        stages["tc_index"] = _stage("failed", "수집된 TC 파일이 없습니다. 지식 폴더 동기화를 확인하세요.")
    else:
        stages["tc_index"] = _stage("partial" if tc_errors else "ok", "; ".join(tc_errors), files=len(inputs.tc_paths), rows=len(tc_rows))

    # 5. 결정적 계산 ---------------------------------------------------------
    findings_box = _OpenFindings(store, dry_run=dry_run)
    b_deterministic = packages.removed_srs_findings(diff, tc_rows) if tc_rows else []
    for finding in b_deterministic:
        if findings_box.save(run_id, SKILL_B, "B-removed", finding.as_dict()):
            all_findings.append({"skill": SKILL_B, "verdict": finding.verdict})
    if not weekly:
        stages["E"] = _stage("not_due", f"매주 {cfg.weekly_day} 에만 돕니다.")
    elif not srs_items or not tc_rows:
        stages["E"] = _stage("skipped", "SRS 스냅샷 또는 TC 색인이 없습니다.")
    else:
        gaps = packages.trace_gaps(srs_items, tc_rows)
        saved = 0
        for finding in gaps.findings:
            if findings_box.save(run_id, SKILL_E, "E-weekly", finding.as_dict()):
                saved += 1
                all_findings.append({"skill": SKILL_E, "verdict": finding.verdict})
        e_note = f"구 번호로 확인 불가 {gaps.legacy_unmatched}종" + (" · dry-run: Finding 을 저장하지 않았습니다" if dry_run else "")
        stages["E"] = _stage(
            "ok", e_note,
            srs_seen=gaps.srs_seen, srs_examined=gaps.srs_examined, tc_rows=gaps.tc_rows_seen,
            found=len(gaps.findings), new=saved,
        )

    # 6. AI 작업 -------------------------------------------------------------
    manual_hash = _manual_hash(inputs.manuals) if inputs.manuals else ""
    manual_changed = bool(manual_hash) and manual_hash != store.get_state(STATE_MANUAL_HASH)
    tasks: dict[str, list[packages.Task]] = {"B": [], "C": [], "F": []}
    b_carried = _load_json_state(store, STATE_B_PENDING, [])
    if stages["collect_srs"]["status"] == "ok":
        tasks["B"] = packages.build_b_tasks(
            diff, tc_rows, cfg.batch_size, cfg.tc_candidate_limit, store.answered_questions(SKILL_B), carried=b_carried
        )
    if new_issues:
        tasks["C"] = packages.build_c_tasks(new_issues, srs_by_id, tc_rows, cfg.batch_size, cfg.tc_candidate_limit, store.answered_questions(SKILL_C))
    f_due = weekly or manual_changed
    if f_due and srs_items:
        # 7일 전 스냅샷이 아직 없으면(운영 첫 주) 오늘 이전의 가장 오래된 스냅샷과 비교한다.
        week_ago = previous_snapshot(cfg.snapshot_dir, (today - timedelta(days=6)).strftime("%Y-%m-%d")) or _oldest_before(
            cfg.snapshot_dir, run_date
        )
        weekly_diff = diff_snapshots(load_snapshot(week_ago) if week_ago else None, srs_items)
        changed = [
            {"id": item["id"], "old_id": item.get("old_id", ""), "title": item.get("title", ""),
             "text": srs_by_id.get(item["id"], {}).get("text", ""), "fields": item.get("fields", ["added"])}
            for item in (*weekly_diff.modified, *weekly_diff.added)
        ]
        tasks["F"] = packages.build_f_tasks(changed, sorted(inputs.manuals), cfg.batch_size, store.answered_questions(SKILL_F))

    budget = cfg.max_tasks_per_run
    input_failed = {
        "B": "SRS 수집이 실패해 검토하지 못했습니다" if stages["collect_srs"]["status"] == "failed" else "",
        "C": "이슈 수집이 실패해 검토하지 못했습니다" if stages["collect_issues"]["status"] == "failed" else "",
    }
    for key in AI_STAGES:
        if key == "F" and not f_due:
            stages["F"] = _stage("not_due", f"매주 {cfg.weekly_day} 또는 매뉴얼이 바뀐 날에만 돕니다.")
            continue
        if not tasks[key] and input_failed.get(key):
            # 수집 실패를 '바뀐 것 없음' 으로 보이지 않게 한다 (MISMATCH 4-1).
            stages[key] = _stage("failed", input_failed[key])
            continue
        if not tasks[key]:
            stages.setdefault(key, _stage("skipped", "입력이 없습니다 (변경·신규 항목 없음)."))
            if key == "B" and not dry_run and stages["collect_srs"]["status"] == "ok" and b_carried:
                _save_json_state(store, STATE_B_PENDING, [])
            continue
        if ai_block == "rules":
            stages[key] = _stage("rules", rules_state.reason, tasks=len(tasks[key]))
            _carry_over(store, key, tasks[key], dry_run)
            continue
        if ai_block:
            stages[key] = _stage("skipped", stages["preflight"]["note"], tasks=len(tasks[key]))
            _carry_over(store, key, tasks[key], dry_run)
            continue
        selected = tasks[key][:budget]
        budget -= len(selected)
        overflow = len(tasks[key]) - len(selected)
        if workspace is not None and not audit.get("context_written"):
            audit["context"] = write_context(workspace, srs_items, [row.as_dict() for row in tc_rows], inputs.manuals)
            audit["context_written"] = True
        stage_result = _run_tasks(key, selected, workspace, runner, store, run_id, out_dir, audit, all_findings, dry_run, findings_box)
        question_count += stage_result.pop("questions", 0)
        failed_tasks = stage_result.pop("failed_tasks", [])
        if overflow:
            stage_result["note"] = (stage_result.get("note", "") + f" · 상한 초과로 {overflow}개 묶음 다음 실행으로 미룸").strip(" ·")
        if key == "B" and failed_tasks:
            stage_result["note"] = (stage_result["note"] + f" · 실패한 {len(failed_tasks)}개 묶음은 다음 실행에 다시 넣음").strip(" ·")
        stages[key] = stage_result
        not_done = [task for task in selected if task.task_id in failed_tasks] + tasks[key][len(selected):]
        _carry_over(store, key, not_done, dry_run)
        if key == "C" and not dry_run:
            done_tasks = [task for task in selected if task.task_id not in failed_tasks]
            _advance_issue_cursor(store, stage_result["status"] == "ok" and not overflow, since, newest_issue_update,
                                  issues_done, done_tasks)
        if key == "F" and stage_result["status"] == "ok" and not dry_run and manual_hash:
            store.set_state(STATE_MANUAL_HASH, manual_hash)

    if stages["collect_issues"]["status"] == "ok" and not new_issues and newest_issue_update and not dry_run:
        store.set_state(STATE_ISSUES_SINCE, newest_issue_update)
        if issues_done:
            _save_json_state(store, STATE_ISSUES_DONE, {})

    # 7. C 초안 Excel -----------------------------------------------------
    c_findings = store.list_findings(run_id=run_id, skill=SKILL_C)
    if c_findings:
        rows = [row for finding in reversed(c_findings) for row in draft_rows_from_finding(finding)]
        review_rows = [
            {
                "이슈 ID": item["subject"], "이슈 제목": item["subject_title"], "이슈 유형": item["issue_type"],
                "판정": item["verdict"], "요약": item["summary"],
                "근거 위치": "\n".join(evidence.get("location", "") for evidence in item["evidence"]),
                "신뢰도": item["confidence"], "Finding 번호": item["id"],
            }
            for item in reversed(c_findings)
        ]
        audit["checklist_draft"] = write_draft(out_dir / "impact_checklist_draft.xlsx", rows, review_rows, inputs.checklist_template)

    # 8. 마무리 ----------------------------------------------------------------
    statuses = [stage["status"] for stage in stages.values()]
    if all(status in ("failed",) for status in statuses if status not in ("not_due", "skipped")) and "failed" in statuses:
        status = "FAILED"
    elif any(status in ("failed", "partial", "rules") for status in statuses):
        status = "PARTIAL"
    else:
        status = "SUCCESS"
    summary = report.summarize(all_findings, question_count)
    summary["rules"] = rules_state.as_dict()
    (out_dir / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return {"stages": stages, "status": status, "summary": summary, "rules_warning": "" if rules_state.ok else rules_state.reason}


def _oldest_before(directory: Path, run_date: str) -> Path | None:
    if not directory.is_dir():
        return None
    candidates = sorted(path for path in directory.glob("*.json") if path.stem < run_date)
    return candidates[0] if candidates else None


def _after(value: str, since: str) -> bool:
    def parse(text: str) -> datetime:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))

    try:
        return parse(value) > parse(since)
    except ValueError:
        return value > since


def _comment_text(comment: dict) -> dict:
    from app.parsers.polarion_issue import html_to_text

    attrs = comment.get("attributes") or {}
    raw = attrs.get("text")
    value = raw.get("value", "") if isinstance(raw, dict) else str(raw or "")
    text, _ = html_to_text(value)
    return {"created": str(attrs.get("created") or ""), "text": text.strip()[:2000]}


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


def _save_json_state(store: DailyQaStore, key: str, value) -> None:
    store.set_state(key, json.dumps(value, ensure_ascii=False))


def _carry_over(store: DailyQaStore, key: str, not_done: list[packages.Task], dry_run: bool) -> None:
    """검토하지 못한 SRS 변경을 다음 실행에 다시 넣도록 남긴다 (MISMATCH 4-3). 이슈는 기준 시각이 맡는다."""
    if key != "B" or dry_run:
        return
    changes = [
        {name: value for name, value in change.items() if name != "candidates"}
        for task in not_done for change in task.payload.get("changes", [])
    ]
    _save_json_state(store, STATE_B_PENDING, changes)


def _advance_issue_cursor(store, all_done: bool, since: str, newest: str, issues_done: dict, done_tasks) -> None:
    """이슈 기준 시각 옮기기 (MISMATCH 4-4).

    모든 묶음이 성공하고 미룬 묶음이 없을 때만 기준 시각을 가장 늦은 수정 시각으로 옮긴다.
    아니면 기준 시각은 그대로 두고(첫 실행이면 이번에 쓴 시각을 저장), 처리한 이슈만 기록해
    다음 실행에서 실패·미룬 이슈만 다시 보낸다.
    """
    if all_done and newest:
        store.set_state(STATE_ISSUES_SINCE, newest)
        _save_json_state(store, STATE_ISSUES_DONE, {})
        return
    if since and not store.get_state(STATE_ISSUES_SINCE):
        store.set_state(STATE_ISSUES_SINCE, since)
    done = dict(issues_done)
    for task in done_tasks:
        for item in task.payload.get("issues", []):
            issue = item.get("issue") or {}
            if issue.get("id"):
                done[issue["id"]] = issue.get("updated", "")
    _save_json_state(store, STATE_ISSUES_DONE, done)


def _tc_key(tc_ref: dict | None) -> tuple:
    if not tc_ref:
        return ()
    workbook, sheet = tc_ref.get("workbook", ""), tc_ref.get("sheet", "")
    return (workbook, sheet, tc_ref.get("tc_id")) if tc_ref.get("tc_id") else (workbook, sheet, int(tc_ref.get("row") or 0))


class _OpenFindings:
    """Finding 저장 창구. 같은 기록인지 가리는 기준은 점검·대상·판정·대상 TC 위치다 (REQ-DAILY-018).

    대상 TC 를 기준에 넣어, 같은 SRS 를 가리키는 TC 가 여럿이면 TC 마다 Finding 이 남는다
    (MISMATCH 4-5). 열린 Finding 목록은 점검마다 한 번만 읽는다. 시험 실행은 저장하지 않는다.
    """

    OPEN = ("PENDING", "APPROVED", "NEED_EVIDENCE")

    def __init__(self, store: DailyQaStore, dry_run: bool = False) -> None:
        self.store = store
        self.dry_run = dry_run
        self._keys: dict[str, set[tuple]] = {}

    def _open_keys(self, skill: str) -> set[tuple]:
        if skill not in self._keys:
            keys: set[tuple] = set()
            for status in self.OPEN:
                for item in self.store.list_findings(skill=skill, review_status=status, limit=1_000_000):
                    keys.add((item["subject"], item["verdict"], _tc_key(item.get("tc_ref"))))
            self._keys[skill] = keys
        return self._keys[skill]

    def save(self, run_id: str, skill: str, task_id: str, finding: dict, dedupe: bool = True) -> bool:
        if self.dry_run:
            return False
        if dedupe:
            key = (finding["subject"], finding["verdict"], _tc_key(finding.get("tc_ref")))
            keys = self._open_keys(skill)
            if key in keys:
                return False
            keys.add(key)
        self.store.add_finding(run_id, skill, task_id, finding)
        return True


def _failure_reason(entry: dict) -> str:
    for attempt in ("attempt2", "attempt1"):
        info = entry.get(attempt) or {}
        text = " ".join(str(info.get(name) or "") for name in ("error", "result_error")).strip()
        if text:
            return " ".join(text.split())[:120]
    return ""


def _run_tasks(key, tasks, workspace, runner, store, run_id, out_dir, audit, all_findings, dry_run, findings_box=None) -> dict:
    findings_box = findings_box or _OpenFindings(store, dry_run=dry_run)
    done = failed = accepted = rejected = questions = 0
    failed_tasks: list[str] = []
    first_reason = ""
    sent_dir = out_dir / "sent"
    for task in tasks:
        entry = {"task_id": task.task_id, "skill": task.skill}
        input_path, masking = write_task_input(workspace, task.task_id, task.payload)
        sent_dir.mkdir(exist_ok=True)
        shutil.copy2(input_path, sent_dir / input_path.name)
        entry["masking"] = masking
        result_path = workspace.out_dir / f"{task.task_id}.json"
        checked = None
        for attempt in (1, 2):
            result_path.unlink(missing_ok=True)
            outcome = runner.run(task, workspace)
            entry[f"attempt{attempt}"] = {"ok": outcome.ok, "seconds": round(outcome.seconds, 1), "error": outcome.error[:300], "meta": outcome.meta}
            # Claude 가 읽고 검색한 기록(도구 호출)을 보낸 입력과 같은 실행 폴더에 모은다 (NFR-SEC-001).
            claude_log = workspace.run_dir / "logs" / f"{task.task_id}.claude.json"
            if claude_log.is_file():
                (out_dir / "claude_logs").mkdir(exist_ok=True)
                shutil.copy2(claude_log, out_dir / "claude_logs" / f"{task.task_id}.attempt{attempt}.claude.json")
            if not outcome.ok and dry_run:
                break
            try:
                checked = parse_result(result_path, task.skill, task.task_id)
                break
            except ResultFileError as exc:
                entry[f"attempt{attempt}"]["result_error"] = str(exc)
        if checked is None:
            failed += 1
            failed_tasks.append(task.task_id)
            first_reason = first_reason or _failure_reason(entry)
            entry["status"] = "failed"
            audit["tasks"].append(entry)
            continue
        done += 1
        shutil.copy2(result_path, out_dir / result_path.name)
        for finding in checked.accepted:
            data = finding.model_dump()
            if findings_box.save(run_id, task.skill, task.task_id, data, dedupe=(task.skill == SKILL_B)):
                accepted += 1
                all_findings.append({"skill": task.skill, "verdict": finding.verdict})
        for question in checked.result.open_questions:
            store.add_question(run_id, task.skill, question.subject, question.question, question.why)
            questions += 1
        rejected += len(checked.rejected)
        entry.update({"status": "ok", "accepted": len(checked.accepted), "rejected": checked.rejected, "gate_status": checked.result.gate_status})
        audit["tasks"].append(entry)
    if dry_run:
        status, note = "skipped", "dry-run: 입력 묶음만 만들고 AI 는 부르지 않았습니다"
    elif failed and not done:
        status, note = "failed", f"{failed}개 작업 모두 실패 (audit.json 참고)"
    elif failed:
        status, note = "partial", f"{failed}개 작업 실패"
    else:
        status, note = "ok", ""
    if failed and first_reason and not dry_run:
        # 메일만 보는 사람도 사용량 한도 같은 이유를 알 수 있게 첫 실패 이유를 붙인다 (MISMATCH 4-7).
        note += f" · 첫 실패 이유: {first_reason}"
    if rejected:
        note = (note + f" · 규칙 위반으로 버린 Finding {rejected}건").strip(" ·")
    return {"status": status, "note": note, "counts": {"tasks": len(tasks), "done": done, "failed": failed, "accepted": accepted, "rejected": rejected},
            "questions": questions, "failed_tasks": [] if dry_run else failed_tasks}

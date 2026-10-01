"""QA Intelligence 시나리오 테스트 공용 도구. 실제 Polarion·Claude·메일을 쓰지 않는다.

`Harness.run(day)` 는 가짜 Polarion(현재 `srs`·`issues`·`comments`)으로 파이프라인을 한 번 돌린다.
`script` 에 대상 번호 → 결과(Finding 일부)를 넣으면 가짜 Claude 가 그대로 결과 파일을 쓴다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.modules.daily_qa.agent_runner import FakeRunner, RunnerOutcome
from app.modules.daily_qa.pipeline import Inputs, run_daily
from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import FakePolarion, make_settings, rules_ok

DAY1 = datetime(2026, 9, 29, 7, 30, tzinfo=timezone.utc)   # 화요일
DAY2 = DAY1 + timedelta(days=1)
DAY3 = DAY1 + timedelta(days=2)


class Harness:
    def __init__(self, tmp: Path, tc_paths: list[Path] | None = None, manuals: dict | None = None, **settings) -> None:
        self.tmp = tmp
        root = tmp / "root"
        (root / "data").mkdir(parents=True, exist_ok=True)
        self.cfg = make_settings(root, tmp / "ws", **settings)
        self.store = DailyQaStore(self.cfg.db_path)
        self.inputs = Inputs(tc_paths=tc_paths or [], manuals=manuals or {},
                             checklist_template=next((path for path in tc_paths or [] if "영향성평가" in path.name), None))
        self.srs: list[dict] = []
        self.issues: list[dict] = []
        self.comments: dict[str, list[dict]] = {}
        self.fail_comments: set[str] = set()
        self.script: dict[str, dict] = {}
        self.payloads: list[dict] = []
        self.calls: list[tuple[str, list[str]]] = []
        self.usage: dict | None = None
        self.outcome_override = None
        self.sent: list = []
        self.polarion: FakePolarion | None = None

    # -- 가짜 Claude --------------------------------------------------------------
    def _produce(self, task, run):
        payload = json.loads((run.in_dir / f"{task.task_id}.json").read_text(encoding="utf-8"))
        self.payloads.append(payload)
        self.calls.append((task.skill, [item.get("target") for item in payload.get("items", [])]))
        if self.outcome_override is not None:
            return self.outcome_override(task, run)
        findings = []
        for item in payload.get("items", []):
            target = item["target"]
            scripted = self.script.get(target)
            if scripted is None:
                continue
            finding = {"subject": target, "summary": scripted.get("summary", "분석"), "confidence": "Review Needed",
                       "evidence": scripted.get("evidence", [{"source_type": "srs", "location": f"{target} 본문", "validity": "Current"}])}
            finding.update({key: value for key, value in scripted.items() if key not in ("summary", "evidence")})
            findings.append(finding)
        (run.out_dir / f"{task.task_id}.json").write_text(json.dumps({
            "skill": task.skill, "task_id": task.task_id, "gate_status": {"G1": "PASS"}, "findings": findings,
            "open_questions": [], "human_review_required": True}, ensure_ascii=False), encoding="utf-8")
        meta = {"usage": self.usage, "total_cost_usd": 0.01} if self.usage else {}
        return RunnerOutcome(True, 0, 0.0, meta=meta)

    def run(self, day: datetime, **kwargs) -> dict:
        self.polarion = FakePolarion(self.srs, self.issues, self.comments, self.fail_comments)
        kwargs.setdefault("runner", FakeRunner(self._produce))
        kwargs.setdefault("rules_state", rules_ok(self.tmp))
        kwargs.setdefault("spec_chunks_loader", lambda: ([], {}, []))
        return run_daily(self.cfg, today=day, polarion_factory=lambda: self.polarion, inputs=self.inputs, store=self.store,
                         send_email=lambda *args: self.sent.append(args) or {"status": "sent"}, **kwargs)

    def events(self, run_id: str | None = None, **filters) -> list[dict]:
        return self.store.list_events(product=self.cfg.slug, run_id=run_id, **filters)

    def skills_called(self) -> list[str]:
        return [skill for skill, _ in self.calls]

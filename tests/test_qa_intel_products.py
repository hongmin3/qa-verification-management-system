"""제품 공통 엔진 — 필드 이름이 다른 가상 제품도 같은 모델·이벤트·저장으로 도는지 (SPEC REQ-QAINTEL-002·023, NFR-QAINTEL-002).

Validates: REQ-QAINTEL-002, REQ-QAINTEL-023, NFR-QAINTEL-002
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.modules.daily_qa.change_events import FIXED_ISSUE, detect_issue_events
from app.modules.daily_qa.product_adapter import load_profile, normalize_issue, normalize_srs
from app.modules.daily_qa.store import DailyQaStore
from tests.daily_qa_fixtures import VXVUE_PROFILE, FakePolarion, issue_item, make_settings, rules_ok, srs_item

REPO = Path(__file__).resolve().parents[1]

FAKE_YAML = """
product: Fake Product
alm:
  project_id: FAKE
  queries: {srs: "type:requirement", issue: "type:defect"}
  fields:
    srs: {legacy_id: legacyNo, description: [bodyText]}
    issue: {rd_result: devResult, reproduction_step: steps, occurrence_cause: rootCause, action_details: fixNote}
  relations: {target_versions: plannedIn, linked_items: relatesTo, comments: notes}
  rd_result_mapping: {resolved: FIXED, as_designed: SPEC, duplicated: DUPLICATE}
qa_intelligence:
  rules: {supported_rev: "", product_skill: ""}
  regression_axes: [DIRECT, STATE]
"""


@pytest.fixture
def fake_root(tmp_path):
    root = tmp_path / "root"
    (root / "config" / "products").mkdir(parents=True)
    (root / "config" / "products" / "fake-product.yaml").write_text(FAKE_YAML, encoding="utf-8")
    (root / "data").mkdir()
    return root


def fake_issue(item_id: str, dev_result: str, cause: str = "", title: str = "화면 멈춤") -> dict:
    return {
        "id": f"FAKE/{item_id}",
        "attributes": {"id": item_id, "title": title, "status": "open", "severity": "major", "devResult": {"id": dev_result},
                       "created": "2026-09-01", "updated": "2026-09-02",
                       "description": {"type": "text/html", "value": "<p>설명</p>"},
                       "steps": {"type": "text/html", "value": "<p>1. 연다</p>"},
                       "rootCause": {"type": "text/html", "value": f"<p>{cause}</p>"}, "fixNote": {"type": "text/html", "value": ""}},
        "relationships": {"plannedIn": {"data": [{"id": "FAKE/2.0"}]},
                          "relatesTo": {"data": [{"id": f"FAKE/{item_id}/relates/FAKE/REQ-7"}]}, "notes": {"data": []}},
    }


def fake_srs(item_id: str, text: str) -> dict:
    return {"id": f"FAKE/{item_id}", "attributes": {"id": item_id, "legacyNo": "L-1", "title": "요구", "status": "approved",
                                                   "bodyText": {"type": "text/html", "value": f"<p>{text}</p>"}}}


def test_two_products_with_different_raw_fields_normalize_to_the_same_model(fake_root):
    fake = load_profile("Fake Product", fake_root)
    assert (fake.slug, fake.project_id, fake.srs_query, fake.issue_query) == ("fake-product", "FAKE", "type:requirement", "type:defect")
    vx = normalize_issue(issue_item("VP-1", "2026-09-02", ["VP-7"], review="lab_fixed", cause="원인"), VXVUE_PROFILE)
    fk = normalize_issue(fake_issue("FK-1", "resolved", cause="원인"), fake)
    assert set(vx) == set(fk)                                    # 같은 공통 모델
    assert vx["rd_result"] == fk["rd_result"] == "FIXED"
    assert vx["occurrence_cause"] == fk["occurrence_cause"] == "원인"
    assert fk["reproduction_step"] == "1. 연다" and fk["target_versions"] == ["2.0"] and fk["linked_ids"] == ["REQ-7"]
    assert fk["comment_ids"] == []
    srs = normalize_srs(fake_srs("REQ-7", "본문"), fake)
    assert srs["old_id"] == "L-1" and srs["text"] == "본문"


@pytest.mark.parametrize(("raw", "common"), [("resolved", "FIXED"), ("as_designed", "SPEC"), ("duplicated", "DUPLICATE"),
                                             ("lab_fixed", "OTHER"), ("", "UNSET")])
def test_rd_result_mapping_is_per_product(fake_root, raw, common):
    assert load_profile("Fake Product", fake_root).rd_common(raw) == common


def test_event_detector_is_the_same_for_both_products(fake_root):
    fake = load_profile("Fake Product", fake_root)
    for profile, before, after in (
        (VXVUE_PROFILE, issue_item("VP-1", "d", [], review=""), issue_item("VP-1", "d", [], review="lab_fixed")),
        (fake, fake_issue("FK-1", ""), fake_issue("FK-1", "resolved")),
    ):
        events = detect_issue_events([normalize_issue(before, profile)], [normalize_issue(after, profile)])
        assert [(event.event_type, event.analyses) for event in events] == [("ISSUE_RD_RESULT_CHANGED", [FIXED_ISSUE])]


def test_missing_alm_section_falls_back_to_config_yaml_values(tmp_path):
    profile = load_profile("NoSuchProduct", tmp_path, {"project_id": "OLD", "srs_query": "type:srs", "issue_query": "type:bug"})
    assert (profile.project_id, profile.issue_query) == ("OLD", "type:bug")
    assert profile.rd_common("anything") == "OTHER"


def test_products_do_not_share_snapshots_state_events_or_findings(fake_root, tmp_path):
    from app.modules.daily_qa.agent_runner import FakeRunner
    from app.modules.daily_qa.pipeline import Inputs, run_daily
    from tests.qa_intel_harness import DAY1, DAY2

    fake = load_profile("Fake Product", fake_root)
    vx_cfg = make_settings(fake_root, tmp_path / "ws")
    fk_cfg = make_settings(fake_root, tmp_path / "ws", product="Fake Product", profile=fake, legacy_product="VXvue")
    store = DailyQaStore(vx_cfg.db_path)
    worlds = {
        "vxvue": FakePolarion([srs_item("VP-10", "01", "A")], [issue_item("VP-1", "d", [], review="")]),
        "fake-product": FakePolarion([fake_srs("REQ-7", "본문")], [fake_issue("FK-1", "")], srs_marker="requirement"),
    }
    for day in (DAY1, DAY2):
        if day is DAY2:
            worlds["fake-product"].issues.append(fake_issue("FK-2", "", title="새 결함"))
        for cfg in (vx_cfg, fk_cfg):
            run_daily(cfg, today=day, polarion_factory=lambda cfg=cfg: worlds[cfg.slug], runner=FakeRunner(lambda task, run: None),
                      inputs=Inputs(), rules_state=rules_ok(tmp_path), store=store, send_email=lambda *a: {"status": "x"},
                      spec_chunks_loader=lambda: ([], {}, []))
    assert vx_cfg.issue_snapshot_dir != fk_cfg.issue_snapshot_dir
    assert (fk_cfg.issue_snapshot_dir / "2026-09-30.json").is_file() and (vx_cfg.issue_snapshot_dir / "2026-09-30.json").is_file()
    assert worlds["fake-product"].queries[:2] == ["type:requirement", "type:defect"]
    fk_events = store.list_events(product="fake-product")
    assert [(event["entity_id"], event["event_type"]) for event in fk_events] == [("FK-2", "ISSUE_CREATED")]
    assert store.list_events(product="vxvue") == []                # 한 제품의 변경이 다른 제품에 섞이지 않는다
    runs = store.list_runs(product="fake-product")
    assert runs and all(run["id"].endswith("-fake-product") for run in runs)
    assert vx_cfg.state_key("claude_limit") != fk_cfg.state_key("claude_limit")
    assert vx_cfg.lock_path != fk_cfg.lock_path and vx_cfg.product_workspace_dir != fk_cfg.product_workspace_dir
    assert fk_cfg.legacy_snapshot_dir is None and vx_cfg.legacy_snapshot_dir is not None


def test_legacy_rows_without_product_are_read_only_as_the_legacy_product(tmp_path):
    store = DailyQaStore(tmp_path / "app.db")
    store.create_run("old-run", False)                               # 개편 전: 제품 열이 빈 기록
    store.create_run("new-run", False, "fake-product")
    assert [run["id"] for run in store.list_runs(product="vxvue", include_legacy=True)] == ["old-run"]
    assert [run["id"] for run in store.list_runs(product="fake-product")] == ["new-run"]


#: 공통 엔진 파일에 제품 고유 필드 이름·원본 값이 없어야 한다 (NFR-QAINTEL-002).
ENGINE_FILES = [
    "app/modules/daily_qa/change_events.py", "app/modules/daily_qa/intelligence.py", "app/modules/daily_qa/evidence_validation.py",
    "app/modules/daily_qa/pipeline.py", "app/modules/daily_qa/collector.py", "app/modules/daily_qa/snapshots.py",
    "app/modules/daily_qa/claude_limits.py", "app/modules/daily_qa/scheduled_jobs.py", "app/core/daily_qa_storage.py",
    "app/modules/qa_agent/dashboard.py",
]
PRODUCT_TOKENS = re.compile(r"rndReviewResult|occurrenceCause|actionDetails|descriptionKR|reproductionStep|\boldId\b|lab_fixed|lab_inspec|occurredVersion|targetVersion")


@pytest.mark.parametrize("relative", ENGINE_FILES)
def test_engine_files_do_not_hardcode_product_fields(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    assert not PRODUCT_TOKENS.findall(text), f"{relative} 에 제품 고유 필드 이름이 있습니다"


def test_product_rule_skill_lives_with_the_product_config():
    assert (REPO / "config" / "products" / "vxvue" / "skills" / "vxvue-qa-rules" / "SKILL.md").is_file()
    common = REPO / "app" / "modules" / "daily_qa" / "skills"
    assert not any(path.name.startswith("vxvue-") for path in common.iterdir())
    assert VXVUE_PROFILE.skills_dir == REPO / "config" / "products" / "vxvue" / "skills"
    assert VXVUE_PROFILE.supported_rules_rev == "1.17" and "GENERATOR" in VXVUE_PROFILE.regression_axes

"""QA Intelligence 대시보드·상세·지금 실행 화면 (TEST-QAINTEL-009).

실제 파이프라인을 가짜 Polarion·가짜 Claude 로 돌려 저장소를 채운 뒤 `/qa-agent` 화면을 연다.
Claude·Polarion·메일은 부르지 않는다. 실행 프로세스도 가짜 `popen` 으로 대신한다.

Validates: REQ-QAINTEL-019, REQ-QAINTEL-020, REQ-QAINTEL-021, REQ-QAINTEL-022, REQ-QAINTEL-024
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.storage import Storage
from app.modules.daily_qa import claude_limits, scheduled_jobs
from app.modules.daily_qa.agent_runner import FakeRunner
from app.modules.daily_qa.pipeline import STATE_CLAUDE_LIMIT, Inputs, run_daily
from app.modules.daily_qa.store import DailyQaStore
from app.modules.qa_agent import dashboard as dash
from app.modules.qa_agent import router as router_module
from tests.daily_qa_fixtures import FakePolarion, comment, issue_item, make_settings, make_tc_workbook, rules_ok, srs_item
from tests.test_qa_intel_pipeline import analysis_producer

REPO_ROOT = Path(__file__).resolve().parents[1]
DAY1 = datetime(2026, 9, 29, 7, 30, tzinfo=timezone.utc)
DAY2 = DAY1 + timedelta(days=1)

#: 분석 종류마다 상세 화면에만 있는 구획 제목 (REQ-QAINTEL-020).
SECTIONS = {
    "NEW_ISSUE": ["Duplicate Analysis", "Specification Analysis", "Historical Analysis", "QA Recommendation"],
    "FIXED_ISSUE": ["Root Cause Review", "Resolution Review", "Specification Consistency", "Regression Risk", "Verification TC"],
    "SPEC_DECISION": ["R&amp;D Claim", "Specification Evidence", "Historical Decisions", "QA Analysis"],
    "COMMENT": ["New Comment"],
    "SPEC_COVERAGE": ["Specification Change", "Historical Issue Coverage", "Checklist Coverage", "Coverage Gap"],
}
REVIEW_WORDS = ("승인", "거절", "근거 추가 필요", "검토자", "질문 답변")


def _copy_product_config(root: Path, name: str) -> None:
    target = root / "config" / "products"
    target.mkdir(parents=True, exist_ok=True)
    (target / name).write_text((REPO_ROOT / "config" / "products" / name).read_text(encoding="utf-8"), encoding="utf-8")


def _write_manifest(root: Path, slug: str, assets: list[dict], synced_at: str = "2026-09-30T01:02:00+00:00") -> None:
    folder = root / "data" / "product_knowledge" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.json").write_text(json.dumps({"synced_at": synced_at, "assets": assets}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """하루치 기준 + 다음 날 분석 5종이 모두 생긴 저장소."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    _copy_product_config(root, "vxvue.yaml")
    cfg = make_settings(root, tmp_path / "ws")
    store = DailyQaStore(cfg.db_path)
    workbook = make_tc_workbook(tmp_path / "(TC) VXvue_TestCase.xlsx", [("TC_1", "VP-10", "목록 표시"), ("TC_2", "VP-11", "검색")])
    srs = [srs_item("VP-10", "01-01", "목록"), srs_item("VP-11", "01-02", "검색", text="검색 결과를 표시한다")]
    issues = [
        issue_item("VP-100", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open"),
        issue_item("VP-101", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open"),
        issue_item("VP-102", "2026-09-28T00:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1"]),
    ]
    comments = {"VP-102": [comment("c1", "처음 댓글입니다 확인 부탁")]}
    calls: list = []

    def run(day):
        polarion = FakePolarion(srs, issues, comments)
        return run_daily(cfg, today=day, polarion_factory=lambda: polarion, runner=FakeRunner(analysis_producer(calls)),
                         inputs=Inputs(tc_paths=[workbook]), rules_state=rules_ok(tmp_path), store=store,
                         send_email=lambda *_: {"status": "sent"}, spec_chunks_loader=lambda: ([], {}, []))

    baseline = run(DAY1)
    issues.append(issue_item("VP-200", "2026-09-29T09:00:00Z", ["VP-11"], review="", status="open"))
    issues[0] = issue_item("VP-100", "2026-09-29T09:00:00Z", ["VP-10"], review="lab_fixed", status="resolved",
                           cause="캐시 초기화 누락", action="초기화 추가")
    issues[1] = issue_item("VP-101", "2026-09-29T09:00:00Z", ["VP-10"], review="lab_inspec", status="open")
    issues[2] = issue_item("VP-102", "2026-09-29T09:00:00Z", ["VP-10"], review="", status="open", comment_ids=["c1", "c2"])
    comments["VP-102"].append(comment("c2", "원인은 캐시 초기화 누락으로 확인되었습니다."))
    srs[1] = srs_item("VP-11", "01-02", "검색", text="검색 결과를 최신순으로 표시한다")
    changed = run(DAY2)

    choice = dash.ProductChoice(cfg, store, [cfg.product])
    monkeypatch.setattr(dash, "choose", lambda product="": choice if product in ("", cfg.product, cfg.slug) else None)
    documents = Storage(db_path=cfg.db_path)
    monkeypatch.setattr(dash, "_documents_storage", lambda _cfg: documents)
    app = FastAPI()
    app.include_router(router_module.router, prefix="/qa-agent")
    return {"cfg": cfg, "store": store, "client": TestClient(app), "baseline": baseline, "changed": changed,
            "calls": calls, "root": root, "documents": documents}


def _finding_id(world, analysis_type: str) -> int:
    return world["store"].list_findings(run_id=world["changed"]["run_id"], analysis_type=analysis_type)[0]["id"]


# -- 대시보드 (REQ-QAINTEL-019) -------------------------------------------------------


def test_dashboard_renders_summary_cards_and_recent_runs(world):
    assert world["changed"]["status"] == "SUCCESS"
    response = world["client"].get("/qa-agent")
    assert response.status_code == 200
    page = response.text
    for heading in ("지식 문서 업로드 현황", "분석 실행 <span class=\"hint\">· VXvue</span>", "최신 분석 결과", "최근 실행"):
        assert heading in page
    assert "[VP-200]" in page and "[VP-100]" in page
    assert f"/qa-agent/runs/{world['changed']['run_id']}" in page
    for kind in ("NEW_ISSUE", "FIXED_ISSUE", "SPEC_DECISION", "COMMENT", "SPEC_COVERAGE"):
        assert f"/qa-agent/findings/{_finding_id(world, kind)}" in page


def test_dashboard_run_period_inputs_default_to_yesterday_and_today(world):
    """[지금 실행] 달력의 기본값은 시작일 = 전날, 종료일 = 오늘이다 (REQ-QAINTEL-027)."""
    today = datetime.now(dash.KST).date()
    page = world["client"].get("/qa-agent").text
    assert f'id="run-since" value="{(today - timedelta(days=1)).isoformat()}"' in page
    assert f'id="run-until" value="{today.isoformat()}"' in page
    assert f'max="{today.isoformat()}"' in page


def test_dashboard_has_no_review_or_answer_forms(world):
    page = world["client"].get("/qa-agent").text
    assert "/decision" not in page and "/answer" not in page and 'name="reviewer"' not in page
    for word in REVIEW_WORDS:
        assert word not in page


def test_dashboard_unknown_product_is_404(world):
    assert world["client"].get("/qa-agent?product=NoSuchProduct").status_code == 404


def test_dashboard_on_empty_store_renders(tmp_path, monkeypatch):
    """실행 기록이 하나도 없는 첫날에도 화면이 열린다."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    cfg = make_settings(root, tmp_path / "ws")
    choice = dash.ProductChoice(cfg, DailyQaStore(cfg.db_path), [cfg.product])
    monkeypatch.setattr(dash, "choose", lambda product="": choice)
    monkeypatch.setattr(dash, "_documents_storage", lambda _cfg: Storage(db_path=cfg.db_path))
    app = FastAPI()
    app.include_router(router_module.router, prefix="/qa-agent")
    response = TestClient(app).get("/qa-agent")
    assert response.status_code == 200
    assert "수집된 지식 문서가 없습니다" in response.text


def test_status_json(world):
    body = world["client"].get("/qa-agent/status").json()
    assert body["product"] == "VXvue" and body["slug"] == "vxvue"
    assert body["running"] is False
    assert body["last_run"]["id"] == world["changed"]["run_id"] and body["last_run"]["status"] == "SUCCESS"
    assert body["claude_limit"] is None


def test_status_json_shows_running_while_locked(world):
    world["cfg"].lock_path.parent.mkdir(parents=True, exist_ok=True)
    world["cfg"].lock_path.write_text(json.dumps({"pid": 1, "started_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    assert world["client"].get("/qa-agent/status").json()["running"] is True


# -- 지식 문서 목록 (REQ-QAINTEL-022) --------------------------------------------------


def _knowledge_rows(page: str) -> str:
    start = page.index("지식 문서 업로드 현황")
    return page[start:page.index("</table>", start)]


def test_knowledge_list_for_alm_product_hides_spec_rules_prompts_and_revision(world):
    _write_manifest(world["root"], "vxvue", [
        {"kind": "specification", "file_name": "(사양서) VXvue 사양서1(260930).pdf", "collected_at": "2026-09-30T01:00:00+00:00"},
        {"kind": "manual", "file_name": "(매뉴얼) VXvue Operation Manual.V1.1.0W2_KO.docx", "revision": "V1.1.0W2",
         "collected_at": "2026-09-28T08:05:00+00:00", "modified": "2026-09-01T05:42:00+00:00"},
        {"kind": "testcase", "file_name": "(TC) RA16-148-002_VXvue_TestCase.xlsx", "collected_at": "2026-09-08T02:12:00+00:00"},
        {"kind": "qa_rules", "file_name": "(QA규칙) VXvue_QA_Rules_Rev1.17.md", "collected_at": "2026-09-08T02:12:00+00:00"},
        {"kind": "instruction_prompt", "file_name": "(지침) QA 지침 프롬프트.md", "collected_at": "2026-09-08T02:12:00+00:00"},
        {"kind": "manual", "file_name": "(매뉴얼) Broken Guide.docx", "collected_at": "2026-09-08T02:12:00+00:00", "error": "parse"},
    ])
    rows = _knowledge_rows(world["client"].get("/qa-agent").text)
    assert "담당자가 직접 올려야 하는 문서: 매뉴얼, TC·Checklist" in rows
    assert "Operation Manual.V1.1.0W2_KO.docx" in rows and "RA16-148-002_VXvue_TestCase.xlsx" in rows
    assert "사양서1" not in rows                    # ALM 자동 추출 제품
    assert "QA_Rules" not in rows and "지침 프롬프트" not in rows
    assert "리비전" not in rows and "<td>V1.1.0W2</td>" not in rows
    assert "2026-09-28 17:05" in rows and "2026-09-01 14:42" in rows   # 한국 시간
    assert "마지막 수집 2026-09-30 10:02" in world["client"].get("/qa-agent").text
    assert "Broken Guide.docx" in rows and "읽지 못함" in rows


def test_knowledge_list_kind_column_does_not_wrap():
    """종류 이름(`TC·Checklist`)은 한 줄로 보인다 (REQ-QAINTEL-022 지킬 것).

    공용 `.history` 표는 둘째 열을 8% 로 두고 글자를 줄바꿈한다. 지식 문서 표의 첫 열 규칙에
    줄바꿈 금지와 이름 전체가 들어가는 폭이 함께 있어야 한다. 실제 한 줄 여부는 브라우저로 확인한다.
    """
    css = (REPO_ROOT / "app/modules/qa_agent/templates/_intel_style.html").read_text(encoding="utf-8")
    rules = re.findall(r"([^{}]+)\{([^{}]*)\}", css)
    first = [body for selector, body in rules
             if ".history.qa-docs td:nth-child(1)" in selector and ".history.qa-docs th:nth-child(1)" in selector]
    assert first, "지식 문서 표 첫 열 규칙이 없다"
    body = first[-1].replace(" ", "")
    assert "white-space:nowrap" in body
    width = re.search(r"width:(\d+)px", body)
    assert width and int(width.group(1)) >= 120, body   # 'TC·Checklist' + 칸 여백


def test_knowledge_list_shows_only_latest_revision_of_same_document(world):
    """같은 논리 문서가 지식 폴더와 Knowledge 화면 등록으로 두 판 있으면 최신 판만 보인다."""
    _write_manifest(world["root"], "vxvue", [
        {"kind": "manual", "file_name": "(매뉴얼) VXvue Operation Manual.V1.1.0W2_KO.docx", "collected_at": "2026-09-28T08:05:00+00:00"},
        {"kind": "manual", "file_name": "(매뉴얼) VXvue DICOM Conformance Statement.V4.4W1.docx", "collected_at": "2026-09-28T08:05:00+00:00"},
    ])
    for name in ("VXvue Operation Manual.V1.0.11_KO.pdf", "VXvue DICOM Conformance Statement.V4.2.pdf", "VXvue Service Manual.V1.0.11_KO.pdf"):
        world["documents"].add_document("manual", "VXvue", "1.0", "Rev.1", name, world["root"] / name)
    rows = _knowledge_rows(world["client"].get("/qa-agent").text)
    assert "V1.1.0W2_KO.docx" in rows and "V1.0.11_KO.pdf</td>" not in rows.replace("Service Manual.V1.0.11_KO.pdf", "")
    assert "V4.4W1.docx" in rows and "V4.2.pdf" not in rows
    assert "Service Manual.V1.0.11_KO.pdf" in rows            # 다른 논리 문서는 그대로
    assert "Knowledge 화면 등록" in rows


def test_latest_revisions_keeps_both_when_revisions_are_not_comparable():
    rows = [{"kind_code": "manual", "file_name": "Guide(260901).pdf"}, {"kind_code": "manual", "file_name": "Guide.V1.2.pdf"}]
    assert dash._latest_revisions(rows) == rows
    older_newer = [{"kind_code": "manual", "file_name": "Guide.V1.2.pdf"}, {"kind_code": "manual", "file_name": "Guide.V1.10.pdf"}]
    assert [row["file_name"] for row in dash._latest_revisions(older_newer)] == ["Guide.V1.10.pdf"]
    other_kind = [{"kind_code": "manual", "file_name": "Guide.V1.2.pdf"}, {"kind_code": "testcase", "file_name": "Guide.V1.10.pdf"}]
    assert dash._latest_revisions(other_kind) == other_kind


def test_knowledge_list_for_non_alm_product_includes_specification(tmp_path):
    root = tmp_path / "root"
    _copy_product_config(root, "bellalun-viewer.yaml")
    _write_manifest(root, "bellalun-viewer", [
        {"kind": "specification", "file_name": "(사양서) Bellalun 사양서.docx", "collected_at": "2026-09-30T01:00:00+00:00"},
        {"kind": "qa_rules", "file_name": "(QA규칙) Bellalun_QA_Rules_Rev1.0.md", "collected_at": "2026-09-30T01:00:00+00:00"},
    ])
    data = dash.knowledge_uploads("Bellalun Viewer", root)
    assert data["kinds"] == ["사양서", "매뉴얼", "TC·Checklist"]
    assert [row["file_name"] for row in data["rows"]] == ["(사양서) Bellalun 사양서.docx"]
    assert all("revision" not in row for row in data["rows"])


def test_knowledge_list_survives_unreadable_document_table(world, monkeypatch):
    _write_manifest(world["root"], "vxvue", [{"kind": "manual", "file_name": "(매뉴얼) A.V1.docx", "collected_at": "2026-09-28T08:05:00+00:00"}])

    class Broken:
        def active_documents(self, kind, product):
            raise RuntimeError("db locked")

    data = dash.knowledge_uploads("VXvue", world["root"], Broken())
    assert [row["file_name"] for row in data["rows"]] == ["(매뉴얼) A.V1.docx"]


# -- 상세 화면 (REQ-QAINTEL-020) -------------------------------------------------------


@pytest.mark.parametrize("analysis_type", sorted(SECTIONS))
def test_finding_detail_shows_sections_of_its_analysis_type(world, analysis_type):
    response = world["client"].get(f"/qa-agent/findings/{_finding_id(world, analysis_type)}")
    assert response.status_code == 200
    page = response.text
    for heading in SECTIONS[analysis_type]:
        assert f"<h3>{heading}" in page, heading
    others = {heading for kind, headings in SECTIONS.items() if kind != analysis_type for heading in headings} - set(SECTIONS[analysis_type])
    assert not [heading for heading in others if f"<h3>{heading}" in page]
    assert "이 분석을 만든 변경 이벤트" in page
    assert "/decision" not in page and 'name="reviewer"' not in page


def test_finding_detail_unknown_id_is_404(world):
    assert world["client"].get("/qa-agent/findings/999999").status_code == 404


def test_run_detail_stage_status_column_does_not_wrap(world):
    """실행 상세 단계 표의 상태 이름은 한 줄로 보인다 (REQ-QAINTEL-020 지킬 것)."""
    css = (REPO_ROOT / "app/modules/qa_agent/templates/_intel_style.html").read_text(encoding="utf-8")
    rules = [body.replace(" ", "") for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)
             if ".history.qa-stages td:nth-child(2)" in selector]
    assert rules and "white-space:nowrap" in rules[-1]
    page = world["client"].get(f"/qa-agent/runs/{world['changed']['run_id']}").text
    assert 'class="history qa-stages"' in page


def test_run_detail_lists_events_files_and_cards(world):
    run_id = world["changed"]["run_id"]
    page = world["client"].get(f"/qa-agent/runs/{run_id}").text
    assert "impact_checklist_draft.xlsx" in page
    assert f"/qa-agent/runs/{run_id}/files/impact_checklist_draft.xlsx" in page
    assert "VP-200" in page and "VP-11" in page
    assert world["client"].get("/qa-agent/runs/20000101-000000-vxvue").status_code == 404


def test_run_file_download_and_path_limits(world, tmp_path):
    run_id = world["changed"]["run_id"]
    client = world["client"]
    ok = client.get(f"/qa-agent/runs/{run_id}/files/impact_checklist_draft.xlsx")
    assert ok.status_code == 200 and ok.content[:2] == b"PK"
    out_dir = world["cfg"].output_dir / run_id
    (out_dir / "notes.txt").write_text("secret", encoding="utf-8")
    (world["cfg"].output_dir / "outside.json").write_text("{}", encoding="utf-8")
    assert client.get(f"/qa-agent/runs/{run_id}/files/notes.txt").status_code == 404          # 허용 확장자 아님
    assert client.get(f"/qa-agent/runs/{run_id}/files/missing.xlsx").status_code == 404
    assert client.get(f"/qa-agent/runs/{run_id}/files/..%2Foutside.json").status_code == 404   # 실행 폴더 밖
    assert client.get(f"/qa-agent/runs/{run_id}/files/%2E%2E%5Coutside.json").status_code == 404


def test_run_file_rejects_run_id_outside_output_dir(world):
    """실행 번호 자리에 `..` 를 넣어 실행 폴더 밖 파일을 내려받을 수 없다 (코드 리뷰 2026-10-01)."""
    client, output_dir = world["client"], world["cfg"].output_dir
    (output_dir / "secret.json").write_text('{"s": 1}', encoding="utf-8")
    (output_dir.parent / "leak.json").write_text('{"leak": 1}', encoding="utf-8")
    for url in ("/qa-agent/runs/%2E%2E/files/leak.json", "/qa-agent/runs/./files/secret.json",
                "/qa-agent/runs/..%5C..%5Coutput/files/leak.json", "/qa-agent/runs/20260930-073000-vxvue%5C..%5C../files/leak.json"):
        assert client.get(url).status_code == 404, url


# -- 기간 조회 (REQ-QAINTEL-024) -------------------------------------------------------


def test_period_view_groups_findings_by_subject(world):
    # 기간은 Finding 을 만든(저장한) 시각 기준이다(REQ-QAINTEL-024 순서 1). 테스트 실행의 저장 시각은
    # 가짜 실행일(DAY1·DAY2)이 아니라 실제 오늘이므로, 날짜를 고정하면 다음 날부터 실패한다.
    today = datetime.now(dash.KST).date()
    page = world["client"].get(f"/qa-agent/period?start={(today - timedelta(days=1)).isoformat()}&end={today.isoformat()}").text
    assert "VP-200" in page and "VP-100" in page
    old = world["client"].get("/qa-agent/period?start=2020-01-01&end=2020-01-02").text
    assert "VP-200" not in old and "VP-100" not in old   # 기간 밖 Finding 은 보이지 않는다
    assert world["client"].get("/qa-agent/period").status_code == 200   # 기본 기간(최근 7일)


@pytest.mark.parametrize(("query", "message"), [
    ("start=2026-09-30&end=2026-09-01", "시작일이 종료일보다 늦습니다."),
    ("start=2026/09/01&end=2026-09-30", "날짜는 YYYY-MM-DD 로 입력하세요."),
    ("start=2024-01-01&end=2026-09-30", "조회 기간은 366일까지입니다."),
])
def test_period_view_rejects_bad_ranges(world, query, message):
    response = world["client"].get(f"/qa-agent/period?{query}")
    assert response.status_code == 400 and message in response.text


def test_period_boundary_of_366_days_is_allowed(world):
    assert world["client"].get("/qa-agent/period?start=2025-09-30&end=2026-09-30").status_code == 200
    assert world["client"].get("/qa-agent/period?start=2025-09-29&end=2026-09-30").status_code == 400


# -- 지금 실행 (REQ-QAINTEL-021·027) ---------------------------------------------------


class _Popen:
    def __init__(self):
        self.commands: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        return type("P", (), {"pid": 4321})()


@pytest.fixture
def launcher(world, monkeypatch):
    """`launch_detached` 는 진짜로 돌리고 프로세스 생성만 가짜로 바꾼다."""
    popen = _Popen()
    original = scheduled_jobs.launch_detached
    monkeypatch.setattr(scheduled_jobs, "load", lambda product=None: world["cfg"])
    monkeypatch.setattr(scheduled_jobs, "launch_detached", lambda *args, **kwargs: original(*args, popen=popen, **kwargs))
    return popen


def test_run_now_launches_detached_process(world, launcher):
    response = world["client"].post("/qa-agent/runs", data={"product": "VXvue"})
    assert response.status_code == 202 and response.json()["status"] == "launched"
    command = launcher.commands[0]
    assert command[-4:] == ["--product", "VXvue", "--trigger", "manual"]


def test_run_now_passes_period_and_drops_until_today(world, launcher):
    today = datetime.now(dash.KST).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    world["client"].post("/qa-agent/runs", data={"product": "VXvue", "since": yesterday, "until": today.isoformat()})
    assert launcher.commands[-1][-2:] == ["--since", yesterday]
    past = (today - timedelta(days=2)).isoformat()
    world["client"].post("/qa-agent/runs", data={"product": "VXvue", "since": past, "until": yesterday})
    assert launcher.commands[-1][-4:] == ["--since", past, "--until", yesterday]


@pytest.mark.parametrize("period", [
    {"since": "2026-09-30", "until": "2026-09-01"},
    {"since": "not-a-date"},
    {"until": "2999-01-01"},
])
def test_run_now_rejects_bad_period_without_launching(world, launcher, period):
    response = world["client"].post("/qa-agent/runs", data={"product": "VXvue", **period})
    assert response.status_code == 400 and response.json()["detail"]
    assert launcher.commands == []


def test_run_now_is_409_while_locked(world, launcher):
    world["cfg"].lock_path.parent.mkdir(parents=True, exist_ok=True)
    world["cfg"].lock_path.write_text(json.dumps({"pid": 1, "started_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    response = world["client"].post("/qa-agent/runs", data={"product": "VXvue"})
    assert response.status_code == 409 and response.json()["detail"] == "QA Agent가 이미 실행 중입니다."
    assert launcher.commands == []


def test_run_now_unknown_product_is_404(world, launcher):
    assert world["client"].post("/qa-agent/runs", data={"product": "NoSuchProduct"}).status_code == 404
    assert launcher.commands == []


def test_run_now_warns_when_claude_limit_is_active(world, launcher):
    reset = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    limit = claude_limits.LimitInfo(kind=claude_limits.KIND_SESSION, reset_at=reset, message="You've hit your session limit")
    world["store"].set_state(world["cfg"].state_key(STATE_CLAUDE_LIMIT), claude_limits.dump(limit))
    response = world["client"].post("/qa-agent/runs", data={"product": "VXvue"})
    assert response.status_code == 202
    assert "AI 분석은 대기로 남습니다" in response.json()["warning"]
    page = world["client"].get("/qa-agent").text
    assert 'id="limit-banner"' in page and "세션" in page
    assert world["client"].get("/qa-agent/status").json()["claude_limit"]["kind"] == claude_limits.KIND_SESSION


def test_expired_limit_is_not_shown(world):
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    limit = claude_limits.LimitInfo(kind=claude_limits.KIND_SESSION, reset_at=past)
    world["store"].set_state(world["cfg"].state_key(STATE_CLAUDE_LIMIT), claude_limits.dump(limit))
    assert 'id="limit-banner"' not in world["client"].get("/qa-agent").text


# -- 사용법 화면 (REQ-QAAGENT-014) ----------------------------------------------------


def test_guide_explains_dashboard_run_now_period_and_limits(world):
    """사용법 화면이 대시보드·지금 실행·기간 조회·한도를 설명하고, 단일 이슈 분석 설명도 남아 있다."""
    page = world["client"].get("/qa-agent/guide").text
    for heading in ("A. 매일 점검 대시보드", "B. 지금 실행", "C. 기간 조회", "D. Claude 사용량 한도", "단일 이슈 분석 사용법",
                    "0. 이 기능이 대신하는 일"):
        assert heading in page, heading
    for column in ("신규 Issue", "Fixed", "Spec", "Comment Update", "SRS Update"):
        assert f"<td>{column}</td>" in page, column
    assert 'href="/qa-agent/period"' in page and 'href="/qa-agent/issue-analysis"' in page


def test_guide_running_message_matches_run_now_conflict_response(world, launcher):
    """사용법이 인용한 '이미 실행 중' 문장은 [지금 실행] 409 응답 문장과 같다."""
    world["cfg"].lock_path.parent.mkdir(parents=True, exist_ok=True)
    world["cfg"].lock_path.write_text(json.dumps({"pid": 1, "started_at": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    response = world["client"].post("/qa-agent/runs", data={"product": world["cfg"].product})
    assert response.status_code == 409
    assert response.json()["detail"] in world["client"].get("/qa-agent/guide").text

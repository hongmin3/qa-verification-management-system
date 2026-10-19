"""Validates: REQ-QAINTEL-046..047. HTTP filters and processing history."""
from tests.test_qa_intel_dashboard import world


def test_search_creator_state_and_processing_http(world):
    store, client = world['store'], world['client']
    finding = store.list_findings(analysis_type='NEW_ISSUE')[0]
    store.storage.qa_save_reporters('vxvue', [{'id': finding['subject'], 'reporter_id': 'creator', 'reporter_name': '최초 작성자'}])
    response = client.get('/qa-agent/results', params={'category': 'issues', 'reporter': 'creator', 'subject': finding['subject']})
    assert response.status_code == 200 and '최초 작성자' in response.text
    response = client.post(f"/qa-agent/results/{finding['id']}/processing",
        data={'product': 'vxvue', 'state': 'done', 'actor': 'QA 담당', 'note': '추가 조치 없음'})
    assert response.status_code == 200 and '추가 조치 없음' in response.text
    response = client.get('/qa-agent/results', params={'state': 'done', 'reporter': 'creator'})
    assert f"/findings/{finding['id']}" in response.text
    assert store.get_finding(finding['id'])['summary'] == finding['summary']
    assert client.get('/qa-agent/results?category=invalid').status_code == 400


def test_known_review_verdict_is_in_review_filter(world):
    fid = world['store'].add_finding(world['changed']['run_id'], 'qa-spec-coverage-analysis', 'review',
        {'subject': 'VP-999', 'analysis_type': 'SPEC_COVERAGE', 'verdict': 'PARTIALLY_COVERED', 'summary': '검토 필터 대상'}, 'vxvue')
    response = world['client'].get('/qa-agent/results', params={'level': '검토 필요'})
    assert response.status_code == 200 and f'/findings/{fid}' in response.text


def test_dashboard_sorting_in_http_browser(world, monkeypatch):
    import pytest
    import threading
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path
    from urllib.parse import urlparse
    from app.modules.qa_agent import dashboard as dash
    playwright = pytest.importorskip('playwright.sync_api')
    choice = dash.ProductChoice(replace(world['cfg'], savings_enabled=True), world['store'], ['VXvue'])
    monkeypatch.setattr(dash, 'choose', lambda product='': choice)
    world['store'].storage.qa_save_reporters('vxvue', [
        {'id':'VP-100','reporter_id':'a','reporter_name':'가 작성자','updated':'2026-10-13T01:00:00Z'},
        {'id':'VP-200','reporter_id':'b','reporter_name':'나 작성자','updated':'2026-10-14T01:00:00Z'}])
    static_root = Path(__file__).resolve().parents[1] / 'app/web'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith('/static/'):
                file = static_root / path.lstrip('/')
                body = file.read_bytes() if file.is_file() else b''
                kind = 'text/javascript' if file.suffix == '.js' else 'text/css'
            else:
                response = world['client'].get(self.path)
                body, kind = response.content, response.headers.get('content-type', 'text/html')
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for width in (1366, 1920):
                    page = browser.new_page(viewport={'width':width, 'height':1080})
                    errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/qa-agent', wait_until='domcontentloaded')
                    assert page.locator('.qa-card h4').first.inner_text().startswith('[VP-200]')
                    assert '2026-10-14 10:00' in page.locator('.qa-card').first.inner_text()
                    page.locator('select[name=sort]').select_option('author')
                    page.get_by_role('button', name='조회', exact=True).first.click()
                    page.wait_for_url('**sort=author**')
                    assert page.locator('.qa-card h4').first.inner_text().startswith('[VP-100]')
                    assert '가 작성자' in page.locator('.qa-card').first.inner_text()
                    assert not errors
                    page.close()
            finally:
                browser.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


# -- 분석결과 목록: 이슈당 카드 한 장, 작성자·업데이트 강조, 필터 (TEST-QAINTEL-029) ----------------------------
# Validates: REQ-QAINTEL-019, REQ-QAINTEL-046
# 합성 이름·번호만 쓴다. 운영에서 본 모양(이전 형식 결과, 같은 이슈의 여러 분석, 번호만 남은 빈 댓글)을 모두 넣는다.
import re
import threading
from dataclasses import replace
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from app.modules.daily_qa import report
from app.modules.qa_agent import dashboard as dash
from app.modules.qa_agent import issue_body, result_views
from tests.test_qa_finding_detail_layout import comment_after, put_event, put_finding, site  # noqa: F401
from tests.test_qa_issue_body import snapshot_item, write

MARK = "목록시험"
PLACEHOLDER = {"created": "", "text": ""}      # 실제 스냅샷 댓글에는 author 키가 아예 없다
LONG_SUMMARY = "여러 줄로 길게 이어지는 요약 문장입니다. " * 30            # 두 줄 제한이 없으면 열 줄이 넘는다
OLD, NEW = "2026-10-14T04:35:47+00:00", "2026-10-14T23:05:19+00:00"


def mine(cards):
    return [card for card in cards if str(card["title"]).startswith(MARK)]


def seed(site, subject, kind="FIXED_ISSUE", verdict="SPEC_UNDEFINED", created=NEW, **extra):
    data = {"subject": subject, "subject_title": f"{MARK} {subject}", "analysis_type": kind, "verdict": verdict,
            "summary": f"{subject} 요약", **extra}
    if not kind:
        data.pop("analysis_type")
    return put_finding(site, data, created=created)


@pytest.fixture
def listing(site):  # noqa: F811
    """같은 이슈 두 분석·이전 형식·분석 뒤 변경·이름 없는 작성자·작성자 정보 없는 이슈·사양 결과."""
    issue_body.clear_cache()
    storage = site["store"].storage
    storage.qa_save_reporters("vxvue", [
        {"id": "VP-7287", "reporter_id": "9001001", "reporter_name": "테스트작성자", "updated": "2026-10-14T09:09:00Z"},
        {"id": "VP-7300", "reporter_id": "9001002", "reporter_name": "샘플작성자", "updated": "2026-10-16T01:00:00Z"},
        {"id": "VP-7400", "reporter_id": "9001003", "reporter_name": "", "updated": "2026-10-13T01:00:00Z"},
        {"id": "VP-7500", "reporter_id": "9001001", "reporter_name": "테스트작성자", "updated": "2026-10-12T01:00:00Z"}])
    comments = [{"id": f"p{n}", **PLACEHOLDER} for n in range(6)]          # 운영 스냅샷 댓글의 대부분이 번호만 남은 빈 값이다
    write(site["cfg"].issue_snapshot_dir, "2026-10-15", [
        snapshot_item("VP-7287", updated="2026-10-14T09:09:00Z", comments=comments),
        snapshot_item("VP-7300", updated="2026-10-16T01:00:00Z", comments=comments),     # 분석 뒤에 이슈가 바뀜
        snapshot_item("VP-7400", updated="2026-10-13T01:00:00Z", comments=comments),
        snapshot_item("VP-7500", updated="2026-10-12T01:00:00Z", comments=comments),
        snapshot_item("VP-7600", updated="2026-10-11T01:00:00Z", comments=comments)])
    seed(site, "VP-7287", created=OLD)
    first = put_event(site, "VP-7287", "ISSUE_COMMENT_ADDED", comment_after("c1", "2026-10-14T08:00:00Z"), "2026-10-14T08:01:00+00:00")
    latest = seed(site, "VP-7287", created=NEW, event_ids=[first])
    put_event(site, "VP-7287", "ISSUE_COMMENT_ADDED", comment_after("c9", "2026-10-15T00:30:00Z"), "2026-10-15T00:31:00+00:00",
              status="pending", key="later")                                    # 최신 분석 뒤에 생긴 대기 이벤트
    seed(site, "VP-7300", kind="NEW_ISSUE", verdict="SPEC_VIOLATION", created="2026-10-14T05:00:00+00:00")
    seed(site, "VP-7400", kind="COMMENT", verdict="ROOT_CAUSE_INFORMATION", created="2026-10-14T06:00:00+00:00", summary=LONG_SUMMARY)
    seed(site, "VP-7500", kind="", verdict="TC 없음", created="2026-10-13T06:00:00+00:00")   # 종류가 빈 이전 형식
    seed(site, "VP-7600", kind="NEW_ISSUE", verdict="CONSISTENT_WITH_SPEC", created="2026-10-12T06:00:00+00:00")  # 작성자 정보 없음
    seed(site, "VP-9910", kind="SPEC_COVERAGE", verdict="PARTIALLY_COVERED", created="2026-10-14T07:00:00+00:00")
    return {**site, "latest": latest, "choice": dash.choose("")}


def subjects(data):
    return [card["subject"] for card in mine(data["cards"])]


def test_issue_view_keeps_latest_finding_per_subject(listing):
    data = result_views.search(listing["choice"], {"view": "issue", "q": MARK})
    assert subjects(data).count("VP-7287") == 1 and data["total"] == 6
    card = next(card for card in data["cards"] if card["subject"] == "VP-7287")
    assert card["id"] == listing["latest"] and card["history_count"] == 2
    every = result_views.search(listing["choice"], {"view": "all", "q": MARK})
    assert every["total"] == 7 and subjects(every).count("VP-7287") == 2
    assert result_views.search(listing["choice"], {"q": MARK})["total"] == 6            # 기본은 이슈별 보기
    assert result_views.search(listing["choice"], {"q": MARK, "run_id": "20260101-000000"})["total"] == 0


def test_search_box_matches_reporter_name_id_and_number(listing):
    by_name = result_views.search(listing["choice"], {"q": "테스트작성자"})
    assert sorted(subjects(by_name)) == ["VP-7287", "VP-7500"]
    assert subjects(result_views.search(listing["choice"], {"q": "9001002"})) == ["VP-7300"]
    assert subjects(result_views.search(listing["choice"], {"q": "7400"})) == ["VP-7400"]


def test_legacy_kind_is_in_default_list_with_tag_and_chip(listing):
    data = result_views.search(listing["choice"], {"q": MARK})
    legacy = next(card for card in data["cards"] if card["subject"] == "VP-7500")
    assert legacy["kind"] == "이전 형식" and legacy["legacy"] is True and legacy["freshness"]["state"] in ("current", "unknown")
    only = result_views.search(listing["choice"], {"q": MARK, "category": "legacy"})
    assert subjects(only) == ["VP-7500"] and only["total"] == 1
    assert "VP-7500" not in subjects(result_views.search(listing["choice"], {"q": MARK, "category": "issues"}))


def test_quick_changed_filter_uses_freshness(listing):
    data = result_views.search(listing["choice"], {"q": MARK, "quick": "changed"})
    assert data["total"] == 2 and sorted(subjects(data)) == ["VP-7287", "VP-7300"]
    assert all(card["freshness"]["state"] == "changed_after" for card in data["cards"])
    assert next(card for card in data["cards"] if card["subject"] == "VP-7287")["freshness"]["after_events"] == 1


def test_quick_changed_filter_counts_and_pages_after_the_filter(listing):
    """분석 이후 변경은 DB 조건으로 못 쓰므로 파이썬에서 판정한다. 결과 수와 페이지는 판정 뒤의 값이어야 한다."""
    names = [f"VP-8{n:03d}" for n in range(60)]
    write(listing["cfg"].issue_snapshot_dir, "2026-10-16", [
        snapshot_item(name, updated="2026-10-17T00:00:00Z" if number < 52 else "2026-10-13T00:00:00Z", comments=[])
        for number, name in enumerate(names)])                                    # 앞의 52건만 분석 뒤에 바뀌었다
    issue_body.clear_cache()
    for name in names:
        put_finding(listing, {"subject": name, "subject_title": "페이지시험", "analysis_type": "NEW_ISSUE", "verdict": "SPEC_VIOLATION",
                              "summary": "요약"}, created="2026-10-14T00:00:00+00:00")
    one = result_views.search(listing["choice"], {"q": "페이지시험", "quick": "changed"})
    two = result_views.search(listing["choice"], {"q": "페이지시험", "quick": "changed", "page": 2})
    assert one["total"] == 52 and one["pages"] == 2 and len(one["cards"]) == 50 and len(two["cards"]) == 2
    assert not {card["id"] for card in one["cards"]} & {card["id"] for card in two["cards"]}
    assert all(card["freshness"]["state"] == "changed_after" for card in one["cards"] + two["cards"])
    plain = result_views.search(listing["choice"], {"q": "페이지시험"})
    assert plain["total"] == 60 and plain["pages"] == 2 and len(plain["cards"]) == 50


def test_quick_review_and_open_filters(listing):
    review = result_views.search(listing["choice"], {"q": MARK, "quick": "review"})
    assert review["total"] >= 1 and all(card["level"] == "검토 필요" for card in review["cards"])
    assert "VP-7500" in subjects(review) and "VP-7300" not in subjects(review)
    finding = next(card for card in result_views.search(listing["choice"], {"q": MARK})["cards"] if card["subject"] == "VP-7400")
    listing["store"].storage.qa_set_processing(finding["id"], "vxvue", "done", "QA 담당", "확인")
    opened = result_views.search(listing["choice"], {"q": MARK, "quick": "open"})
    assert "VP-7400" not in subjects(opened) and opened["total"] == 5
    with pytest.raises(ValueError):
        result_views.search(listing["choice"], {"quick": "unknown"})
    with pytest.raises(ValueError):
        result_views.search(listing["choice"], {"view": "everything"})


def test_card_why_and_history_fields(listing):
    data = result_views.search(listing["choice"], {"q": MARK})
    card = next(card for card in data["cards"] if card["subject"] == "VP-7287")
    assert [step["label"] for step in card["trigger"]] == ["새 댓글"] and card["trigger"][0]["time"] == "2026-10-14 17:00"
    assert next(card for card in data["cards"] if card["subject"] == "VP-7600")["trigger"] == []
    assert dash.card({**listing["store"].get_finding(listing["latest"])})["freshness"]["state"] == "unknown"


def test_relative_update_time_words():
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    assert dash.ago("2026-10-15T11:59:40Z", now) == "방금"
    assert dash.ago("2026-10-15T11:40:00Z", now) == "20분 전"
    assert dash.ago("2026-10-15T09:00:00Z", now) == "3시간 전"
    assert dash.ago("2026-10-12T12:00:00Z", now) == "3일 전"
    assert dash.ago("", now) == "" and dash.ago("바보", now) == ""


def test_author_grouping_follows_mail_order(listing):
    data = result_views.search(listing["choice"], {"q": MARK, "group": "author"})
    mail = report._issue_groups(data["items"], lambda path: path)
    assert [(g["name"], g["id"], [i["subject"] for i in g["issues"]]) for g in mail] == \
           [(g["name"], g["id"], [c["subject"] for c in g["cards"]]) for g in data["groups"]]
    assert [g["id"] for g in data["groups"]][-1] == "ID 미확인" and len(data["groups"]) >= 4      # 작성자 정보 없는 이슈가 맨 뒤
    assert next(g for g in data["groups"] if g["id"] == "9001003")["name"] == "이름 미확인"


def cards_by_subject(html):
    return {re.search(r"\[(VP-\d+)\]", card).group(1): card for card in re.findall(r"<article.*?</article>", html, re.S)}


def test_results_page_card_header_filters_and_zero_claude_calls(listing):
    calls = len(listing["calls"])
    html = listing["client"].get("/qa-agent/results", params={"product": "VXvue", "q": MARK}).text
    cards = cards_by_subject(html)
    assert len(cards) == 6 and all('class="qa-updated"' in card for key, card in cards.items() if key != "VP-9910")
    assert 'class="qa-reporter"' in cards["VP-7287"] and 'class="qa-reporter"' not in cards["VP-9910"]      # 사양 결과에는 작성자가 없다
    assert [key for key, card in cards.items() if 'class="qa-changed"' in card] == ["VP-7300", "VP-7287"] or \
        sorted(key for key, card in cards.items() if 'class="qa-changed"' in card) == ["VP-7287", "VP-7300"]
    assert [key for key, card in cards.items() if "qa-kind-legacy" in card] == ["VP-7500"]
    assert [key for key, card in cards.items() if "이전 분석 1건" in card] == ["VP-7287"]
    assert [key for key, card in cards.items() if "왜 분석했나" in card] == ["VP-7287"]
    assert 'name="q"' in html and "자세한 조건" in html and "작성자별로 묶기" in html and "모든 결과 보기" in html
    assert html.count('class="qa-chip"') + html.count('class="qa-chip on"') == 6
    grouped = listing["client"].get("/qa-agent/results", params={"product": "VXvue", "q": MARK, "group": "author"}).text
    assert 'class="qa-group"' in grouped and "테스트작성자" in grouped and "이름 미확인" in grouped
    assert listing["client"].get("/qa-agent/results?quick=zzz").status_code == 400
    assert len(listing["calls"]) == calls


def test_history_link_matches_exact_subject_only(listing):
    """`VP-728` 의 이전 분석 링크에 `VP-7287` 같은 앞부분이 같은 번호가 섞이지 않는다."""
    seed(listing, "VP-728", kind="NEW_ISSUE", verdict="SPEC_VIOLATION", created="2026-10-06T03:00:00+00:00")
    seed(listing, "VP-728", kind="NEW_ISSUE", verdict="SPEC_VIOLATION", created="2026-10-07T03:00:00+00:00")
    data = result_views.search(listing["choice"], {"q": MARK})
    card = next(card for card in data["cards"] if card["subject"] == "VP-728")
    assert card["history_count"] == 2 and "subject_id=VP-728" in card["history_url"]
    html = listing["client"].get(card["history_url"]).text
    opened = cards_by_subject(html)
    assert list(opened) == ["VP-728", "VP-728"] or sorted(re.findall(r"\[(VP-\d+)\]", html)) == ["VP-728", "VP-728"]
    assert html.count('href="/qa-agent/findings/') == card["history_count"] == 2 and "[VP-7287]" not in html
    exact = result_views.search(listing["choice"], {"subject_id": "VP-728", "view": "all"})
    assert exact["total"] == 2 and {item["subject"] for item in exact["items"]} == {"VP-728"}
    assert result_views.search(listing["choice"], {"subject": "VP-728", "view": "all"})["total"] == 4        # 번호 칸은 일부만 맞아도 찾는다


@pytest.mark.parametrize("path", ["/qa-agent/results", "/qa-agent"])
def test_history_list_keeps_exact_subject_when_the_form_is_submitted(listing, path, monkeypatch):
    """`이전 분석 N건` 으로 연 목록에서 조회·정렬·검색을 눌러도 그 번호만 남는다(폼이 `subject_id` 를 그대로 싣는다)."""
    import html as htmllib
    choice = dash.ProductChoice(listing["cfg"], listing["store"], ["VXvue"])
    monkeypatch.setattr(dash, "choose", lambda product="": choice if product in ("", "VXvue", "vxvue") else None)
    seed(listing, "VP-728", kind="NEW_ISSUE", verdict="SPEC_VIOLATION", created="2026-10-06T03:00:00+00:00")
    seed(listing, "VP-728", kind="NEW_ISSUE", verdict="SPEC_VIOLATION", created="2026-10-07T03:00:00+00:00")
    card = next(card for card in result_views.search(choice, {"q": MARK}, path=path)["cards"] if card["subject"] == "VP-728")
    opened = listing["client"].get(card["history_url"]).text
    form = re.search(r'<form method="get" action="([^"]*)" class="qa-filters".*?</form>', opened, re.S)
    assert form and htmllib.unescape(form.group(1)) == path
    hidden = {htmllib.unescape(name): htmllib.unescape(value) for name, value in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form.group(0))}
    assert hidden.get("subject_id") == "VP-728" and hidden.get("view") == "all"
    for changes in ({}, {"sort": "analyzed"}, {"q": "요약"}):
        submitted = listing["client"].get(path, params={**hidden, **changes}).text
        assert sorted(re.findall(r"\[(VP-\d+)\]", submitted)) == ["VP-728", "VP-728"], changes


def test_period_and_text_filters_apply_before_picking_latest(listing):
    """분석 기간·검색어·종류는 최근 한 건을 고르기 전에 적용한다."""
    old = seed(listing, "VP-7800", created="2026-10-05T03:00:00+00:00", summary="지난주 분석 고유문구")
    new = seed(listing, "VP-7800", created="2026-10-13T03:00:00+00:00")
    in_week = result_views.search(listing["choice"], {"q": MARK, "start": "2026-10-04", "end": "2026-10-06"})
    card = next(card for card in in_week["cards"] if card["subject"] == "VP-7800")
    assert card["id"] == old and card["history_count"] == 2 and in_week["total"] == 1
    assert next(card for card in result_views.search(listing["choice"], {"q": MARK})["cards"] if card["subject"] == "VP-7800")["id"] == new
    only_old = result_views.search(listing["choice"], {"q": "고유문구"})
    assert [card["id"] for card in only_old["cards"]] == [old]
    older_legacy = seed(listing, "VP-7801", kind="", verdict="TC 없음", created="2026-10-02T03:00:00+00:00")
    seed(listing, "VP-7801", kind="NEW_ISSUE", verdict="SPEC_VIOLATION", created="2026-10-12T03:00:00+00:00")
    legacy = result_views.search(listing["choice"], {"q": MARK, "category": "legacy"})
    assert "VP-7801" in subjects(legacy) and next(card for card in legacy["cards"] if card["subject"] == "VP-7801")["id"] == older_legacy


def test_status_filters_apply_to_the_latest_result_only(listing):
    """AI 판정·처리 상태·`검토 필요`·`미처리` 는 고른 최근 결과에 적용한다. 덮인 옛 결과는 되살아나지 않는다."""
    seed(listing, "VP-7810", verdict="SPEC_UNDEFINED", created="2026-10-05T03:00:00+00:00")             # 옛 결과는 검토 필요
    newest = seed(listing, "VP-7810", verdict="CONSISTENT_WITH_SPEC", created="2026-10-13T03:00:00+00:00")  # 최근 결과는 문제 없음
    for filters in ({"quick": "review"}, {"level": "검토 필요"}):
        assert "VP-7810" not in subjects(result_views.search(listing["choice"], {"q": MARK, **filters}))
    ok = result_views.search(listing["choice"], {"q": MARK, "level": "문제 없음"})
    assert next(card for card in ok["cards"] if card["subject"] == "VP-7810")["id"] == newest
    stale_open = seed(listing, "VP-7811", created="2026-10-05T03:00:00+00:00")                           # 옛 결과만 미처리
    done_latest = seed(listing, "VP-7811", created="2026-10-13T03:00:00+00:00")
    listing["store"].storage.qa_set_processing(done_latest, "vxvue", "done", "QA 담당", "확인")
    open_latest = seed(listing, "VP-7812", created="2026-10-13T03:00:00+00:00")                          # 최근 결과가 미처리
    old_done = seed(listing, "VP-7812", created="2026-10-05T03:00:00+00:00")
    listing["store"].storage.qa_set_processing(old_done, "vxvue", "done", "QA 담당", "확인")
    opened = result_views.search(listing["choice"], {"q": MARK, "quick": "open"})
    assert "VP-7811" not in subjects(opened) and stale_open
    assert next(card for card in opened["cards"] if card["subject"] == "VP-7812")["id"] == open_latest
    assert "VP-7811" in subjects(result_views.search(listing["choice"], {"q": MARK, "state": "done"}))


@pytest.mark.parametrize("savings", [False, True])
def test_dashboard_uses_same_cards_and_filters_in_both_modes(listing, monkeypatch, savings):
    choice = dash.ProductChoice(replace(listing["cfg"], savings_enabled=savings), listing["store"], ["VXvue"])
    monkeypatch.setattr(dash, "choose", lambda product="": choice if product in ("", "VXvue", "vxvue") else None)
    html = listing["client"].get("/qa-agent", params={"q": "테스트작성자"}).text
    assert html.count('class="qa-reporter"') == 2 and 'class="qa-updated"' in html and "[VP-7287]" in html and "[VP-7300]" not in html
    assert 'name="q"' in html and "자세한 조건" in html and 'class="qa-chip' in html and 'class="qa-view-switch"' in html
    everything = listing["client"].get("/qa-agent", params={"q": MARK}).text
    assert everything.count("[VP-7287]") == 1 and "이전 분석 1건" in everything          # 이슈마다 최신 결과 한 장
    assert 'class="qa-ok-cards"' not in everything and "[VP-7600]" in everything                   # `문제 없음` 카드도 접지 않는다
    assert listing["client"].get("/qa-agent?quick=zzz").status_code == 400
    assert listing["client"].get("/qa-agent?view=zzz").status_code == 400


def serve(client):
    static_root = Path(__file__).resolve().parents[1] / "app/web"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlparse(self.path).path
            if path.startswith("/static/"):
                file = static_root / path.lstrip("/")
                body = file.read_bytes() if file.is_file() else b""
                kind = "text/javascript" if file.suffix == ".js" else "text/css"
            else:
                response = client.get(self.path)
                body, kind = response.content, response.headers.get("content-type", "text/html")
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_result_list_has_no_horizontal_overflow_and_reads_without_script(listing, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    choice = dash.ProductChoice(replace(listing["cfg"], savings_enabled=True), listing["store"], ["VXvue"])
    monkeypatch.setattr(dash, "choose", lambda product="": choice if product in ("", "VXvue", "vxvue") else None)
    server, thread = serve(listing["client"])
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with playwright.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for width in (1366, 1920):
                    for script in (True, False):
                        context = browser.new_context(viewport={"width": width, "height": 1080}, java_script_enabled=script)
                        page = context.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        for path in (f"/qa-agent/results?product=VXvue&q={MARK}", f"/qa-agent/results?product=VXvue&q={MARK}&group=author",
                                     f"/qa-agent?product=VXvue&q={MARK}"):
                            page.goto(base + path, wait_until="domcontentloaded")
                            assert page.locator(".qa-card").count() >= 5
                            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (width, script, path)
                            first = page.locator(".qa-card").first
                            assert first.locator(".qa-reporter").is_visible() and first.locator(".qa-updated").is_visible()
                            assert first.locator("h3, h4").first.is_visible()
                            assert page.locator(".qa-changed").count() == 2 and page.locator(".qa-changed").first.is_visible()      # VP-7287, VP-7300
                            assert page.locator(".qa-card", has_text="[VP-7400]").locator(".qa-changed").count() == 0
                            assert page.locator(".qa-card", has_text="[VP-7500]").locator(".qa-kind-legacy").is_visible()
                            assert page.locator(".qa-kind-legacy").count() == 1
                            clamp = page.locator(".qa-card", has_text="[VP-7400]").locator(".qa-summary").evaluate(
                                "el => { const s = getComputedStyle(el); return {line: parseFloat(s.lineHeight), clamp: s.webkitLineClamp, shown: el.clientHeight, full: el.scrollHeight}; }")
                            # 두 줄 제한: 보이는 높이가 두 줄(글자 모양에 따른 여유 반 줄 포함) 이하이고 실제 글은 그보다 길다(제한 CSS 를 빼면 실패한다)
                            assert clamp["clamp"] == "2" and clamp["shown"] <= 2.5 * clamp["line"] and clamp["full"] > clamp["shown"], clamp
                        assert not errors
                        context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

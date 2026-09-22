from fastapi.testclient import TestClient

from app.main import app
from app.web import router


def test_health():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_hub_links_to_qa_modules():
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "<nav>" not in response.text
    assert 'href="/impact-analyzer"' in response.text
    assert 'href="/manual-review"' in response.text


def test_hub_shows_configured_external_services():
    """config.yaml `services.*`에 URL이 있는 하위 서비스는 허브 카드로 나온다."""
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert 'href="/manual-hub/"' in response.text
    assert "매뉴얼 서버" in response.text


def test_hub_hides_external_service_without_url(monkeypatch):
    """URL이 비어 있으면 카드를 만들지 않는다 — 하위 서비스를 배포하지 않은 환경에서
    깨진 링크가 노출되지 않아야 한다."""
    monkeypatch.setitem(
        router.get_settings().raw,
        "services",
        {"manual_hub": {"name": "매뉴얼 서버", "url": ""}},
    )
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert 'href="/manual-hub/"' not in response.text
    assert "매뉴얼 서버" not in response.text


def test_external_services_skips_blank_url(monkeypatch):
    """URL이 공백이거나 없는 항목은 카드 목록에서 제외된다."""
    settings = router.get_settings()
    monkeypatch.setitem(
        settings.raw,
        "services",
        {
            "with_url": {"name": "있음", "url": " https://example.internal "},
            "blank_url": {"name": "없음", "url": "   "},
            "no_url": {"name": "누락"},
            "not_a_dict": "무시",
        },
    )
    cards = router.external_services()
    assert [card["key"] for card in cards] == ["with_url"]
    assert cards[0]["url"] == "https://example.internal"


def test_relative_service_url_redirects_to_nginx_origin_when_hit_on_app_port():
    """앱 포트로 직접 들어온 /manual-hub 요청은 포트 없는 같은 호스트로 돌려보낸다.

    nginx를 거치면 이 경로는 앱까지 오지 않는다. 앱 포트(:24357 등)로 직접 접속한
    사용자의 홈 카드가 404로 끝나지 않게 하기 위한 폴백이다."""
    client = TestClient(app, base_url="http://10.0.0.5:24357")
    response = client.get("/manual-hub/documents?q=x", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "http://10.0.0.5/manual-hub/documents?q=x"


def test_relative_service_url_does_not_loop_on_default_port():
    """기본 포트로 들어왔는데 이 앱까지 왔다면 nginx 설정이 빠진 것이다.

    같은 주소로 리다이렉트하면 무한 루프가 되므로 404로 원인을 알린다."""
    client = TestClient(app, base_url="http://10.0.0.5")
    response = client.get("/manual-hub/", follow_redirects=False)
    assert response.status_code == 404
    assert "nginx" in response.json()["detail"]


def test_impact_analyzer_has_dedicated_entry_route():
    response = TestClient(app).get("/impact-analyzer")
    assert response.status_code == 200
    assert "<nav>" in response.text
    assert 'href="/"' in response.text
    assert 'href="/impact-analyzer/guide"' in response.text
    assert 'href="/manual-review"' not in response.text
    assert "Regression 분석" in response.text


def test_impact_and_manual_guides_are_separate():
    client = TestClient(app)
    impact = client.get("/impact-analyzer/guide")
    manual = client.get("/manual-review/guide")

    assert impact.status_code == 200
    assert "Regression 영향 분석 사용법" in impact.text
    assert "매뉴얼 개정 검증 사용법" not in impact.text
    assert manual.status_code == 200
    assert "매뉴얼 개정 검증 사용법" in manual.text
    assert "Regression 영향 분석 사용법" not in manual.text


def test_legacy_guide_redirects_to_impact_guide():
    response = TestClient(app).get("/guide", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/impact-analyzer/guide"


def test_knowledge_is_presented_as_shared_workspace():
    response = TestClient(app).get("/knowledge")
    assert response.status_code == 200
    assert "공용 Knowledge" in response.text
    assert 'href="/impact-analyzer"' in response.text
    assert 'href="/manual-review"' in response.text

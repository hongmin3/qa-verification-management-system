"""Validates: REQ-QAINTEL-038 / TEST-QAINTEL-023. 실제 API와 화면, 외부 AI·메일 없음."""
import pytest

from tests.test_qa_intel_dashboard import world  # noqa: F401


def test_policy_defaults_and_shared_web_save(world):
    client = world["client"]
    response = client.get("/qa-agent/automation-policy?product=VXvue")
    assert response.status_code == 200
    assert response.json()["mode"] == "daily"
    # 사내 프록시를 거친 요청도 새 점검 설정을 저장할 수 있다.
    saved = client.post("/qa-agent/automation-policy", data={"product": "VXvue", "enabled": "true"},
                        headers={"X-Forwarded-For": "10.1.1.20"}, follow_redirects=False)
    assert saved.status_code == 303
    assert saved.headers["location"].startswith("/qa-agent?product=")
    assert client.get("/qa-agent/automation-policy?product=vxvue").json()["mode"] == "weekly"
    page = client.get(saved.headers["location"]).text
    assert 'id="qa-auto-enabled"' in page and 'id="qa-validation-period"' in page
    assert "차수 대기" in page and "변경 이력" in page


@pytest.mark.parametrize("data, mode", [
    ({"enabled": "true", "validation_period": "true"}, "daily"),
    ({"enabled": "true"}, "weekly"),
    ({}, "off"),
])
def test_policy_checkbox_modes(world, data, mode):
    client = world["client"]
    response = client.post("/qa-agent/automation-policy", data={"product": "VXvue", **data}, follow_redirects=False)
    assert response.status_code == 303
    policy = client.get("/qa-agent/automation-policy?product=VXvue").json()
    assert policy["mode"] == mode
    assert policy["enabled"] == bool(data.get("enabled"))
    assert policy["validation_period"] == bool(data.get("validation_period"))
    page = client.get("/qa-agent?product=VXvue").text
    notice = {"daily": "검증 진행 중 · 매 업무일 분석 후 메일 발송",
              "weekly": "다음 차수 대기 · 매주 첫 업무일 분석 후 메일 발송",
              "off": "자동 분석과 메일 발송이 꺼져 있습니다."}[mode]
    assert notice in page


def test_policy_bad_product_and_value_do_not_change_state(world):
    client = world["client"]
    assert client.post("/qa-agent/automation-policy", data={"product": "missing", "enabled": "true"}).status_code == 404
    assert client.post("/qa-agent/automation-policy", data={"product": "VXvue", "enabled": "invalid"}).status_code == 422
    assert client.get("/qa-agent/automation-policy?product=VXvue").json()["mode"] == "daily"

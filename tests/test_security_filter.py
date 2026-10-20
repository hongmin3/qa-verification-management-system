"""외부 전송 직전 마스킹 계층 테스트.

두 가지를 동시에 지켜야 한다.

- 개인정보·사내 경로는 자리표로 바뀐다.
- **QA 판단에 필요한 식별자는 바뀌지 않는다.** 이쪽이 더 위험한 실패다 — 버전이나
  ErrorCode 가 마스킹되면 분석 자체가 불가능해진다.
"""

from __future__ import annotations

import pytest

from app.core.security_filter import mask_payload, mask_text


def _mask(text: str) -> str:
    return mask_text(text)[0]


# --- 마스킹돼야 하는 것 -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "placeholder"),
    [
        ("문의: user@example.com 로 회신", "[EMAIL]"),
        ("장비 IP 192.168.10.25 에 접속", "[IP_ADDRESS]"),
        ("Patient ID: P20260908-77 조회", "[PATIENT_ID]"),
        ("환자번호 1234567 로 검색", "[PATIENT_ID]"),
        ("Patient Name: 홍길동", "[PATIENT_NAME]"),
        ("환자 이름 홍길동 으로 등록", "[PATIENT_NAME]"),
        ("Hospital: 서울대학교병원", "[HOSPITAL]"),
        ("병원명: 강남세브란스", "[HOSPITAL]"),
        ("고객명: 뷰웍스대리점", "[CUSTOMER]"),
        ("S/N: FXRD3643-000123", "[SERIAL]"),
        ("password: Abcd1234!", "[ACCOUNT]"),
        ("api_key = AIzaSyDummyKeyValue123456", "[SECRET]"),
        (r"로그 경로 \\10.13.0.5\qa\VXvue\log.zip", "[NETWORK_PATH]"),
        (r"C:\Users\2024980\Documents\VXvue\log.zip 확인", "[LOCAL_PATH]"),
    ],
)
def test_sensitive_values_are_replaced(text: str, placeholder: str) -> None:
    masked = _mask(text)
    assert placeholder in masked


def test_original_value_is_gone_not_just_annotated() -> None:
    masked = _mask("문의: user@example.com")
    assert "user@example.com" not in masked


def test_sentence_structure_is_kept() -> None:
    """통째로 지우면 문장이 깨져 판정이 틀어진다 — 자리표로 바꾼다."""
    masked = _mask("Patient ID: P123 로 조회하면 목록이 표시된다")
    assert masked.startswith("Patient ID: [PATIENT_ID]")
    assert "로 조회하면 목록이 표시된다" in masked


# --- 마스킹되면 안 되는 것 (더 위험한 실패) -----------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "VP-1234 재현 확인",
        "SRS-1234 로그인 사양",
        "Command 0x30402 요청",
        "DICOM Attribute (0008,0018) 확인",
        "VXvue 1.1.0.001 에서 발생",
        "매뉴얼 V1.0.11 기준",
        "errorCode 4097 반환",
        "FXRD-3643VAW 모델",
    ],
)
def test_qa_identifiers_survive(text: str) -> None:
    assert _mask(text) == text


def test_version_string_is_not_mistaken_for_ip() -> None:
    """`1.1.0.001` 은 IPv4 처럼 보이지만 버전이다. 옥텟 범위 제한으로 구분한다."""
    assert _mask("VXvue 1.1.0.001 에서 발생") == "VXvue 1.1.0.001 에서 발생"


def test_real_ip_next_to_a_version_is_still_masked() -> None:
    masked = _mask("VXvue 1.1.0.001 장비 10.13.0.222 접속")
    assert "1.1.0.001" in masked
    assert "10.13.0.222" not in masked


def test_dicom_tag_is_not_masked_even_next_to_patient_label() -> None:
    masked = _mask("Patient ID (0010,0020) 태그를 확인한다")
    assert "(0010,0020)" in masked


# --- 보고 ---------------------------------------------------------------------


def test_report_counts_by_rule() -> None:
    _, report = mask_text("a@b.com 과 c@d.com, IP 10.0.0.1")
    assert report.counts["email"] == 2
    assert report.counts["ip"] == 1
    assert report.total == 3
    assert report.applied is True


def test_report_does_not_contain_the_original_values() -> None:
    """로그에 원본이 남으면 마스킹의 의미가 없다."""
    _, report = mask_text("Patient ID: P20260908-77")
    assert "P20260908-77" not in str(report.as_dict())


def test_clean_text_reports_nothing_applied() -> None:
    text = "VP-1234 의 Root Cause 를 확인한다"
    masked, report = mask_text(text)
    assert masked == text
    assert report.applied is False


def test_empty_text_is_safe() -> None:
    masked, report = mask_text("")
    assert masked == ""
    assert report.total == 0


# --- 구조 전체 ----------------------------------------------------------------


def test_payload_is_masked_recursively() -> None:
    payload = {
        "issue": {"title": "user@example.com 로 회신 요청", "steps": ["Patient ID: P1 조회", "VP-1234 재현"]},
        "count": 3,
        "flags": [True, None],
    }
    masked, report = mask_payload(payload)
    assert "[EMAIL]" in masked["issue"]["title"]
    assert "[PATIENT_ID]" in masked["issue"]["steps"][0]
    assert masked["issue"]["steps"][1] == "VP-1234 재현"
    assert masked["count"] == 3
    assert masked["flags"] == [True, None]
    assert report.counts == {"email": 1, "patient_id": 1}


def test_payload_keys_are_not_masked() -> None:
    masked, _ = mask_payload({"patient_id": "P1"})
    assert "patient_id" in masked


# --- 버전과 IP 구분 (NFR-PRIV-001) -----------------------------------------------
# 운영 Desktop 응답에서 `1.0.0.3`·`VSS.Vieworks 1.0.2.3` 같은 제품 버전이 `[IP_ADDRESS]` 로 바뀌어
# 버전별 이력을 읽을 수 없었다(2026-10-19 get_status·get_generator_evidence 확인).


@pytest.mark.parametrize(
    "text",
    [
        "V1.0.0.3 대신 1.0.0.3 으로 적은 열",
        "VSS.Vieworks 1.0.2.3\nVXvue 1.1.0.1",
        "ECORAY 결과 VSS 1.0.12.3",
        "1.0.10.14",
        "제너레이터 F/W 2.38.1.4",
    ],
)
def test_bare_four_part_versions_survive(text: str) -> None:
    assert _mask(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("서버 10.13.0.222 접속", "서버 [IP_ADDRESS] 접속"),
        ("장비 172.20.1.5 와 연결", "장비 [IP_ADDRESS] 와 연결"),
        ("Console 192.168.0.10", "Console [IP_ADDRESS]"),
        ("loopback 127.0.0.1", "loopback [IP_ADDRESS]"),
        ("주소 http://8.8.4.4/x", "주소 http://[IP_ADDRESS]/x"),
        ("host=1.2.3.4", "host=[IP_ADDRESS]"),
        ("DNS 서버 IP: 8.8.8.8", "DNS 서버 IP: [IP_ADDRESS]"),
        ("1.2.3.4:8080 로 연결", "[IP_ADDRESS]:8080 로 연결"),
        ("대역 3.4.5.0/24", "대역 [IP_ADDRESS]/24"),
    ],
)
def test_network_addresses_are_still_masked(text: str, expected: str) -> None:
    assert _mask(text) == expected


# --- 서버 경로 -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Ambiguous latest revision: /home/ubuntu/ai-regression-impact-analyzer/data/x.xlsx",
        "읽기 실패 /opt/qa-manual-hub/logs/backup.log",
        "/srv/qa/original/(TC) 목록.xlsx",
    ],
)
def test_server_paths_are_masked(text: str) -> None:
    masked = _mask(text)
    assert "[SERVER_PATH]" in masked and "/home/" not in masked and "/opt/" not in masked and "/srv/" not in masked


@pytest.mark.parametrize("path,tokens", [
    ("/home/ubuntu/qa/VP-1234.xlsx", ["VP-1234"]),
    ("/opt/qa/V1.0.11/report.txt", ["V1.0.11"]),
    ("/srv/qa/0x30402/report.txt", ["0x30402"]),
    ("/home/ubuntu/VP-1234/V1.0.11/report.txt", ["VP-1234", "V1.0.11"]),
    ("/home/ubuntu/qa/(0010,0010)/report.txt", ["(0010,0010)"]),
    ("/srv/qa/( 0010 , 0010 )/VP-1234/report.txt", ["( 0010 , 0010 )", "VP-1234"]),
])
def test_server_path_with_qa_identifiers_hides_layout_and_keeps_tokens(path, tokens):
    """Validates: NFR-PRIV-001. 경로 보호와 QA 식별자 보존을 함께 확인한다."""
    masked, report = mask_text("읽기 실패 " + path)
    assert "[SERVER_PATH]" in masked
    assert not any(prefix in masked for prefix in ("/home/", "/opt/", "/srv/"))
    assert all(token in masked for token in tokens)
    assert "report.txt" not in masked
    assert report.counts["server_path"] == 1


@pytest.mark.parametrize("text", ["http://10.13.0.222:24357/qa-agent/changes", "/qa-agent/runs/1 화면", "docs/OPERATIONS.md"])
def test_web_paths_are_not_server_paths(text: str) -> None:
    assert "[SERVER_PATH]" not in _mask(text)

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.knowledge.srs_crawler.util import (
    build_pdf_filename,
    is_valid_srs_id,
    module_key_from_old_id,
    safe_filename,
    today_yymmdd,
)


def test_is_valid_srs_id():
    assert is_valid_srs_id("VP-411")
    assert is_valid_srs_id("VP-40")  # 2자리도 유효해야 함 (License Manager)
    assert is_valid_srs_id("VP-6822")
    assert not is_valid_srs_id("411")
    assert not is_valid_srs_id("")
    assert not is_valid_srs_id("VP411")


def test_module_key_from_old_id_vxvue_style():
    assert module_key_from_old_id("01") == "01"
    assert module_key_from_old_id("01-10") == "01"
    assert module_key_from_old_id("01-10-10") == "01"
    assert module_key_from_old_id("11-90-30") == "11"


def test_module_key_from_old_id_license_manager_style():
    assert module_key_from_old_id("SRS 01-10-10") == "01"
    assert module_key_from_old_id("SRS 06-10-30") == "06"


def test_module_key_from_old_id_none():
    assert module_key_from_old_id(None) is None
    assert module_key_from_old_id("") is None


def test_safe_filename_strips_forbidden_chars():
    result = safe_filename('(사양서) VXvue 사양서1<>:"/\\|?*(260824).pdf')
    for ch in '<>:"/\\|?*':
        assert ch not in result


def test_build_pdf_filename_format():
    name = build_pdf_filename("(사양서) ", "VXvue 사양서1", "260824")
    assert name == "(사양서) VXvue 사양서1(260824).pdf"


def test_today_yymmdd_format():
    dt = datetime(2026, 8, 24)
    assert today_yymmdd(dt) == "260824"

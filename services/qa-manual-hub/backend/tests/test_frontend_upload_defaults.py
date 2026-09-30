"""Upload dialog defaults, checked on the frontend source (no browser, no DB).

The frontend has no test runner of its own, so this reads the component source
the same way ``test_deploy_scripts.py`` reads the shell scripts.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
DETAIL = FRONTEND_SRC / "pages" / "DocumentDetail.tsx"


@pytest.fixture(autouse=True)
def clean_tables():
    """No database here: replace the conftest fixture of the same name."""
    yield


# Validates: REQ-HUBDATA-013
def test_revision_date_is_blank_unless_the_file_name_has_a_date():
    """SPEC 13.6 #3 (b): Revision Date is the date written in the document, so the
    upload dialog must not silently fill in today's date."""
    text = DETAIL.read_text(encoding="utf-8")
    assert re.search(r"const \[revisionDate, setRevisionDate\] = useState\(''\)", text)
    assert "setRevisionDate(guess.revisionDate ?? '')" in text
    assert "useState(todayIso())" not in text
    assert "?? todayIso()" not in text

"""REQ-MANUAL-024: separate main review items from reference items and filter them."""
import json
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app.modules.manual_review.shared_review import build_report
from app.modules.manual_review.shared_tools import definitions
from test_manual_review_shared import finding, store_source


def report(tmp_path, rows):
    store, source = store_source(tmp_path)
    result = build_report(store, source['source_id'], rows)
    folder = Path(result['directory'])
    return folder, json.loads((folder / 'report.json').read_text('utf-8'))


def test_reference_items_are_marked_and_filterable_in_html_and_excel(tmp_path):
    folder, payload = report(tmp_path, [finding('R01', [2], review_scope='main'), finding('R02', [2], review_scope='reference')])
    assert [r['review_scope'] for r in payload['findings']] == ['main', 'reference']
    page = (folder / 'manual_review.html').read_text('utf-8')
    assert '<article id="R01" data-scope="main"' in page and '<article id="R02" data-scope="reference"' in page
    assert 'data-filter="main">본 검토 1건' in page and 'data-filter="reference">참고 1건' in page
    assert 'data-filter="all">전체 2건' in page
    ws = load_workbook(folder / 'manual_review.xlsx')['검토결과']
    headers = [c.value for c in ws[6]]
    column = headers.index('검토 구분') + 1
    assert [ws.cell(7, column).value, ws.cell(8, column).value] == ['본 검토', '참고']
    assert headers[:18] == headers[:18] and len(headers) == 19  # 기존 열 뒤에 추가


def test_legacy_call_without_scope_is_main_and_shows_no_filter(tmp_path):
    folder, payload = report(tmp_path, [finding('R01', [2])])
    assert payload['findings'][0]['review_scope'] == 'main'
    page = (folder / 'manual_review.html').read_text('utf-8')
    assert 'data-filter="reference"' not in page


def test_invalid_scope_fails_before_files(tmp_path):
    store, source = store_source(tmp_path)
    with pytest.raises(ValueError, match='review_scope'):
        build_report(store, source['source_id'], [finding('R01', [2], review_scope='minor')])
    assert not (store.base / 'reports').exists()


def test_mcp_schema_exposes_review_scope():
    definition = next(x for x in definitions() if x['name'] == 'make_manual_review_report')
    props = definition['inputSchema']['properties']['findings']['items']['properties']
    assert props['review_scope']['enum'] == ['main', 'reference']
    assert 'review_scope' in definition['description']

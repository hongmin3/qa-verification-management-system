"""Validates: REQ-QAINTEL-045. Workbook selection never crosses versions."""
from pathlib import Path
import pytest
from openpyxl import Workbook
from tests.qa_intel_harness import Harness
from app.modules.daily_qa import workflow_settings as workflow


def workbook(path, sheets):
    book = Workbook()
    book.remove(book.active)
    for name in sheets:
        sheet = book.create_sheet(name)
        sheet.append(['SRS ID', 'Title', 'Step', 'Expected'])
        sheet.append(['VP-10', name, '1. 확인', '1. 사양대로'])
    book.save(path)
    return path


def test_default_and_saved_version_are_real_sheets(tmp_path):
    h = Harness(tmp_path)
    path = workbook(tmp_path / 'Checklist.xlsx', ['V1.1.0', 'V1.2.0'])
    options, errors = workflow.versions([path])
    assert not errors
    selected = workflow.selected(h.store, h.cfg.slug, options)
    assert selected['sheet'] == 'V1.1.0'
    workflow.save(h.store, h.cfg.slug, options[1]['key'], options)
    assert workflow.selected(h.store, h.cfg.slug, options)['sheet'] == 'V1.2.0'
    with pytest.raises(ValueError):
        workflow.save(h.store, h.cfg.slug, 'invented', options)
    rows, errors = workflow.selected_rows([path], options[0])
    assert not errors and {row.sheet for row in rows} == {'V1.1.0'}


def test_missing_or_ambiguous_version_does_not_fallback(tmp_path):
    h = Harness(tmp_path)
    paths = [workbook(tmp_path / name, ['V1.1.0']) for name in ['A.xlsx', 'B.xlsx']]
    options, _ = workflow.versions(paths)
    assert workflow.selected(h.store, h.cfg.slug, options) is None
    workflow.save(h.store, h.cfg.slug, options[0]['key'], options)
    remaining, _ = workflow.versions(paths[1:])
    assert workflow.selected(h.store, h.cfg.slug, remaining) is None

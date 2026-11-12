"""PC 보조 스크립트(scripts/vxvue_issue_tools) 순수 로직 시험. 실제 영상·Redmine·공유폴더는 쓰지 않는다."""
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / 'scripts' / 'vxvue_issue_tools'
sys.path.insert(0, str(TOOLS))

import _common  # noqa: E402
import collect_evidence  # noqa: E402
import env_info  # noqa: E402
import register_redmine_issue as reg  # noqa: E402


def test_picpick_name_is_recording_start():
    assert _common.video_start_from_name(Path('2026-11-12 13 55 44.mp4')) == datetime(2026, 11, 12, 13, 55, 44)
    assert _common.video_start_from_name(Path('clip.mp4')) is None


def test_log_times_follow_content_not_mtime(tmp_path):
    tt = tmp_path / 'teraterm.log'
    tt.write_text('[26/11/12 13:34:33.961] : a\n[14:20:35.160] : b\n', encoding='ascii')
    times = [t for t, _ in _common.line_times(tt, 'teraterm')]
    assert times == [datetime(2026, 11, 12, 13, 34, 33, 961000), datetime(2026, 11, 12, 14, 20, 35, 160000)]
    viewer = tmp_path / 'v.log'
    viewer.write_bytes('﻿[2026/11/12 09:10:00.808] [Info] x\r\n'.encode('utf-16-le'))
    assert _common.line_times(viewer, 'viewer')[0][0] == datetime(2026, 11, 12, 9, 10, 0, 808000)


def test_find_database_prefers_folder_that_has_the_sub_path(tmp_path, monkeypatch):
    c, d = tmp_path / 'C' / 'Database', tmp_path / 'D' / 'Database'
    (d / 'log' / 'VSS.Vieworks').mkdir(parents=True); c.mkdir(parents=True)
    monkeypatch.setattr(_common, 'DATABASE_CANDIDATES', [c, d])
    assert _common.find_database('log/VSS.Vieworks') == d
    assert _common.find_database('Integration/VSS.Vieworks') == c


def vss(path, start, lines):
    path.write_text(''.join(f'{start} {line}\n' for line in lines), encoding='ascii')


def test_collect_evidence_picks_sessions_by_content_and_labels_restart(tmp_path):
    db = tmp_path / 'Database'; vdir = db / 'log' / 'VSS.Vieworks'; vdir.mkdir(parents=True)
    (db / 'log' / 'Viewer').mkdir()
    (db / 'log' / 'Viewer' / '2026_11_12.log').write_bytes('[2026/11/12 13:56:00.000] [Info] x\r\n'.encode('utf-16'))
    vss(vdir / 'Vieworks_2026-11-12-13-55-03.log', '2026/11/12 13:55:03,404', ['[Info] a'])
    vss(vdir / 'Vieworks_2026-11-12-13-56-56.log', '2026/11/12 13:56:56,000', ['[Info] b'])
    vss(vdir / 'Vieworks_2026-11-12-09-00-00.log', '2026/11/12 09:00:00,000', ['[Info] old'])
    video = tmp_path / '2026-11-12 13 55 44.mp4'; video.write_bytes(b'0' * 10)
    out = tmp_path / 'out'
    code = collect_evidence.main(['--video', str(video), '--duration', '104', '--margin', '3', '--out', str(out),
                                  '--restart', '--viewer-dir', str(db / 'log' / 'Viewer'), '--vss-dir', str(vdir)])
    assert code == 0
    data = json.loads((out / 'evidence.json').read_text(encoding='utf-8'))
    names = [f['target_name'] for f in data['files']]
    assert names == ['01_현상요약.mp4', '02_Viewer_2026_11_12.log', '03_VSS_재실행전_13-55-03.log', '04_VSS_재실행후_13-56-56.log']
    assert all(f['match'] for f in data['files'])
    assert [s['file'] for s in data['skipped']] == ['Vieworks_2026-11-12-09-00-00.log']


def test_env_info_reads_selected_model_from_connector_line(tmp_path):
    log = tmp_path / 'Vieworks_2026-11-12-13-55-03.log'
    log.write_text('2026/11/12 13:55:03,404 [Info] [APP] Program Name: VSS.Vieworks, Version: 1.6.0.100\n'
                   '2026/11/12 13:55:03,610 [Debug] [ConnectionManager] Generator device connector created. [ID: 31] '
                   '[Name: INNOGEN-S40] (ConnectionType: Serial, Port: [COM4], BaudRate: [115200], DataBits: [8])\n', encoding='ascii')
    info = env_info.session_info(log)
    assert info['selected']['name'] == 'INNOGEN-S40' and info['selected']['port'] == 'COM4' and info['vss_version'] == '1.6.0.100'
    xml = tmp_path / 'VSS.Vieworks.xml'
    xml.write_text('<Root><DeviceList><Device ID="31" Name="INNOGEN-S40"/><Device ID="32" Name="INNOGEN-S52"/></DeviceList>'
                   '<Generator><SelectedDevice>31</SelectedDevice></Generator></Root>', encoding='utf-8')
    devices = env_info.xml_devices(xml)
    assert [d['id'] for d in devices['devices']] == ['31', '32']
    assert devices['selected_candidates'] == [{'element': 'SelectedDevice', 'key': 'SelectedDevice', 'value': '31'}]


SHARE = '\\\\10.1.1.100\\전사 공유폴더\\VXvue\\2.Test_Case\\V1.1.0\\Defect\\#'


def test_fill_number_and_share_folder():
    body = '[Log, Data]\r\n' + SHARE
    assert reg.fill_number(body, 25575).endswith('Defect\\#25575')
    assert str(reg.share_folder(body, 25575)).endswith('V1.1.0\\Defect\\#25575')
    with pytest.raises(ValueError):
        reg.fill_number('[Log, Data]\r\nno path', 1)


def draft_file(tmp_path, **changes):
    body = '\r\n'.join(['[Issue Title]', 'T', '[Precondition]', '1. 환경', '[Step]', '1. 실행', '[Actual Result]', '1. 표시됨',
                        '[Expected Result]', '1. 유지되어야 할 것 같음. 사양 확인 필요', '[Log, Data]', SHARE])
    att = tmp_path / '01_현상요약.mp4'; att.write_bytes(b'v')
    draft = {'subject': '(DK INNOGEN) [에뮬레이터연동] 현상', 'description': body, 'project_id': 61, 'tracker_id': 7,
             'priority_id': 2, 'custom_fields': [{'id': 54, 'value': '2767'}], 'attachments': [{'path': str(att)}]}
    draft.update(changes)
    path = tmp_path / 'draft.json'
    path.write_text(json.dumps(draft, ensure_ascii=False), encoding='utf-8')
    return path


def test_register_dry_run_defaults_grade_c_and_never_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(reg.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('dry-run must not use network'))
    assert reg.main([str(draft_file(tmp_path)), '--out', str(tmp_path / 'o')]) == 0
    payload = json.loads((tmp_path / 'o' / 'payload.json').read_text(encoding='utf-8'))
    assert {'id': 4, 'value': 'C'} in payload['issue']['custom_fields']
    result = json.loads((tmp_path / 'o' / 'result.json').read_text(encoding='utf-8'))
    assert result['writes'] == 0 and result['draft_sha256']


def test_register_keeps_user_grade_and_refuses_wrong_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(reg.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('no network before approval check'))
    path = draft_file(tmp_path, custom_fields=[{'id': 54, 'value': '2767'}, {'id': 4, 'value': 'B'}])
    assert reg.main([str(path), '--out', str(tmp_path / 'o'), '--execute', '--approved-sha', 'deadbeef']) == 3
    payload = json.loads((tmp_path / 'o' / 'payload.json').read_text(encoding='utf-8'))
    assert {'id': 4, 'value': 'B'} in payload['issue']['custom_fields']
    assert {'id': 4, 'value': 'C'} not in payload['issue']['custom_fields']


def test_register_without_key_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr(reg.urllib.request, 'urlopen', lambda *a, **k: pytest.fail('no network without key'))
    monkeypatch.setattr(reg, 'api_key', lambda: '')
    path = draft_file(tmp_path)
    reg.main([str(path), '--out', str(tmp_path / 'd')])
    sha = json.loads((tmp_path / 'd' / 'result.json').read_text(encoding='utf-8'))['draft_sha256']
    assert reg.main([str(path), '--out', str(tmp_path / 'o'), '--execute', '--approved-sha', sha]) == 4

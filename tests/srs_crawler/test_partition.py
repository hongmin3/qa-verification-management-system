# Validates: REQ-SYNC-003
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.knowledge.srs_crawler.config import Config, ProjectSpec, SingleFileProject, VXvueGroup
from app.modules.knowledge.srs_crawler.partition import assign_file_groups


def _make_config() -> Config:
    return Config(
        host="https://example.com",
        token="dummy",
        verify_ssl=True,
        timeout_seconds=10,
        request_interval_seconds=0,
        page_size=100,
        projects=[ProjectSpec(id="VXvue", query="type:srs"), ProjectSpec(id="LicenseManager", query="type:srs")],
        content_field_priority=["descriptionKR", "description"],
        vxvue_project_id="VXvue",
        vxvue_groups=[
            VXvueGroup(file_key="spec1", display_name="VXvue 사양서1", modules=["01", "02"]),
            VXvueGroup(file_key="spec2", display_name="VXvue 사양서2", modules=["04"]),
        ],
        single_file_projects=[
            SingleFileProject(project_id="LicenseManager", file_key="license_manager", display_name="Licence Manager SRS 사양서")
        ],
        base_dir=Path("."),
        archive_dir=Path("."),
        snapshots_dir=Path("."),
        logs_dir=Path("."),
        knowledge_folder=None,
        filename_prefix="(사양서) ",
        filename_date_format="%y%m%d",
        min_expected_srs_ratio=0.95,
        require_all_pdfs=True,
    )


def _rec(project_id, id_, old_id):
    return {"project_id": project_id, "id": id_, "uid": f"{project_id}/{id_}", "old_id": old_id, "module_key": old_id.split("-")[0].zfill(2) if old_id else None}


def test_same_srs_never_split_across_groups():
    config = _make_config()
    records = [
        _rec("VXvue", "VP-411", "01"),
        _rec("VXvue", "VP-413", "01-10-10"),
        _rec("VXvue", "VP-540", "04-10-10"),
        _rec("LicenseManager", "VP-40", "SRS 01-10-10"),
    ]
    groups, warnings = assign_file_groups(records, config)
    assigned = {r["uid"]: g.file_key for g in groups for r in g.records}

    assert assigned["VXvue/VP-411"] == "spec1"
    assert assigned["VXvue/VP-413"] == "spec1"
    assert assigned["VXvue/VP-540"] == "spec2"
    assert assigned["LicenseManager/VP-40"] == "license_manager"
    # 각 SRS는 정확히 한 그룹에만 존재
    all_uids = [r["uid"] for g in groups for r in g.records]
    assert len(all_uids) == len(set(all_uids)) == 4


def test_unknown_module_falls_back_with_warning():
    config = _make_config()
    records = [_rec("VXvue", "VP-999", "09-10-10")]  # 모듈 09는 매핑에 없음
    groups, warnings = assign_file_groups(records, config)
    assigned = {r["uid"]: g.file_key for g in groups for r in g.records}
    assert assigned["VXvue/VP-999"] == "spec2"  # 마지막 그룹으로 fallback
    assert len(warnings) == 1
    assert "VP-999" in warnings[0]


def test_stable_across_reruns_with_new_items_inserted():
    """새 SRS가 트리 중간(oldId 기준)에 추가돼도 기존 항목의 그룹은 바뀌지 않아야 한다."""
    config = _make_config()
    run1 = [_rec("VXvue", "VP-413", "01-10-10"), _rec("VXvue", "VP-540", "04-10-10")]
    groups1, _ = assign_file_groups(run1, config)
    assigned1 = {r["uid"]: g.file_key for g in groups1 for r in g.records}

    run2 = run1 + [_rec("VXvue", "VP-9999", "01-20-05")]  # 새로 추가된 항목
    groups2, _ = assign_file_groups(run2, config)
    assigned2 = {r["uid"]: g.file_key for g in groups2 for r in g.records}

    assert assigned1["VXvue/VP-413"] == assigned2["VXvue/VP-413"]
    assert assigned1["VXvue/VP-540"] == assigned2["VXvue/VP-540"]
    assert assigned2["VXvue/VP-9999"] == "spec1"

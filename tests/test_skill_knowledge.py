"""Validates: REQ-VXVUECHATBOT-008. No production plugin/knowledge writes."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.core import skill_knowledge


def package(home, version, text="skill", code="print('parser')"):
    root = home / "plugins" / "cache" / "local" / "sample" / version
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "sample", "version": version}), encoding="utf-8")
    skill = root / "skills" / "qa"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text(text, encoding="utf-8")
    (skill / "references" / "qa.md").write_text("full reference", encoding="utf-8")
    (skill / "scripts" / "search.py").write_text(code, encoding="utf-8")
    (root / "config").mkdir()
    (root / "config" / "secret.json").write_text('{"key":"NEVER_EXPORT_THIS"}', encoding="utf-8")
    home.mkdir(exist_ok=True)
    (home / "config.toml").write_text('[plugins."sample@local"]\nenabled = true\n', encoding="utf-8")
    return root


def config(folder):
    return SimpleNamespace(product="Sample", knowledge_source=SimpleNamespace(dir=str(folder), skill_sync=SimpleNamespace(enabled=True, plugin="sample@local", skill="qa")))


def test_export_latest_numeric_version_only_md_and_hashes(tmp_path):
    home, folder = tmp_path / "codex", tmp_path / "knowledge"
    package(home, "0.3.9", "old")
    package(home, "0.3.10", "LATEST SKILL", "SPECIAL_CODE_NEVER_EXPORT")
    result = skill_knowledge.export_skill(config(folder), codex_home=home)
    text = Path(result["path"]).read_text(encoding="utf-8")
    assert "LATEST SKILL" in text and "full reference" in text
    assert "NEVER_EXPORT_THIS" not in text and "SPECIAL_CODE_NEVER_EXPORT" not in text
    meta = skill_knowledge.parse_bundle(text)
    assert meta["version"] == "0.3.10" and meta["code_hash"]
    before = Path(result["path"]).stat().st_mtime_ns
    assert skill_knowledge.export_skill(config(folder), codex_home=home)["changed"] is False
    assert Path(result["path"]).stat().st_mtime_ns == before


def test_md_and_code_changes_are_separate_and_both_detected(tmp_path):
    home, folder = tmp_path / "codex", tmp_path / "knowledge"
    root = package(home, "1.0")
    first = skill_knowledge.export_skill(config(folder), codex_home=home)
    (root / "skills/qa/references/qa.md").write_text("NEW REFERENCE", encoding="utf-8")
    second = skill_knowledge.export_skill(config(folder), codex_home=home)
    assert first["content_hash"] != second["content_hash"]
    assert first["code_hash"] == second["code_hash"]
    (root / "skills/qa/scripts/search.py").write_text("NEW TOOL CODE", encoding="utf-8")
    third = skill_knowledge.export_skill(config(folder), codex_home=home)
    assert second["content_hash"] == third["content_hash"] and second["code_hash"] != third["code_hash"]


def test_incomplete_latest_or_disabled_does_not_replace_last_export(tmp_path):
    home, folder = tmp_path / "codex", tmp_path / "knowledge"
    package(home, "1.0")
    original = Path(skill_knowledge.export_skill(config(folder), codex_home=home)["path"]).read_bytes()
    newer = package(home, "1.1")
    (newer / "skills/qa/SKILL.md").unlink()
    with pytest.raises(ValueError):
        skill_knowledge.export_skill(config(folder), codex_home=home)
    assert (folder / "[Skill 지침] Sample.md").read_bytes() == original
    (home / "config.toml").write_text('[plugins."sample@local"]\nenabled = false\n', encoding="utf-8")
    with pytest.raises(ValueError):
        skill_knowledge.export_skill(config(folder), codex_home=home)


def test_non_object_plugin_manifest_preserves_last_export(tmp_path):
    home, folder = tmp_path / "codex", tmp_path / "knowledge"
    package(home, "1.0")
    original = Path(skill_knowledge.export_skill(config(folder), codex_home=home)["path"]).read_bytes()
    root = package(home, "1.1")
    (root / ".codex-plugin/plugin.json").write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        skill_knowledge.export_skill(config(folder), codex_home=home)
    assert (folder / "[Skill 지침] Sample.md").read_bytes() == original


def test_corrupt_bundle_detected():
    with pytest.raises(ValueError):
        skill_knowledge.parse_bundle("plain unofficial file")


@pytest.mark.parametrize("header", ["[]", "null", '"invalid"'])
def test_non_object_metadata_is_a_reported_format_error(header):
    with pytest.raises(ValueError):
        skill_knowledge.parse_bundle(skill_knowledge.PREFIX + header + " -->\nbody")


def test_missing_backtick_reference_preserves_previous_export(tmp_path):
    home, folder = tmp_path / "codex", tmp_path / "knowledge"
    package(home, "1.0")
    previous = Path(skill_knowledge.export_skill(config(folder), codex_home=home)["path"]).read_bytes()
    root = package(home, "1.1", "Read `references/qa.md` before answering.")
    (root / "skills/qa/references/qa.md").unlink()
    with pytest.raises(ValueError):
        skill_knowledge.export_skill(config(folder), codex_home=home)
    assert (folder / "[Skill 지침] Sample.md").read_bytes() == previous


def test_windows_crlf_sources_roundtrip_as_canonical_export(tmp_path):
    home, folder = tmp_path / "codex", tmp_path / "knowledge"
    root = package(home, "1.0")
    (root / "skills/qa/SKILL.md").write_bytes(b"# Skill\r\nLine one\r\nLine two\r\n")
    (root / "skills/qa/references/qa.md").write_bytes(b"# Reference\r\nCondition\r\n")
    exported = skill_knowledge.export_skill(config(folder), codex_home=home)
    text = Path(exported["path"]).read_text(encoding="utf-8")
    assert skill_knowledge.parse_bundle(text)["content_hash"] == exported["content_hash"]
    assert skill_knowledge.export_skill(config(folder), codex_home=home)["changed"] is False


def test_export_rejects_path_escape_and_hand_edited_generated_file(tmp_path):
    home, folder = tmp_path / "codex", tmp_path / "knowledge"
    package(home, "1.0")
    cfg = config(folder)
    cfg.knowledge_source.skill_sync.skill = "../outside"
    with pytest.raises(ValueError):
        skill_knowledge.export_skill(cfg, codex_home=home)
    cfg.knowledge_source.skill_sync.skill = "qa"
    folder.mkdir()
    (folder / "[Skill 지침] Sample.md").write_text("user file", encoding="utf-8")
    with pytest.raises(ValueError):
        skill_knowledge.export_skill(cfg, codex_home=home)
    assert (folder / "[Skill 지침] Sample.md").read_text(encoding="utf-8") == "user file"

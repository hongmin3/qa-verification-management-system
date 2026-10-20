"""Validates: REQ-VXVUECHATBOT-015. Temporary homes and synthetic server responses only."""
import io
import json
import zipfile

import pytest

from scripts import sync_vxvue_local_skill as sync

MARKER = "<!-- vxvue-ai-managed: test -->"


def skill(body="new rules"):
    return f"---\nname: vxvue-ai\ndescription: test\n---\n{MARKER}\n\n# VXvue AI\n{body}\n".encode("utf-8")


def zipped(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return output.getvalue()


@pytest.fixture
def homes(tmp_path):
    claude, codex, state = tmp_path / ".claude", tmp_path / ".codex", tmp_path / ".vxvue-ai"
    plugin = codex / "local-marketplaces/vxvue-server-local/plugins/vxvue-server-ai"
    cache = codex / "plugins/cache/vxvue-server-local/vxvue-server-ai"
    paths = {
        "claude": claude / "skills/vxvue-ai/SKILL.md",
        "codex": codex / "skills/vxvue-ai/SKILL.md",
        "codex-plugin": plugin / "skills/vxvue-ai/SKILL.md",
        "codex-cache": cache / "1.10.0/skills/vxvue-ai/SKILL.md",
    }
    for path in paths.values():
        path.parent.mkdir(parents=True)
        path.write_bytes(skill("old rules").replace(b"\n", b"\r\n"))
    older = cache / "1.9.0/skills/vxvue-ai/SKILL.md"
    older.parent.mkdir(parents=True)
    older.write_bytes(skill("old rules"))
    return {"claude": claude, "codex": codex, "state": state, "paths": paths, "older": older}


def run(homes, data, **kw):
    return sync.sync(data, claude_dir=homes["claude"], codex_dir=homes["codex"], state_dir=homes["state"], **kw)


def test_updates_all_four_copies_and_latest_numeric_cache(homes):
    result = run(homes, skill())
    assert sorted(result["updated"]) == ["claude", "codex", "codex-cache", "codex-plugin"]
    for path in homes["paths"].values():
        assert path.read_bytes() == skill()
    assert homes["older"].read_bytes() == skill("old rules")
    assert len(list((homes["state"] / "backups").iterdir())) == 4
    assert run(homes, skill())["same"] == ["claude", "codex", "codex-plugin", "codex-cache"]


def test_line_endings_alone_are_not_a_change(homes):
    result = run(homes, skill("old rules"))
    assert result["updated"] == [] and len(result["same"]) == 4
    assert homes["paths"]["claude"].read_bytes().count(b"\r\n") > 0


def test_unmanaged_skill_and_missing_copy_are_left_alone(homes):
    homes["paths"]["claude"].write_text("---\nname: vxvue-ai\n---\nmy own skill\n", encoding="utf-8")
    homes["paths"]["codex"].unlink()
    result = run(homes, skill())
    assert homes["paths"]["claude"].read_text(encoding="utf-8").endswith("my own skill\n")
    assert not homes["paths"]["codex"].exists()
    reasons = dict(result["skipped"])
    assert reasons["claude"] == "관리 표시 없음" and reasons["codex"] == "사본 없음"


def test_local_edit_after_sync_is_protected_until_forced(homes):
    run(homes, skill("v1"))
    homes["paths"]["claude"].write_bytes(skill("edited on this PC"))
    result = run(homes, skill("v2"))
    assert result["local_edits"] == ["claude"]
    assert homes["paths"]["claude"].read_bytes() == skill("edited on this PC")
    assert homes["paths"]["codex"].read_bytes() == skill("v2")
    assert "claude" in run(homes, skill("v2"), force=True)["updated"]
    assert homes["paths"]["claude"].read_bytes() == skill("v2")


@pytest.mark.parametrize("data", [
    zipped({"other/SKILL.md": skill()}),
    zipped({"vxvue-ai/SKILL.md": b"---\nname: vxvue-ai\n---\nno marker\n"}),
    zipped({"vxvue-ai/SKILL.md": skill().replace(b"name: vxvue-ai", b"name: other")}),
    b"not a zip",
])
def test_invalid_server_zip_changes_nothing(homes, data):
    with pytest.raises(ValueError):
        sync.skill_from_zip(data)
    assert homes["paths"]["claude"].read_bytes() == skill("old rules").replace(b"\n", b"\r\n")


def test_server_url_prefers_option_then_registered_connection(homes):
    (homes["claude"].parent / ".claude.json").write_text(json.dumps({"mcpServers": {"vxvue-ai": {
        "type": "http", "url": "http://10.0.0.5:8000/vxvue-desktop/mcp", "headers": {"Authorization": "Bearer secret"}}}}),
        encoding="utf-8")
    assert sync.server_url("http://a:1/", claude_dir=homes["claude"], codex_dir=homes["codex"], env={}) == "http://a:1"
    assert sync.server_url(None, claude_dir=homes["claude"], codex_dir=homes["codex"], env={}) == "http://10.0.0.5:8000"
    (homes["claude"].parent / ".claude.json").unlink()
    plugin = homes["codex"] / "local-marketplaces/vxvue-server-local/plugins/vxvue-server-ai/.mcp.json"
    plugin.write_text(json.dumps({"mcpServers": {"vxvue-ai": {"env": {"VXVUE_SERVER_URL": "http://10.0.0.6:9"}}}}), encoding="utf-8")
    assert sync.server_url(None, claude_dir=homes["claude"], codex_dir=homes["codex"], env={}) == "http://10.0.0.6:9"


def test_hook_is_silent_when_nothing_changed_and_backs_off_after_failure(homes, monkeypatch, capsys):
    monkeypatch.setattr(sync, "fetch", lambda server, timeout: zipped({"vxvue-ai/SKILL.md": skill("old rules")}))
    args = ["--hook", "codex", "--server", "http://s:1", "--claude-dir", str(homes["claude"]),
            "--codex-dir", str(homes["codex"]), "--state-dir", str(homes["state"])]
    assert sync.main(args) == 0 and capsys.readouterr().out == ""

    monkeypatch.setattr(sync, "fetch", lambda server, timeout: zipped({"vxvue-ai/SKILL.md": skill("v2")}))
    assert sync.main(args) == 0
    message = json.loads(capsys.readouterr().out)
    assert "4개" in message["systemMessage"]
    assert message["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    calls = []
    def offline(server, timeout):
        calls.append(server)
        raise OSError("down")
    monkeypatch.setattr(sync, "fetch", offline)
    assert sync.main(args) == 0
    assert json.loads((homes["state"] / "skill-sync-state.json").read_text(encoding="utf-8"))["last_hook"]["outcome"] \
        .startswith("server unavailable")
    assert sync.main(args) == 0
    assert len(calls) == 1 and capsys.readouterr().out == ""
    last = json.loads((homes["state"] / "skill-sync-state.json").read_text(encoding="utf-8"))["last_hook"]
    assert last["cli"] == "codex" and last["outcome"] == "waiting after failure"


def test_install_hooks_is_repeatable_and_keeps_other_entries(homes):
    settings, hooks = homes["claude"] / "settings.json", homes["codex"] / "hooks.json"
    other = {"type": "command", "command": "node other.js", "timeout": 5}
    settings.write_text(json.dumps({"model": "opus", "hooks": {"SessionStart": [{"hooks": [other]}]}}), encoding="utf-8")
    for _ in range(2):
        sync.install_hooks(claude_dir=homes["claude"], codex_dir=homes["codex"], python="C:/Python/python.exe")
    claude = json.loads(settings.read_text(encoding="utf-8"))
    codex = json.loads(hooks.read_text(encoding="utf-8"))
    assert claude["model"] == "opus"
    commands = [h["command"] for g in claude["hooks"]["SessionStart"] for h in g["hooks"]]
    assert commands.count("node other.js") == 1 and sum(sync.HOOK_TAG in c for c in commands) == 1
    assert sync.HOOK_TAG in commands[0]  # first, so an installer that re-appends its own group keeps positions
    entry = [h for g in codex["hooks"]["SessionStart"] for h in g["hooks"]][0]
    assert sync.HOOK_TAG in entry["command"] and entry["command"].startswith('"C:/Python/python.exe"')
    assert entry["commandWindows"].startswith("C:/Python/python.exe -X utf8 -c ")

    sync.install_hooks(claude_dir=homes["claude"], codex_dir=homes["codex"], python="python", remove=True)
    claude = json.loads(settings.read_text(encoding="utf-8"))
    assert [h["command"] for g in claude["hooks"]["SessionStart"] for h in g["hooks"]] == ["node other.js"]
    assert "SessionStart" not in json.loads(hooks.read_text(encoding="utf-8")).get("hooks", {})


def test_windows_hook_command_never_starts_with_a_quote():
    assert sync.hook_command("C:/Program Files/Python/python.exe", "codex", windows=True).startswith("python -X utf8")
    assert not sync.hook_command("C:/Python/python.exe", "codex", windows=True).startswith('"')


@pytest.mark.parametrize("windows", [False, True])
def test_hook_command_runs_the_script_with_its_arguments(homes, windows):
    command = sync.hook_command("C:/Python/python.exe", "claude", windows=windows)
    code = command.split(" -c ", 1)[1].strip('"')
    captured = {}
    import runpy
    import sys
    original, argv = runpy.run_path, list(sys.argv)
    runpy.run_path = lambda path, run_name: captured.update(path=path, argv=list(sys.argv))
    try:
        exec(code, {"__name__": "probe"})
    finally:
        runpy.run_path, sys.argv = original, argv
    assert captured["path"].endswith("sync_vxvue_local_skill.py")
    assert captured["argv"][1:] == ["--hook", "claude"]

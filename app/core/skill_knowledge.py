"""Export installed Skill instructions only; executable files remain local."""
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
import tomllib

PREFIX = "<!-- qa-skill-export:v1 "


def digest(value):
    return hashlib.sha256(value).hexdigest()


def component(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]+", value) or value in (".", ".."):
        raise ValueError("Skill 식별자와 경로 설정을 확인해주세요.")
    return value


def installed_skill(plugin, skill, codex_home=None):
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser().resolve()
    try:
        name, marketplace = plugin.split("@")
        component(name); component(marketplace); component(skill)
        settings = tomllib.loads((home / "config.toml").read_text(encoding="utf-8-sig"))
        plugins = settings.get("plugins", {})
        entry = plugins.get(plugin) if isinstance(plugins, dict) else None
        if not isinstance(entry, dict) or entry.get("enabled") is not True:
            raise ValueError("설정된 로컬 플러그인이 활성 상태가 아닙니다.")
        cache = home / "plugins/cache" / marketplace / name
        candidates = [(tuple(map(int, p.name.split("."))), p) for p in cache.iterdir()
                      if p.is_dir() and re.fullmatch(r"\d+(?:\.\d+)*", p.name)]
        if not candidates:
            raise ValueError("로컬 플러그인 설치 캐시를 찾지 못했습니다.")
        package = max(candidates, key=lambda item: item[0])[1]
        if package.is_symlink() or (hasattr(package, "is_junction") and package.is_junction()) or not package.resolve().is_relative_to(cache.resolve()):
            raise ValueError("설치 캐시 밖의 플러그인 연결은 동기화할 수 없습니다.")
        manifest = json.loads((package / ".codex-plugin/plugin.json").read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict) or manifest.get("name") != name or manifest.get("version") != package.name:
            raise ValueError("최신 플러그인 설치본의 이름·버전이 맞지 않습니다.")
        source = package / "skills" / skill
        if not source.resolve().is_relative_to(package.resolve()) or not (source / "SKILL.md").is_file():
            raise ValueError("최신 플러그인에서 Skill 지침을 읽지 못했습니다.")
        return package, source, package.name
    except (OSError, KeyError, tomllib.TOMLDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("로컬 Skill 설치 상태를 읽지 못했습니다.") from exc


def read_bundle(plugin, skill, codex_home=None):
    package, source, version = installed_skill(plugin, skill, codex_home)
    references = source / "references"
    paths = [source / "SKILL.md"]
    if references.exists():
        paths += sorted(p for p in references.rglob("*") if p.is_file() and p.suffix.lower() == ".md")
    # Directory links must not silently skip references or extend the selected package.
    for path in [source, *source.rglob("*")]:
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()) or not path.resolve().is_relative_to(package.resolve()):
            raise ValueError("Skill 안의 연결 경로는 자동 동기화할 수 없습니다.")
    documents, records = [], []
    for path in paths:
        data = path.read_bytes()
        if len(data) > 4 * 1024 * 1024:
            raise ValueError("Skill 참조 MD가 자동 동기화 크기 제한을 넘었습니다.")
        text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            raise ValueError("최신 Skill 지침 또는 참조 MD가 비어 있습니다.")
        for linked in re.findall(r"references[/\\][^`\n\"'()<>\[\]]+?\.md", text, re.I):
            linked = linked.replace("\\", "/")
            if not (source / linked).is_file() or not (source / linked).resolve().is_relative_to(references.resolve()):
                raise ValueError("Skill 지침이 참조하는 MD를 읽지 못했습니다.")
        relative = path.relative_to(source).as_posix()
        records.append({"path": relative, "sha256": digest(data)})
        documents.append(f"\n\n## {relative}\n{text}")
    body = "# 로컬 Skill 지침 자동 동기화본\n" + "".join(documents)
    code_paths = sorted(p for p in (source / "scripts").rglob("*") if p.is_file()) if (source / "scripts").is_dir() else []
    code_paths = [p for p in code_paths if p.suffix in (".py", ".js", ".ps1", ".sh")]
    code_paths += [package / name for name in (".mcp.json", "mcp.json") if (package / name).is_file()]
    # Server connector entry points live at the package root rather than skills/scripts.
    code_paths += sorted(p for p in package.iterdir() if p.is_file() and p.suffix in (".cjs", ".mjs", ".js", ".py", ".ps1", ".sh"))
    if any(p.is_symlink() or not p.resolve().is_relative_to(package.resolve()) for p in code_paths):
        raise ValueError("설치 캐시 밖의 실행 파일은 동기화할 수 없습니다.")
    code_hashes = {p.relative_to(package).as_posix(): digest(p.read_bytes()) for p in code_paths}
    if any(digest(path.read_bytes()) != record["sha256"] for path, record in zip(paths, records)) or any(digest(path.read_bytes()) != code_hashes[path.relative_to(package).as_posix()] for path in code_paths):
        raise ValueError("Skill 내보내기 중 원본이 바뀌었습니다. 다음 동기화에서 다시 확인합니다.")
    metadata = {"schema": 1, "plugin": plugin, "skill": skill, "version": version, "files": records,
                "content_hash": digest(body.encode("utf-8")), "code_hash": digest(json.dumps(code_hashes, sort_keys=True, separators=(",", ":")).encode())}
    return metadata, body


def parse_bundle(text):
    line, separator, body = text.partition("\n")
    try:
        if not separator or not line.startswith(PREFIX) or not line.endswith(" -->"):
            raise ValueError()
        metadata = json.loads(line[len(PREFIX):-4])
        if not isinstance(metadata, dict):
            raise ValueError()
        if metadata.get("schema") != 1 or metadata["content_hash"] != digest(body.encode("utf-8")):
            raise ValueError()
        if not re.fullmatch(r"[0-9a-f]{64}", metadata["code_hash"]):
            raise ValueError()
        for key in ("plugin", "skill", "version"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                raise ValueError()
        return metadata
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("자동 생성 Skill 문서의 형식이나 해시가 맞지 않습니다.") from exc


def export_skill(config, *, codex_home=None):
    profile = config.knowledge_source.skill_sync
    if not profile.enabled:
        return {"changed": False, "enabled": False}
    metadata, body = read_bundle(profile.plugin, profile.skill, codex_home)
    if any(c in config.product for c in ("/", "\\", "\0")) or not config.product.strip():
        raise ValueError("제품 이름과 저장 경로 설정을 확인해주세요.")
    folder = Path(config.knowledge_source.dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"[Skill 지침] {config.product}.md"
    contents = PREFIX + json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + " -->\n" + body
    if target.exists():
        previous = target.read_bytes().decode("utf-8")
        parse_bundle(previous)  # never overwrite an unrelated hand-authored file
        if previous == contents:
            return {**metadata, "path": str(target), "changed": False, "enabled": True}
    with tempfile.NamedTemporaryFile(dir=folder, suffix=".tmp", delete=False) as handle:
        temp = Path(handle.name)
        handle.write(contents.encode("utf-8"))
    try:
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
    return {**metadata, "path": str(target), "changed": True, "enabled": True}

"""Build the ALM workflow Skill with a deployment-specific catalog address."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import zipfile

VERSION = "1.1.1"
SOURCE = Path(__file__).resolve().parents[1] / "deploy/alm-skill/alm"
FILES = ("SKILL.md", "agents/openai.yaml", "scripts/knowledge.py", "references/browser-operations.md")


def build_skill(server_url, output):
    if any(character.isspace() for character in server_url):
        raise ValueError("서버 주소에 공백이나 줄바꿈을 넣지 마세요.")
    value = urlsplit(server_url)
    if value.port is not None and not 1 <= value.port <= 65535:
        raise ValueError("서버 포트 범위를 확인하세요.")
    if (value.scheme not in ("http", "https") or not value.hostname
            or value.username is not None or value.password is not None
            or value.query or value.fragment or value.path not in ("", "/")):
        raise ValueError("인증정보가 없는 QA 서버의 기본 주소를 지정하세요.")
    output = Path(output)
    if output.exists():
        raise FileExistsError("기존 출력 파일을 덮어쓰지 않습니다.")
    for name in FILES:
        if not (SOURCE / name).is_file():
            raise ValueError("ALM 스킬 원본 파일이 없습니다: " + name)
    output.parent.mkdir(parents=True, exist_ok=True)
    config = {"catalog_url": server_url.rstrip("/") + "/static/alm-guide/current.json", "skill_version": VERSION}
    with zipfile.ZipFile(output, "x", zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(SOURCE / name, "alm/" + name)
        archive.writestr("alm/server.json", json.dumps(config, ensure_ascii=False, indent=2))
    body = output.read_bytes()
    return {"skill_version": VERSION, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="QA 서버 주소를 넣은 ALM Skill ZIP 생성")
    parser.add_argument("--server", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_skill(args.server, args.output), ensure_ascii=False))

"""Build a deterministic MCPB containing only the manifest and stdio bridge."""
import argparse
import io
import json
from pathlib import Path
import zipfile
from urllib.parse import urlsplit


def bundle_bytes(server_url=None):
    source = Path(__file__).resolve().parents[1] / "deploy/claude-desktop"
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    if server_url:
        parts = urlsplit(server_url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("서버 주소 형식을 확인하세요.")
        manifest["user_config"]["server_url"]["default"] = server_url.rstrip("/")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")),
                              ("server.cjs", (source / "server.cjs").read_bytes())):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 15, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


def skill_bytes():
    source = Path(__file__).resolve().parents[1] / "deploy/claude-desktop/skill/vxvue-ai/SKILL.md"
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("vxvue-ai/SKILL.md", date_time=(2026, 10, 15, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, source.read_bytes())
    return output.getvalue()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--server-url")
    parser.add_argument("--skill-output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(bundle_bytes(args.server_url))
    print(f"배포 파일 생성: {args.output.resolve()}")
    if args.skill_output:
        args.skill_output.parent.mkdir(parents=True, exist_ok=True)
        args.skill_output.write_bytes(skill_bytes())
        print(f"Skill 파일 생성: {args.skill_output.resolve()}")

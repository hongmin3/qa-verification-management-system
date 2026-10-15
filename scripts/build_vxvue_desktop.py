"""Build the VXvue AI desktop bundle, the Claude Code installer and the managed Skill ZIP.

The server calls these per download with its own address and a fresh one-time ticket (REQ-VXVUECHATBOT-013).
No long-lived key is ever written into a bundle or script.
"""
import argparse
import base64
import io
import json
from pathlib import Path
import re
import zipfile
from urllib.parse import urlsplit

SOURCE = Path(__file__).resolve().parents[1] / "deploy/claude-desktop"
STAMP = (2026, 10, 15, 0, 0, 0)


def _server(server_url):
    parts = urlsplit(server_url or "")
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment \
            or not re.fullmatch(r"[A-Za-z0-9.:/_\-\[\]%]+", server_url):
        raise ValueError("서버 주소 형식을 확인하세요.")
    return server_url.rstrip("/")


def _ticket(ticket):
    if not isinstance(ticket, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", ticket):
        raise ValueError("등록 번호 형식을 확인하세요.")
    return ticket


def _zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(name, date_time=STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content)
    return output.getvalue()


def bundle_bytes(server_url, ticket, expires_at=""):
    """The .mcpb: manifest with the server address, the stdio bridge, and the one-time enrollment ticket."""
    server = _server(server_url)
    manifest = json.loads((SOURCE / "manifest.json").read_text(encoding="utf-8"))
    manifest["server"]["mcp_config"]["env"]["VXVUE_SERVER_URL"] = server
    enrollment = {"server_url": server, "ticket": _ticket(ticket), "expires_at": expires_at}
    return _zip((("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")),
                 ("server.cjs", (SOURCE / "server.cjs").read_bytes()),
                 ("enrollment.json", json.dumps(enrollment, ensure_ascii=False, indent=2).encode("utf-8"))))


def skill_text():
    return (SOURCE / "skill/vxvue-ai/SKILL.md").read_bytes().replace(b"\r\n", b"\n")


def skill_bytes():
    return _zip((("vxvue-ai/SKILL.md", skill_text()),))


def installer_text(server_url, ticket):
    from app.modules.vxvue_chatbot.desktop import BRIDGE_VERSION
    template = (SOURCE / "install-claude-code.ps1").read_text(encoding="utf-8")
    return (template.replace("__SERVER__", _server(server_url)).replace("__TICKET__", _ticket(ticket))
            .replace("__SKILL_BASE64__", base64.b64encode(skill_text()).decode("ascii"))
            .replace("__VERSION__", BRIDGE_VERSION))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write the managed Skill ZIP. Bundles need a server-issued ticket; download them from /vxvue-desktop.")
    parser.add_argument("--skill-output", type=Path, required=True)
    args = parser.parse_args()
    args.skill_output.parent.mkdir(parents=True, exist_ok=True)
    args.skill_output.write_bytes(skill_bytes())
    print(f"Skill 파일 생성: {args.skill_output.resolve()}")

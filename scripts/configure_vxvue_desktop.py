"""Initialize the team access token without printing or replacing existing secrets."""
import argparse
import os
from pathlib import Path
import secrets


def initialize(root):
    path = Path(root) / "data/vxvue_desktop/access-token.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        if len(path.read_text(encoding="utf-8").strip()) < 32:
            raise ValueError("기존 팀 토큰의 형식을 확인하세요. 자동으로 덮어쓰지 않습니다.")
        return path
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secrets.token_urlsafe(48) + "\n")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    args = parser.parse_args()
    print(f"팀 접근 토큰 파일: {initialize(args.project_root).resolve()}")
    print("토큰 값은 출력하지 않았습니다. 관리자만 안전하게 열어 팀원에게 별도로 전달하세요.")

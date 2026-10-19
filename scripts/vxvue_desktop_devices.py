"""List or revoke PCs connected to VXvue AI (REQ-VXVUECHATBOT-013). Keys are never printed; only hashes are stored."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.storage import Storage  # noqa: E402

COLUMNS = ("id", "client", "hostname", "username", "address", "bridge_version", "status", "created_at", "last_used_at")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="등록된 PC 목록")
    revoke = sub.add_parser("revoke", help="PC 하나의 연결 키 취소")
    revoke.add_argument("device_id")
    args = parser.parse_args(argv)
    storage = Storage(args.project_root / "data/app.db")
    if args.command == "list":
        devices = storage.vxdesk_devices()
        print("\t".join(COLUMNS))
        for device in devices:
            print("\t".join(str(device.get(c) or "") for c in COLUMNS))
        print(f"등록된 PC {len(devices)}대 (취소 {sum(d['status'] == 'revoked' for d in devices)}대)")
        return 0
    if storage.vxdesk_revoke(args.device_id):
        print(f"취소했습니다: {args.device_id}. 이 PC는 다음 요청부터 연결이 거절됩니다.")
        return 0
    print(f"취소할 활성 PC를 찾지 못했습니다: {args.device_id}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

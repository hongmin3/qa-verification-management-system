"""Zero-input connection for the desktop/CLI bridge (REQ-VXVUECHATBOT-013).

A download mints a one-time ticket; the bridge trades it for a per-device key on first run.
Only hashes are stored. Access is limited to configured networks and each key can be revoked.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import ipaddress
import secrets
import threading

from app.core.storage import Storage

DEFAULT_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "::1/128")
_storages = {}
_lock = threading.Lock()


class AccessError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def setting(settings, key, default):
    getter = getattr(settings, "get", None)
    value = getter(key, default) if callable(getter) else default
    return default if value is None else value


def storage(settings):
    path = settings.root / "data/app.db"
    with _lock:
        if path not in _storages:
            _storages[path] = Storage(path)
        return _storages[path]


def reset():
    with _lock:
        _storages.clear()


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def client_address(request):
    """The peer address; a forwarded address is trusted only when the peer is the local proxy."""
    peer = request.client.host if request.client else ""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    forwarded = request.headers.get("x-real-ip", "").strip()
    if peer_ip.is_loopback and forwarded:
        return forwarded
    return peer


def require_network(settings, request):
    address = client_address(request)
    try:
        ip = ipaddress.ip_address(address)
        allowed = any(ip in ipaddress.ip_network(n, strict=False) for n in setting(settings, "vxvue_desktop.allowed_networks", DEFAULT_NETWORKS))
    except ValueError:
        allowed = False
    if not allowed:
        raise AccessError(403, "network_not_allowed", f"이 접속 주소({address})는 VXvue AI 연결이 허용되지 않았습니다. 사내망 또는 VPN에서 다시 시도하세요.")
    return address


def mint_ticket(settings, request, purpose):
    address = require_network(settings, request)
    ticket = secrets.token_urlsafe(32)
    hours = int(setting(settings, "vxvue_desktop.ticket_hours", 168))
    expires = datetime.now(timezone.utc) + timedelta(hours=hours)
    storage(settings).vxdesk_add_ticket(digest(ticket), purpose, address, expires.isoformat())
    return ticket, expires


def _text(value, limit=100):
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else "unknown"


def redeem(settings, request, body):
    address = require_network(settings, request)
    ticket = body.get("ticket") if isinstance(body, dict) else None
    if not isinstance(ticket, str) or not 20 <= len(ticket) <= 200:
        raise AccessError(400, "ticket_invalid", "등록 번호 형식이 맞지 않습니다. 안내 화면에서 설치 파일을 다시 받으세요.")
    client = body.get("client") if body.get("client") in ("desktop", "claude-code") else "desktop"
    token = secrets.token_urlsafe(32)
    device = {"client": client, "hostname": _text(body.get("hostname")), "username": _text(body.get("username")),
              "address": address, "bridge_version": _text(body.get("bridge_version"), 20)}
    device_id, reason = storage(settings).vxdesk_redeem(digest(ticket), digest(token), device)
    messages = {
        "ticket_unknown": "등록 번호를 찾을 수 없습니다. 안내 화면에서 설치 파일을 새로 받으세요.",
        "ticket_used": "이 설치 파일의 등록 번호는 이미 사용됐습니다. 다른 PC에서 받은 파일이면 안내 화면에서 새로 받으세요.",
        "ticket_expired": "설치 파일의 등록 번호가 만료됐습니다. 안내 화면에서 새로 받으세요.",
    }
    if reason:
        raise AccessError(404 if reason == "ticket_unknown" else 410, reason, messages[reason])
    return {"device_id": device_id, "token": token}


def authenticate(settings, request, team_token=None):
    """Return the caller identity or raise AccessError. Keys are compared by hash only."""
    address = require_network(settings, request)
    header = request.headers.get("authorization", "")
    supplied = header[7:].strip() if header[:7].lower() == "bearer " else ""
    if not supplied:
        raise AccessError(401, "missing_credential", "연결 키가 없습니다. VXvue AI 확장을 안내 화면에서 받아 다시 설치하세요.")
    if team_token and setting(settings, "vxvue_desktop.accept_team_token", True) and \
            hmac.compare_digest(supplied.encode(), team_token.encode()):
        return {"kind": "team", "device_id": None}
    store = storage(settings)
    device = store.vxdesk_device_by_token(digest(supplied))
    if not device:
        raise AccessError(401, "unknown_credential", "등록되지 않은 연결 키입니다. 안내 화면에서 설치 파일을 새로 받아 설치하세요.")
    if device["status"] != "active":
        raise AccessError(401, "device_revoked", "이 PC의 연결이 관리자에 의해 취소됐습니다. 필요하면 관리자에게 문의한 뒤 다시 설치하세요.")
    last = datetime.fromisoformat(device["last_used_at"])
    now = datetime.now(timezone.utc)
    if now - last > timedelta(days=int(setting(settings, "vxvue_desktop.device_idle_days", 90))):
        raise AccessError(401, "device_expired", "오래 사용하지 않아 이 PC의 연결 키가 만료됐습니다. 안내 화면에서 설치 파일을 새로 받으세요.")
    version = request.headers.get("x-vxvue-bridge", "")[:20]
    if now - last > timedelta(minutes=5) or (version and version != device["bridge_version"]):
        store.vxdesk_touch(device["id"], address, version)
    return {"kind": "device", "device_id": device["id"]}

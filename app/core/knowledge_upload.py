"""지식 폴더를 담당자 PC 에서 운영 서버로 올려 받는 쪽.

**왜 마운트가 아니라 업로드인가.** 서버가 담당자 PC 의 폴더를 CIFS 로 마운트하는 방법도
있고 실제로 포트(445)는 열려 있다. 그런데 이 PC 는 Wi-Fi DHCP(측정 시 10.201.0.139)라 IP 가
바뀌고, 사내 DNS 에 이름이 없어 서버가 이름으로 찾지 못하며(`getent hosts` 실패), 외근 중엔
아예 네트워크에 없다. fstab 에 IP 를 박아두면 IP 가 바뀔 때마다 사람이 고쳐야 한다.

그래서 **폴더를 볼 수 있는 쪽이 밀어 올린다.** 이미 이 PC 에서 도는 ALM 크롤러 동기화
(`scripts/sync_vxvue_spec.py`)와 같은 방향이다. 서버는 마지막 업로드본을 계속 들고 있으므로
PC 가 꺼져 있어도 분석이 멈추지 않는다 — 마운트였다면 그 주 수집이 통째로 비었을 것이다.

프로토콜은 3단계다.

    ① GET  /knowledge/product-knowledge/state   서버가 이미 가진 것 (file_name + sha256)
    ② POST /knowledge/product-knowledge/asset   ①과 다른 파일만 하나씩
    ③ POST /knowledge/product-knowledge/commit  manifest 반영 → 문서 등록 → 사라진 파일 정리

①이 있어서 매주 전송량이 변경분으로 줄어든다 (초기 약 192MB, 이후엔 바뀐 문서 몇 개).

**분류는 PC 가 한다.** 파일명 규약을 판정하려면 폴더 전체를 봐야 하기 때문이다 — 같은 논리
문서의 여러 리비전 중 무엇이 최신인지는 이웃 파일을 봐야 알 수 있다(`_resolve_duplicates`).
서버는 PC 가 보낸 분류를 그대로 쓰되, **경로로 쓰이는 값은 전부 다시 검증한다** (③ 참고).
"""

from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.core.product_knowledge import (
    CLASSIFY_ORDER,
    DEFAULT_EXTENSIONS,
    KIND_UNKNOWN,
    _extract_text,
    _normalized_suffix,
    _sha256,
    load_manifest,
    manifest_path,
    normalize_file_name,
    product_dir,
    product_slug,
    register_collected,
    resolve_config,
)

#: 서버가 파일을 받아 둘 수 있는 종류. `unknown` 은 받지 않는다 — 분류되지 않은 파일은
#: 어차피 등록되지 않고 디스크만 차지한다.
ALLOWED_KINDS = frozenset(CLASSIFY_ORDER)

#: 한 파일 최대 크기. 실측 최대는 VXvue 매뉴얼 PDF 약 30MB 라 여유를 크게 둔다.
MAX_ASSET_BYTES = 200 * 1024 * 1024


class UploadRejected(ValueError):
    """업로드를 받을 수 없는 이유. 호출자가 그대로 400 으로 돌려준다."""


def safe_file_name(raw: str) -> str:
    """업로드된 이름이 **경로가 아닌 순수한 파일 이름인지** 확인한다.

    이 값은 곧바로 디스크 경로가 되므로 신뢰하면 안 된다. `../../etc/passwd`,
    `C:\\Windows\\x`, `a/b.pdf` 가 모두 들어올 수 있다.

    경로 조각을 **떼어내지 않고 거절한다.** 떼어내면 `../../../evil.pdf` 가 조용히
    `evil.pdf` 로 저장돼, 보낸 적 없는 파일이 수집 폴더에 생기고 운영자는 그것이 왜 거기
    있는지 설명할 수 없다. 정상 클라이언트는 애초에 순수 파일 이름만 보낸다 — 경로가 섞여
    있다면 공격이거나 보내는 쪽 버그이고, 둘 다 조용히 넘길 일이 아니다.

    Windows 와 POSIX 양쪽으로 해석해서 본다 — 서버는 Linux 지만 보내는 쪽이 Windows 라
    `PurePosixPath` 만으로는 역슬래시가 걸러지지 않는다.

    이름 정규화(NFC·mojibake 복구)는 수집 경로와 같은 함수를 쓴다 — 여기서 다른 규칙을
    쓰면 PC 가 만든 manifest 의 file_name 과 서버가 저장한 이름이 어긋난다.
    """
    candidate = unicodedata.normalize("NFC", (raw or "").strip())
    if not candidate:
        raise UploadRejected("파일 이름이 비어 있습니다.")
    bare = PurePosixPath(PureWindowsPath(candidate).name).name
    if bare != candidate:
        raise UploadRejected(f"파일 이름에 경로가 들어 있습니다: {raw!r}")
    if candidate in (".", ".."):
        raise UploadRejected(f"파일 이름이 아닙니다: {raw!r}")
    if candidate.startswith("."):
        raise UploadRejected(f"숨김 파일은 받지 않습니다: {candidate}")
    normalized, _note = normalize_file_name(candidate)
    if Path(normalized).suffix.lower() not in DEFAULT_EXTENSIONS:
        raise UploadRejected(f"수집 대상 형식이 아닙니다: {normalized} (허용: {', '.join(DEFAULT_EXTENSIONS)})")
    return normalized


def _require_product(product: str):
    config = resolve_config(product)
    if config is None:
        raise UploadRejected(f"config/products/ 에 '{product}' 설정이 없습니다. 서버에 제품 설정을 먼저 배포하세요.")
    return config


def server_state(product: str, root: Path | None = None) -> dict:
    """서버가 지금 가지고 있는 자산 목록. PC 가 이것과 비교해 **바뀐 것만** 올린다.

    manifest 가 아니라 **실제 파일**을 기준으로 답한다 — manifest 에는 있는데 파일이 없으면
    (전송 중 끊김 등) 다시 받아야 하기 때문이다.
    """
    config = _require_product(product)
    base = product_dir(config.product, root)
    assets = []
    for entry in load_manifest(config.product, root).get("assets", []):
        kind, file_name = entry.get("kind", ""), entry.get("file_name", "")
        if not kind or not file_name:
            continue
        path = base / "original" / kind / file_name
        if not path.is_file():
            continue
        assets.append({"file_name": file_name, "kind": kind, "sha256": entry.get("sha256", "")})
    return {"product": config.product, "slug": product_slug(config.product), "assets": assets}


def store_asset(product: str, kind: str, file_name: str, data: bytes, sha256: str = "", root: Path | None = None) -> dict:
    """원본 파일 하나를 받아 두고 정규화 텍스트를 뽑는다.

    정규화는 **서버가 직접** 한다 (PC 가 만든 것을 함께 올리지 않는다). 파서가 바뀌면 서버가
    가진 텍스트도 함께 바뀌어야 하는데, PC 가 보낸 텍스트를 쓰면 PC 쪽 코드 버전에 묶인다.
    """
    config = _require_product(product)
    if kind not in ALLOWED_KINDS:
        raise UploadRejected(f"알 수 없는 자산 종류입니다: {kind!r} (허용: {', '.join(sorted(ALLOWED_KINDS))})")
    if not data:
        raise UploadRejected("빈 파일입니다.")
    if len(data) > MAX_ASSET_BYTES:
        raise UploadRejected(f"파일이 너무 큽니다: {len(data):,}바이트 (상한 {MAX_ASSET_BYTES:,})")

    safe_name = safe_file_name(file_name)
    target = product_dir(config.product, root) / "original" / kind / safe_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    # 무결성 확인은 **쓴 뒤** 한다. 전송 중 잘린 파일이 디스크에 남아 다음 실행에서
    # "이미 있음"으로 건너뛰어지는 것이 가장 나쁘다 — 어긋나면 바로 지운다.
    digest = _sha256(target)
    if sha256 and digest != sha256:
        target.unlink(missing_ok=True)
        raise UploadRejected(f"전송된 내용이 손상됐습니다 (sha256 불일치): {safe_name}")

    record = {
        "file_name": safe_name,
        "kind": kind,
        "sha256": digest,
        "bytes": len(data),
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "normalized_path": "",
        "normalized_chars": 0,
    }
    try:
        text = _extract_text(target)
    except Exception as exc:
        # 텍스트 추출 실패가 업로드 실패는 아니다. 원본은 이미 서버에 있고 등록도 된다.
        # 검색에서 빠질 뿐이므로 그 사실을 기록해 commit 응답에 실어 보낸다.
        record["error"] = f"정규화 실패 {type(exc).__name__}: {exc}"
        return record

    base = product_dir(config.product, root)
    normalized_target = base / "normalized" / kind / f"{Path(safe_name).stem}{_normalized_suffix(Path(safe_name).suffix.lower())}"
    normalized_target.parent.mkdir(parents=True, exist_ok=True)
    normalized_target.write_text(text, encoding="utf-8")
    record["normalized_path"] = str(normalized_target.relative_to(base)).replace("\\", "/")
    record["normalized_chars"] = len(text)
    return record


def _prune_orphans(base: Path, keep: set[tuple[str, str]]) -> list[str]:
    """manifest 에 더 이상 없는 원본·정규화 파일을 지운다.

    지우지 않으면 사양서(260831)가 서버에 남아 계속 검색된다 — "어느 것이 최신인가"에
    답하는 것이 이 시스템의 존재 이유라 옛 리비전이 남는 것이 가장 나쁜 실패다.
    """
    removed: list[str] = []
    original_root = base / "original"
    if not original_root.is_dir():
        return removed
    keep_stems = {(kind, Path(name).stem) for kind, name in keep}
    for kind_dir in original_root.iterdir():
        if not kind_dir.is_dir():
            continue
        for path in kind_dir.iterdir():
            if path.is_file() and (kind_dir.name, path.name) not in keep:
                path.unlink(missing_ok=True)
                removed.append(f"{kind_dir.name}/{path.name}")
    normalized_root = base / "normalized"
    if normalized_root.is_dir():
        for kind_dir in normalized_root.iterdir():
            if not kind_dir.is_dir():
                continue
            for path in kind_dir.iterdir():
                if path.is_file() and (kind_dir.name, path.stem) not in keep_stems:
                    path.unlink(missing_ok=True)
    return removed


def commit(product: str, manifest: dict, uploaded: dict[str, dict] | None = None, storage=None, root: Path | None = None) -> dict:
    """PC 가 보낸 manifest 를 서버 상태로 확정하고 문서로 등록한다.

    PC 의 manifest 를 그대로 쓰지 않는다. 서버에 **실제로 파일이 있는 자산만** 남기고,
    `normalized_path` 같은 서버 고유 값은 서버가 만든 것으로 덮는다. PC 경로가 섞여 들어가면
    서버에서 열리지 않는 경로가 `documents` 에 등록된다.
    """
    config = _require_product(product)
    base = product_dir(config.product, root)
    uploaded = uploaded or {}

    kept: list[dict] = []
    missing: list[str] = []
    for entry in manifest.get("assets", []) or []:
        kind, raw_name = entry.get("kind", ""), entry.get("file_name", "")
        if kind not in ALLOWED_KINDS or not raw_name:
            continue
        try:
            file_name = safe_file_name(raw_name)
        except UploadRejected:
            continue
        if not (base / "original" / kind / file_name).is_file():
            missing.append(file_name)
            continue
        record = {key: value for key, value in entry.items() if key not in ("normalized_path", "normalized_chars", "source_path", "collected_at", "error")}
        record["file_name"] = file_name
        record["kind"] = kind
        record.update({key: value for key, value in (uploaded.get(file_name) or {}).items() if key in ("normalized_path", "normalized_chars", "collected_at", "error")})
        if not record.get("normalized_path"):
            # 이번에 올리지 않은(=서버가 이미 가진) 파일은 지난 manifest 의 값을 잇는다.
            previous = next((item for item in load_manifest(config.product, root).get("assets", []) if item.get("file_name") == file_name), {})
            record["normalized_path"] = previous.get("normalized_path", "")
            record["normalized_chars"] = previous.get("normalized_chars", 0)
            record.setdefault("collected_at", previous.get("collected_at", ""))
        kept.append(record)

    keep_keys = {(record["kind"], record["file_name"]) for record in kept}
    removed = _prune_orphans(base, keep_keys)

    payload = {
        "product": config.product,
        "slug": product_slug(config.product),
        "source_dir": manifest.get("source_dir", ""),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "uploaded_from": manifest.get("uploaded_from", ""),
        "counts": manifest.get("counts", {}),
        "assets": kept,
        "excluded": manifest.get("excluded", []),
    }
    path = manifest_path(config.product, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    registered = register_collected(config.product, storage=storage, root=root)
    failures = [record["error"] for record in kept if record.get("error")]
    detail = f"자산 {len(kept)}건 확정, 정리 {len(removed)}건 / {registered.detail}"
    if missing:
        detail += f" / 업로드 누락 {len(missing)}건: {', '.join(missing[:5])}"
    if failures:
        detail += f" / 정규화 실패 {len(failures)}건"
    return {
        "status": "PARTIAL" if (missing or failures) else "SUCCESS",
        "detail": detail,
        "assets": len(kept),
        "removed": removed,
        "missing": missing,
        "failures": failures,
        "registered": registered.detail,
        "duplicates": registered.duplicates,
    }


__all__ = [
    "ALLOWED_KINDS",
    "KIND_UNKNOWN",
    "MAX_ASSET_BYTES",
    "UploadRejected",
    "commit",
    "safe_file_name",
    "server_state",
    "store_asset",
]

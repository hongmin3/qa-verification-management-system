"""지식 폴더를 담당자 PC 에서 운영 서버로 밀어 올리는 쪽 (보내는 편).

받는 쪽은 `app/core/knowledge_upload.py` 다. 왜 마운트가 아니라 업로드인지도 거기에 적혀 있다.

**순서가 중요하다.** 먼저 로컬에 수집하고(`sync_product`), 그 결과 manifest 를 기준으로 올린다.
스캔 결과를 바로 올리지 않는 이유는 분류·중복 해소·리비전 판정이 전부 수집 단계에서 확정되기
때문이다 — 서버가 받는 것은 "폴더에 뭐가 있더라"가 아니라 "무엇을 쓰기로 했다"여야 한다.

**서버가 이미 가진 파일은 올리지 않는다.** 초기 1회는 약 192MB 지만 이후 주간 실행은 바뀐
문서 몇 개로 줄어든다. 판단 기준은 sha256 이다 — 수정 시각은 복사·동기화로 쉽게 바뀐다.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

from app.core.product_knowledge import (
    collected_path,
    load_manifest,
    product_slug,
    resolve_config,
    source_available,
    sync_product,
)

#: 큰 PDF 한 개를 사내망으로 올리는 데 걸리는 시간을 넉넉히 잡는다.
UPLOAD_TIMEOUT_SECONDS = 300


def _client(timeout: int):
    import httpx

    return httpx.Client(timeout=timeout)


def fetch_server_state(client, target_url: str, product: str) -> dict[tuple[str, str], str]:
    """서버가 가진 `(kind, file_name) → sha256`.

    조회에 실패하면 예외를 올린다 — 조용히 빈 값으로 넘어가면 전부 다시 올리게 되고,
    그것이 "왜 매주 3분씩 걸리지"의 원인이 되어도 아무 데도 남지 않는다.
    """
    response = client.get(f"{target_url.rstrip('/')}/knowledge/product-knowledge/state", params={"product": product})
    response.raise_for_status()
    payload = response.json()
    return {(asset["kind"], asset["file_name"]): asset.get("sha256", "") for asset in payload.get("assets", [])}


def push_product(product: str, target_url: str, *, dry_run: bool = False, timeout: int = UPLOAD_TIMEOUT_SECONDS) -> dict:
    """제품 하나의 지식 폴더를 수집해 서버로 올린다.

    반환값은 `sync_and_register` 와 같은 모양(`status`/`detail`)이라 CLI 가 두 경로를 같게
    다룰 수 있다.
    """
    config = resolve_config(product)
    if config is None:
        return {"status": "NEEDS_CONFIG", "detail": f"config/products/ 에 '{product}' 설정이 없습니다."}
    if not source_available(config):
        return {
            "status": "NEEDS_CONFIG",
            "detail": f"이 PC 에서 지식 폴더에 접근할 수 없습니다: {config.knowledge_source.dir or '(미설정)'}",
        }

    collected = sync_product(config, dry_run=dry_run)
    if collected.status in ("NEEDS_CONFIG", "FAILED"):
        return {"status": collected.status, "detail": collected.detail}

    manifest = load_manifest(config.product)
    assets = [asset for asset in manifest.get("assets", []) if not asset.get("error")]
    manifest_payload = {
        "source_dir": manifest.get("source_dir", ""),
        "counts": manifest.get("counts", {}),
        "uploaded_from": socket.gethostname(),
        # 서버는 자기 파일 경로를 스스로 만든다. PC 경로(`source_path`)를 보내면 서버에서
        # 열 수 없는 경로가 `documents` 에 등록될 수 있어 아예 뺀다.
        "assets": [{key: value for key, value in asset.items() if key not in ("source_path", "normalized_path", "normalized_chars")} for asset in assets],
        "excluded": manifest.get("excluded", []),
    }

    if dry_run:
        return {
            "status": "DRY_RUN",
            "detail": f"{collected.detail} / 업로드 대상 {len(assets)}건 (실제 전송하지 않음)",
            "uploaded": [],
            "skipped": [],
        }

    uploaded: dict[str, dict] = {}
    skipped: list[str] = []
    failed: list[str] = []
    sent_bytes = 0

    with _client(timeout) as client:
        have = fetch_server_state(client, target_url, config.product)
        for asset in assets:
            kind, file_name, digest = asset.get("kind", ""), asset.get("file_name", ""), asset.get("sha256", "")
            if have.get((kind, file_name)) == digest and digest:
                skipped.append(file_name)
                continue
            path = collected_path(config.product, asset)
            if not path.is_file():
                failed.append(f"{file_name} (수집본 없음)")
                continue
            try:
                with path.open("rb") as handle:
                    response = client.post(
                        f"{target_url.rstrip('/')}/knowledge/product-knowledge/asset",
                        data={"product": config.product, "kind": kind, "sha256": digest},
                        files={"file": (file_name, handle, "application/octet-stream")},
                    )
                response.raise_for_status()
            except Exception as exc:
                # 한 파일이 실패해도 나머지는 계속 올린다. 큰 PDF 하나 때문에 그 주 수집이
                # 통째로 비는 것이 가장 나쁘다.
                failed.append(f"{file_name} ({type(exc).__name__})")
                continue
            uploaded[file_name] = response.json()
            sent_bytes += path.stat().st_size

        commit = client.post(
            f"{target_url.rstrip('/')}/knowledge/product-knowledge/commit",
            data={
                "product": config.product,
                "manifest": json.dumps(manifest_payload, ensure_ascii=False),
                "uploaded": json.dumps(uploaded, ensure_ascii=False),
            },
        )
        commit.raise_for_status()
        result = commit.json()

    detail = (
        f"{collected.detail} / 업로드 {len(uploaded)}건({sent_bytes / 1_048_576:.1f}MB), "
        f"서버 보유분 건너뜀 {len(skipped)}건 / {result.get('detail', '')}"
    )
    if failed:
        detail += f" / 업로드 실패 {len(failed)}건: {', '.join(failed[:5])}"
    return {
        "status": "PARTIAL" if failed else result.get("status", "SUCCESS"),
        "detail": detail,
        "uploaded": sorted(uploaded),
        "skipped": skipped,
        "failed": failed,
        "duplicates": result.get("duplicates", []),
        "removed": result.get("removed", []),
    }


def target_slug(product: str) -> str:
    """서버에서 이 제품이 쓰는 폴더 이름. 문제 확인 시 경로를 안내하는 데 쓴다."""
    return product_slug(product)


def collected_root(product: str) -> Path:
    from app.core.product_knowledge import product_dir

    return product_dir(product)
